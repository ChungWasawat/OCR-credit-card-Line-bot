"""Task 13: offline OCR provider comparison — extract from a folder of real receipts
with every configured provider, then score against a human-verified ground truth.

Two subcommands:
  extract  runs provider(s) against every image in a folder, writes a long-format
           results CSV (one row per image x provider): every extracted field, latency,
           token usage, and an estimated cost.
  score    joins that results CSV against a ground-truth CSV (filename-keyed, filled in
           separately — see docs/ocr_model_comparison.md) and prints/writes field-level
           + is_receipt accuracy per provider.

Full walkthrough (gathering photos, filling ground truth, reading the summary, and
recording the final OCR_MODEL decision): docs/ocr_model_comparison.md

Usage (run as a module, not a direct script path — this file imports from app.*,
which is only importable when the repo root is on sys.path, which -m guarantees and
plain `python scripts/compare_models.py` does not):
  uv run python -m scripts.compare_models extract --images data/comparison
  uv run python -m scripts.compare_models extract --images data/comparison --models gemini typhoon_gemini
  uv run python -m scripts.compare_models score --results data/comparison/results.csv --truth data/comparison/ground_truth.csv
"""

from __future__ import annotations

import argparse
import csv
import difflib
import re
import time
from pathlib import Path
from types import SimpleNamespace

from dotenv import load_dotenv

load_dotenv()

from app.ocr.base import OcrContentError  # noqa: E402
from app.schema import ReceiptExtraction  # noqa: E402

ALL_MODELS = ["claude", "gemini", "typhoon", "typhoon_gemini"]
IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png"}

RESULTS_FIELDS = [
    "filename", "model", "status", "error", "latency_s",
    "is_receipt", "date", "merchant", "amount", "last4", "details", "quality_issue",
    "usage_detail", "input_tokens", "output_tokens", "est_cost_usd",
]

# $/MTok (input, output). None = pricing undocumented/unverified for this repo — the
# cost column is marked with a ">=" prefix (a lower bound) rather than fabricated.
PRICE_TABLE: dict[str, tuple[float, float] | None] = {
    "claude-haiku-4-5": (1.0, 5.0),
    "gemini-3.1-flash-lite": (0.0, 0.0),  # free tier
    "typhoon-ocr": None,
}


# --- SDK-call recording proxies (no changes to app/ — wraps the existing
# constructor-injection seam every provider already exposes for tests) --------------


def _wrap_anthropic(client, sink: list[tuple[str, int | None, int | None]]):
    def create(**kwargs):
        response = client.messages.create(**kwargs)
        usage = getattr(response, "usage", None)
        sink.append((
            kwargs.get("model"),
            getattr(usage, "input_tokens", None),
            getattr(usage, "output_tokens", None),
        ))
        return response

    return SimpleNamespace(messages=SimpleNamespace(create=create))


def _wrap_gemini(client, sink: list[tuple[str, int | None, int | None]]):
    def generate_content(**kwargs):
        response = client.models.generate_content(**kwargs)
        usage = getattr(response, "usage_metadata", None)
        sink.append((
            kwargs.get("model"),
            getattr(usage, "prompt_token_count", None),
            getattr(usage, "candidates_token_count", None),
        ))
        return response

    return SimpleNamespace(models=SimpleNamespace(generate_content=generate_content))


def _wrap_openai(client, sink: list[tuple[str, int | None, int | None]]):
    def create(**kwargs):
        response = client.chat.completions.create(**kwargs)
        usage = getattr(response, "usage", None)
        sink.append((
            kwargs.get("model"),
            getattr(usage, "prompt_tokens", None),
            getattr(usage, "completion_tokens", None),
        ))
        return response

    return SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))


def _build_provider(name: str, sink: list[tuple[str, int | None, int | None]]):
    if name == "claude":
        from app.ocr.claude import ClaudeOcr, default_claude_client

        return ClaudeOcr(client=_wrap_anthropic(default_claude_client(), sink))
    if name == "gemini":
        from app.ocr.gemini import GeminiOcr, default_gemini_client

        return GeminiOcr(client=_wrap_gemini(default_gemini_client(), sink))
    if name == "typhoon":
        from app.ocr.claude import default_claude_client
        from app.ocr.typhoon import TyphoonOcr, default_typhoon_client

        return TyphoonOcr(
            typhoon_client=_wrap_openai(default_typhoon_client(), sink),
            claude_client=_wrap_anthropic(default_claude_client(), sink),
        )
    if name == "typhoon_gemini":
        from app.ocr.gemini import default_gemini_client
        from app.ocr.typhoon import default_typhoon_client
        from app.ocr.typhoon_gemini import TyphoonGeminiOcr

        return TyphoonGeminiOcr(
            typhoon_client=_wrap_openai(default_typhoon_client(), sink),
            gemini_client=_wrap_gemini(default_gemini_client(), sink),
        )
    raise ValueError(f"unknown model {name!r}")


