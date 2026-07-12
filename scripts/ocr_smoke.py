"""Manual OCR verification (Task 6): "one manual run per provider against a real
receipt photo."

Prerequisites:
- .env populated with the relevant API key(s) for the model(s) you're testing
- ANTHROPIC_API_KEY has no billing configured yet — --model claude and --model typhoon
  (which uses Claude Haiku for its parse step) will fail until that's set up.
  --model gemini and --model typhoon_gemini need only GEMINI_API_KEY
  (+ TYPHOON_API_KEY for the latter) — both are fully free, zero Anthropic dependency.

Usage:
  uv run python scripts/ocr_smoke.py --model gemini
  uv run python scripts/ocr_smoke.py --model typhoon_gemini
  uv run python scripts/ocr_smoke.py --model typhoon --image data/some_other_receipt.jpg
"""

from __future__ import annotations

import argparse
import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

from app.schema import ReceiptExtraction, check_bounds  # noqa: E402

_DEFAULT_IMAGE = Path(__file__).resolve().parent.parent / "data" / "IMG_2498.JPG"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image", type=Path, default=_DEFAULT_IMAGE)
    parser.add_argument(
        "--model",
        choices=["claude", "typhoon", "gemini", "typhoon_gemini"],
        default=None,
    )
    args = parser.parse_args()

    if not args.image.exists():
        raise SystemExit(f"image not found: {args.image}")

    if args.model:
        os.environ["OCR_MODEL"] = args.model

    from app.ocr.factory import get_ocr_provider  # deferred: pick up OCR_MODEL override

    provider = get_ocr_provider()
    print(f"Provider: {type(provider).__name__}")
    print(f"Image: {args.image}")

    raw = provider.extract(args.image.read_bytes())
    print(f"Raw extraction: {raw}")

    extraction = ReceiptExtraction.model_validate(raw)
    print(f"Parsed: {extraction}")

    violations = check_bounds(extraction)
    print(f"Bounds violations: {violations or 'none (passes)'}")


if __name__ == "__main__":
    main()
