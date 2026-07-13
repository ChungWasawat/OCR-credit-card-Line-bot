# OCR model comparison (Task 13)

## Purpose & background

This bot's OCR step is pluggable (`OCR_MODEL` env var → `app/ocr/factory.py`): four
providers exist — `claude`, `gemini`, `typhoon`, `typhoon_gemini` — with different
cost/latency/accuracy tradeoffs. Production currently runs `OCR_MODEL=claude`, but
that was a **billing-gate decision**, not a quality-backed one: `gemini` was the
interim default while Anthropic billing was unconfigured, and the switch to `claude`
on 2026-07-13 happened purely because billing became available, with no accuracy
comparison behind it.

This guide walks through running `scripts/compare_models.py` to actually measure
field-level accuracy, `is_receipt` accuracy, latency, and cost across all four
providers on real receipt photos, so the `OCR_MODEL` choice is backed by numbers —
and either ratifies `claude` or overturns it.

You're expected to run this yourself, end to end. Every step below is exact and
copy-pasteable; nothing requires touching the live bot, Sheets, or LINE.

## Step 1 — Gather photos

Create `data/comparison/` (already covered by `data/`'s `.gitignore` entry) and put
**10–20 real receipt photos** in it:

- Mostly ordinary receipts, from a mix of merchants/formats — this is what drives
  meaningful accuracy numbers; a folder of near-identical receipts tells you nothing.
- **At least 2 installment slips** (ผ่อนชำระ / IPP / Smart Pay / 0%) — the
  amount-extraction rule for these (record the TOTAL, not the monthly figure — see
  `app/ocr/base.py`'s `EXTRACTION_RULES`) is the hardest field this bot extracts,
  worth extra samples.
- **At least 1 non-receipt** (a menu, a random object, a selfie) — to measure
  `is_receipt` accuracy, not just field accuracy.
- Optionally 1–2 deliberately imperfect photos (blurry, rotated) — useful to see
  whether/how each provider populates the `quality_issue` field.

The 4 existing sample photos in `data/` (`IMG_2498.JPG`–`IMG_2501.JPG`) can be copied
in as a starting point — only `IMG_2498.JPG` has been characterized so far (a plain
Big C Supercenter receipt); the other 3 are unknown and worth looking at while you're
at it.

## Step 2 — Ground truth

Create `data/comparison/ground_truth.csv`, one row per image, columns:

```
filename,kind,is_receipt,date,merchant,amount,last4,notes
```

- `kind`: one of `receipt`, `installment`, `non_receipt` — lets the scorer verify
  you've covered the checklist's requirement and report per-class accuracy.
- `is_receipt`: `True`/`False`.
- `date`: `YYYY-MM-DD`, Common Era (if the receipt prints a Buddhist Era year, e.g.
  2569, convert it — 2569 → 2026).
- `amount`: the plain total (for installment slips, the TOTAL purchase amount, not the
  monthly figure).
- `last4`: last 4 digits of the card number if printed, else blank.
- Blank any field you can't be sure of — the scorer treats blank-vs-blank as correct
  on both sides.

**Recommended approach — have Claude Code draft it, then review it yourself:**
this is by far the most tedious step (typing ~15–20 rows of merchant names and
amounts by hand), and Claude Code can read images directly. In an interactive Claude
Code session:

> Look at each image in `data/comparison/` and draft `data/comparison/ground_truth.csv`
> with columns `filename,kind,is_receipt,date,merchant,amount,last4,notes`.

**Important caveat**: the model drafting this (Claude) is the same family as one of
the four providers being measured (`claude`). This makes the draft a real time-saver,
but **you must review every row against the actual photo before trusting it** — don't
skip this. It's not circular as long as you (the human) remain final authority on
every value; it just means the draft is a starting point, not the ground truth itself
until you've checked it.

If you'd rather type it fully by hand instead, that's equally valid — just skip the
drafting step.

## Step 3 — Extract

```
uv run python -m scripts.compare_models extract --images data/comparison
```

Prerequisites: `ANTHROPIC_API_KEY`, `GEMINI_API_KEY`, `TYPHOON_API_KEY` all set in
`.env` (already confirmed present as of this writing). Runs all 4 providers against
every image by default — pass `--models gemini typhoon_gemini` to run a subset (e.g.
to retry just one provider after a transient failure, combined with `--append` so you
don't lose the rows already written).

**Expect**: ~2s between cells (rate-limit headroom, `--sleep` to change), so a 20-image
× 4-provider run takes roughly 5–7 minutes. **Estimated cost: well under $0.25** for a
full 20-image run (Claude ≈$0.003/image, Typhoon's Claude-parse step ≈$0.002/image,
Gemini and Typhoon's OCR step assumed free-tier — see the script's `PRICE_TABLE` for
exact figures and caveats).

Output: `data/comparison/results.csv`, one row per image×provider. A provider that
fails on one image (network error, or a genuine "can't read this" content error) gets
an `error`/`content_error` row and the batch keeps going — nothing aborts the whole run.

## Step 4 — Score

```
uv run python -m scripts.compare_models score
```

(defaults: `--results data/comparison/results.csv --truth data/comparison/ground_truth.csv
--out data/comparison/summary.md`)

Prints a per-provider table to stdout and writes the same table + a full mismatch
listing to `data/comparison/summary.md`. Read the mismatch listing before trusting
`merchant_acc` specifically — the merchant-matching rule is fuzzy (case/whitespace/
punctuation-insensitive, plus substring and near-match tolerance) and it's worth
skimming for any near-misses that slipped through wrong, or genuine misses that got
flagged unfairly.

## Step 5 — Decide & record

**Decision priority for this project** (a small-group (<10 users) expense tracker, where a
wrong number silently corrupts a shared Sheet — see `docs/abnormal_photo_scenarios.md`
for the related silent-wrong-data risk):

1. **Amount + date accuracy** — highest priority; these are the fields that actually
   get written to the Sheet and reconciled against card statements.
2. **`is_receipt` accuracy** — a wrong classification is user-visible and recoverable
   (`Process anyway` button), so it matters less than a silently wrong amount.
3. **Cost** — all four providers are cheap enough at this volume (50–150 receipts/mo)
   that cost differences are unlikely to be decisive.
4. **Latency** — barely matters for a chat bot with no real-time UI pressure.

Once you've picked a model:

1. Update `ocr_model` in `infra/terraform.tfvars`, with a comment citing this doc and
   the date. `terraform apply` if it's changing (from `infra/`).
2. Update local `.env`'s `OCR_MODEL` to match, for consistency.
3. Paste the summary table + your rationale into the **Results** section below.
4. Tick Task 13's three checkboxes in `checklist3.md`, with the headline numbers.
5. `README.md` already links to this doc (added alongside this guide) — no further
   action needed there unless Task 14's full README rewrite wants more detail.

## Results

_(fill in after running)_

- **Run date:**
- **Image counts:** total ___, installment ___, non-receipt ___
- **Summary table:** (paste `data/comparison/summary.md`'s table here)
- **Notable mismatches:** (anything from the mismatch listing worth flagging)
- **Decision:** `OCR_MODEL=` ___
- **Rationale:** (3–5 sentences)