def _summarize_usage(calls: list[tuple[str, int | None, int | None]]) -> tuple[str, str, str, str]:
    """Renders one extract() call's SDK usage as (usage_detail, input_tokens,
    output_tokens, est_cost_usd) CSV cell strings. Missing/unpriced usage degrades to
    blank cells or a ">=" lower-bound cost prefix — never fabricated, never crashes.
    """
    if not calls:
        return "", "", "", ""
    parts, total_in, total_out, total_cost = [], 0, 0, 0.0
    any_missing = any_unpriced = False
    for model, in_tok, out_tok in calls:
        if in_tok is None or out_tok is None:
            parts.append(f"{model}:?/?")
            any_missing = True
            continue
        parts.append(f"{model}:{in_tok}/{out_tok}")
        total_in += in_tok
        total_out += out_tok
        price = PRICE_TABLE.get(model)
        if price is None:
            any_unpriced = True
            continue
        in_price, out_price = price
        total_cost += (in_tok / 1_000_000) * in_price + (out_tok / 1_000_000) * out_price
    usage_detail = "; ".join(parts)
    input_tokens = "" if any_missing else str(total_in)
    output_tokens = "" if any_missing else str(total_out)
    prefix = ">=" if (any_unpriced or any_missing) else ""
    return usage_detail, input_tokens, output_tokens, f"{prefix}{total_cost:.6f}"


# --- extract -------------------------------------------------------------------------


def cmd_extract(args: argparse.Namespace) -> None:
    images_dir = Path(args.images)
    image_paths = sorted(
        p for p in images_dir.iterdir() if p.is_file() and p.suffix.lower() in IMAGE_SUFFIXES
    )
    if not image_paths:
        raise SystemExit(f"no images found in {images_dir} (looked for {sorted(IMAGE_SUFFIXES)})")

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    write_header = not (args.append and out_path.exists())
    mode = "a" if args.append else "w"

    total_cells = len(image_paths) * len(args.models)
    cell_num = 0

    with out_path.open(mode, newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=RESULTS_FIELDS)
        if write_header:
            writer.writeheader()
            f.flush()

        for image_path in image_paths:
            image_bytes = image_path.read_bytes()
            for model_name in args.models:
                cell_num += 1
                sink: list[tuple[str, int | None, int | None]] = []
                provider = _build_provider(model_name, sink)
                row = {"filename": image_path.name, "model": model_name}
                t0 = time.perf_counter()
                try:
                    raw = provider.extract(image_bytes)
                    extraction = ReceiptExtraction.model_validate(raw)
                    latency = time.perf_counter() - t0
                    row.update({
                        "status": "ok",
                        "error": "",
                        "latency_s": f"{latency:.2f}",
                        "is_receipt": extraction.is_receipt,
                        "date": extraction.date.isoformat() if extraction.date else "",
                        "merchant": extraction.merchant or "",
                        "amount": "" if extraction.amount is None else extraction.amount,
                        "last4": extraction.last4 or "",
                        "details": extraction.details or "",
                        "quality_issue": extraction.quality_issue.value if extraction.quality_issue else "",
                    })
                    print(f"[{cell_num}/{total_cells}] {image_path.name} x {model_name} ... ok {latency:.1f}s")
                except OcrContentError as exc:
                    latency = time.perf_counter() - t0
                    row.update(_error_row(latency, "content_error", exc))
                    print(f"[{cell_num}/{total_cells}] {image_path.name} x {model_name} ... "
                          f"content_error {type(exc).__name__}: {exc}")
                except Exception as exc:  # noqa: BLE001 - batch must never abort on one cell
                    latency = time.perf_counter() - t0
                    row.update(_error_row(latency, "error", exc))
                    print(f"[{cell_num}/{total_cells}] {image_path.name} x {model_name} ... "
                          f"ERROR {type(exc).__name__}: {exc}")

                usage_detail, input_tokens, output_tokens, cost = _summarize_usage(sink)
                row["usage_detail"] = usage_detail
                row["input_tokens"] = input_tokens
                row["output_tokens"] = output_tokens
                row["est_cost_usd"] = cost

                writer.writerow(row)
                f.flush()

                if cell_num < total_cells:
                    time.sleep(args.sleep)

    print(f"Wrote {cell_num} rows to {out_path}")


def _error_row(latency: float, status: str, exc: Exception) -> dict:
    return {
        "status": status,
        "error": f"{type(exc).__name__}: {exc}",
        "latency_s": f"{latency:.2f}",
        "is_receipt": "", "date": "", "merchant": "", "amount": "",
        "last4": "", "details": "", "quality_issue": "",
    }


# --- score -----------------------------------------------------------------------
# Pure helpers below (no I/O) are the actual scoring rules — unit tested in
# tests/test_compare_models.py since they drive a real model-choice decision.


def normalize_merchant(s: str) -> str:
    s = re.sub(r"[^\w\s]", "", (s or "").strip().casefold())
    return re.sub(r"\s+", " ", s).strip()


def merchant_match(pred: str, truth: str) -> bool:
    p, t = normalize_merchant(pred), normalize_merchant(truth)
    if not p and not t:
        return True
    if not p or not t:
        return False
    if p == t or p in t or t in p:
        return True
    return difflib.SequenceMatcher(None, p, t).ratio() >= 0.8


def _parse_float_or_none(s: str) -> float | None:
    s = (s or "").strip()
    if not s:
        return None
    try:
        return float(s)
    except ValueError:
        return None


def amount_match(pred: str, truth: str) -> bool:
    p, t = _parse_float_or_none(pred), _parse_float_or_none(truth)
    if p is None and t is None:
        return True
    if p is None or t is None:
        return False
    return abs(p - t) <= 0.01


def date_match(pred: str, truth: str) -> bool:
    return (pred or "").strip() == (truth or "").strip()


def _to_bool(v: str | bool) -> bool:
    if isinstance(v, bool):
        return v
    return str(v).strip().lower() in {"true", "1", "yes"}


def is_receipt_match(pred: str, truth: str) -> bool:
    return _to_bool(pred) == _to_bool(truth)


def _read_csv_rows(path: Path) -> list[dict]:
    with path.open(newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def score_rows(results_rows: list[dict], truth_rows: list[dict]) -> tuple[dict, list[dict]]:
    """Pure scoring core: (summary keyed by model, mismatch list). Last row wins per
    key so a re-extracted/edited CSV doesn't need pre-deduping.
    """
    truth_by_filename: dict[str, dict] = {}
    for row in truth_rows:
        truth_by_filename[row["filename"]] = row

    results_by_key: dict[tuple[str, str], dict] = {}
    for row in results_rows:
        results_by_key[(row["filename"], row["model"])] = row

    models = sorted({row["model"] for row in results_rows})
    filenames = sorted(truth_by_filename)

    summary: dict[str, dict] = {}
    mismatches: list[dict] = []

    for model in models:
        n_ok = n_error = 0
        is_receipt_correct = is_receipt_total = 0
        date_correct = date_total = 0
        amount_correct = amount_total = 0
        merchant_correct = merchant_total = 0
        latencies: list[float] = []
        total_cost = 0.0
        cost_partial = False

        for filename in filenames:
            truth = truth_by_filename[filename]
            result = results_by_key.get((filename, model))
            if result is None:
                continue

            ok = result["status"] == "ok"
            n_ok += int(ok)
            n_error += int(not ok)

            is_receipt_total += 1
            pred_is_receipt = result.get("is_receipt", "") if ok else "False"
            if ok and is_receipt_match(pred_is_receipt, truth.get("is_receipt", "")):
                is_receipt_correct += 1
            else:
                predicted = pred_is_receipt if ok else f"ERROR:{result.get('error', '')}"
                mismatches.append({
                    "filename": filename, "model": model, "field": "is_receipt",
                    "truth": truth.get("is_receipt", ""), "predicted": predicted,
                })

            kind = truth.get("kind") or "receipt"
            if ok and kind != "non_receipt":
                date_total += 1
                if date_match(result.get("date", ""), truth.get("date", "")):
                    date_correct += 1
                else:
                    mismatches.append({
                        "filename": filename, "model": model, "field": "date",
                        "truth": truth.get("date", ""), "predicted": result.get("date", ""),
                    })

                amount_total += 1
                if amount_match(result.get("amount", ""), truth.get("amount", "")):
                    amount_correct += 1
                else:
                    mismatches.append({
                        "filename": filename, "model": model, "field": "amount",
                        "truth": truth.get("amount", ""), "predicted": result.get("amount", ""),
                    })

                merchant_total += 1
                if merchant_match(result.get("merchant", ""), truth.get("merchant", "")):
                    merchant_correct += 1
                else:
                    mismatches.append({
                        "filename": filename, "model": model, "field": "merchant",
                        "truth": truth.get("merchant", ""), "predicted": result.get("merchant", ""),
                    })

            if ok and result.get("latency_s"):
                latencies.append(float(result["latency_s"]))

            cost_str = result.get("est_cost_usd", "")
            if cost_str:
                if cost_str.startswith(">="):
                    cost_partial = True
                    cost_str = cost_str[2:]
                try:
                    total_cost += float(cost_str)
                except ValueError:
                    pass

        def _acc(correct: int, total: int) -> float | None:
            return correct / total if total else None

        summary[model] = {
            "n_ok": n_ok,
            "n_error": n_error,
            "is_receipt_acc": _acc(is_receipt_correct, is_receipt_total),
            "date_acc": _acc(date_correct, date_total),
            "amount_acc": _acc(amount_correct, amount_total),
            "merchant_acc": _acc(merchant_correct, merchant_total),
            "latency_mean_s": (sum(latencies) / len(latencies)) if latencies else None,
            "total_est_cost_usd": total_cost,
            "cost_partial": cost_partial,
        }

    return summary, mismatches


def _fmt_pct(v: float | None) -> str:
    return f"{v * 100:.0f}%" if v is not None else "n/a"


def _fmt_num(v: float | None, digits: int = 2) -> str:
    return f"{v:.{digits}f}" if v is not None else "n/a"


def _fmt_cost(stats: dict) -> str:
    prefix = ">=" if stats["cost_partial"] else ""
    return f"{prefix}{stats['total_est_cost_usd']:.4f}"


_SUMMARY_HEADERS = [
    "model", "n_ok", "n_error", "is_receipt_acc", "date_acc",
    "amount_acc", "merchant_acc", "latency_mean_s", "total_est_cost_usd",
]


def _summary_row(model: str, stats: dict) -> list[str]:
    return [
        model, str(stats["n_ok"]), str(stats["n_error"]),
        _fmt_pct(stats["is_receipt_acc"]), _fmt_pct(stats["date_acc"]),
        _fmt_pct(stats["amount_acc"]), _fmt_pct(stats["merchant_acc"]),
        _fmt_num(stats["latency_mean_s"]), _fmt_cost(stats),
    ]


def _print_summary(summary: dict) -> None:
    print(" | ".join(f"{h:>16}" for h in _SUMMARY_HEADERS))
    for model, stats in summary.items():
        print(" | ".join(f"{v:>16}" for v in _summary_row(model, stats)))


def _write_summary_md(out_path: Path, summary: dict, mismatches: list[dict]) -> None:
    lines = [
        "# OCR comparison summary",
        "",
        "| " + " | ".join(_SUMMARY_HEADERS) + " |",
        "|" + "---|" * len(_SUMMARY_HEADERS),
    ]
    for model, stats in summary.items():
        lines.append("| " + " | ".join(_summary_row(model, stats)) + " |")
    lines += ["", "## Mismatches", "", "| filename | model | field | truth | predicted |", "|---|---|---|---|---|"]
    for m in mismatches:
        lines.append(f"| {m['filename']} | {m['model']} | {m['field']} | {m['truth']} | {m['predicted']} |")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"Wrote summary to {out_path}")


def cmd_score(args: argparse.Namespace) -> None:
    results_rows = _read_csv_rows(Path(args.results))
    truth_rows = _read_csv_rows(Path(args.truth))
    if not truth_rows:
        raise SystemExit(f"no rows in ground-truth file {args.truth}")
    summary, mismatches = score_rows(results_rows, truth_rows)
    _print_summary(summary)
    _write_summary_md(Path(args.out), summary, mismatches)


# --- CLI -----------------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)

    extract_p = sub.add_parser("extract", help="run provider(s) against a folder of images")
    extract_p.add_argument("--images", default="data/comparison", help="folder of receipt images")
    extract_p.add_argument("--models", nargs="+", choices=ALL_MODELS, default=ALL_MODELS)
    extract_p.add_argument("--out", default="data/comparison/results.csv")
    extract_p.add_argument("--append", action="store_true", help="append to an existing results CSV")
    extract_p.add_argument("--sleep", type=float, default=2.0, help="seconds between cells (default: 2.0)")
    extract_p.set_defaults(func=cmd_extract)

    score_p = sub.add_parser("score", help="score a results CSV against ground truth")
    score_p.add_argument("--results", default="data/comparison/results.csv")
    score_p.add_argument("--truth", default="data/comparison/ground_truth.csv")
    score_p.add_argument("--out", default="data/comparison/summary.md")
    score_p.set_defaults(func=cmd_score)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
