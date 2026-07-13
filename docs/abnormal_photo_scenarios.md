# Abnormal photo scenarios — behavior and classification

Companion reference to `problems_and_fixes.md`'s Task 15/16 entries. Answers, for every
kind of "bad" receipt photo a group member might send: what the bot does today, what it
does after the fixes in this task, and whether the failure is something the *user* can fix
by retaking the photo, or something only the *owner* can fix (billing, config, quota).

Root cause behind every "Behavior today: silence" row: `services/worker_main.py`'s `/task`
handler only special-cased `OcrParseError`/`ValidationError` as content errors. Every other
exception from the OCR call — including deterministic ones like "the API rejected this
image and always will" — fell through to the outer `except`, which re-raises so Cloud Tasks
retries 3 times, then logs an ERROR and returns 500 with no reply ever sent to the user.

## User-fixable — bot now asks the user to retake the photo

| Scenario | Behavior today (before this task) | Behavior after the fix | Classification |
|---|---|---|---|
| Slightly inclined/rotated | Usually reads fine (Claude/Gemini vision tolerates modest skew). If fields still come out unreadable, generic bounds message. | Same, but if the model reports `quality_issue: rotated`, the reply adds "the slip looks rotated — retake with it upright." | User-fixable |
| Upside down | Fields misread or come back null. | Bounds violation with a targeted "rotated" tip instead of a generic "please resend." | User-fixable |
| Blurry | Fields unreadable → generic "Cannot read this receipt clearly (...). Please resend the photo." | Same bounds message, now with "the photo looks blurry — hold the camera steady" when the model detects it. | User-fixable |
| Too dark | Same as blurry. | Targeted "the photo looks too dark — retake in better light." | User-fixable |
| Glare / reflection | Same as blurry. | Targeted "there's glare on the slip — retake without flash or reflections." | User-fixable |
| Cropped (edge cut off) | Missing amount/date → generic bounds message. | Targeted "part of the slip is cut off — fit the whole slip in the frame." | User-fixable |
| Partial (only a fragment visible) | Same as cropped. | Targeted "only part of the slip is visible — fit the whole slip in the frame." | User-fixable |
| Not a receipt at all (pet photo, selfie, random object) | Already handled: `is_receipt=false` → "Doesn't look like a receipt" + `Process anyway` quick reply. | **Unchanged** — already correct. | User-fixable (self-evident) |
| Blank/near-empty OCR output (e.g. all-white or all-black photo) | `blank_extraction()` → treated as not-a-receipt → same reply as above. | **Unchanged** — already correct. | User-fixable |
| Oversized image (LINE allows photos up to ~10 MB; Anthropic/Gemini/Typhoon reject overly large payloads with a 4xx) | Provider API 400/413 → **not** classified as content error → 3 futile retries (each re-pays for a rejected call) → final 500 → **silent drop, no reply**. | Provider exception mapped to `OcrImageError` → immediate "Couldn't read that photo — please resend." + 200, no retry. | User-fixable (resend a smaller/compressed photo) |
| Corrupt or truncated image bytes | Same silent-drop path as oversized — provider 4xx misclassified as transient. | Same `OcrImageError` fix — immediate reply instead of 3 retries then silence. | User-fixable (resend usually works) |
| Model safety refusal / response with no text block (e.g. content flagged, model declines) | `StopIteration` from `next(block.text for block in response.content if block.type == "text")` in `claude.py`/`typhoon.py` — not a caught exception type → 3 retries → **silent drop**. | Extraction wrapped in a `first_text_block()` helper that raises `OcrParseError` instead of leaking `StopIteration` → immediate "Couldn't read that photo — please resend." | User-fixable (retake usually avoids whatever triggered it) |

## System-critical — user cannot fix; owner must act

These stay on the transient/retry path (retrying is the *correct* behavior — the failure
may clear on its own), but previously ended in total silence after 3 exhausted attempts.
After the fix, the final attempt sends a generic failure signal so the user at least knows
something went wrong, even though the underlying cause needs the owner's attention.

| Scenario | Behavior today | Behavior after the fix | Classification |
|---|---|---|---|
| Provider quota/rate-limit exhausted (e.g. Gemini free-tier `429 RESOURCE_EXHAUSTED`, a real observed failure mode noted in `app/ocr/gemini.py`) | 3 retries, all fail identically within the backoff window → ERROR log, no reply. | Same retries (correct — quota can free up between attempts), but the final attempt now sends "Something went wrong — please resend that." | System-critical (owner must check API quota/billing) |
| Provider 5xx / timeout | Same as above. | Same fix — final-attempt reply. | System-critical (usually self-resolves; owner monitors ERROR rate alert from Task 10) |
| Bad/expired API key (401/403) | 3 retries against a guaranteed-to-fail key → ERROR log, no reply. | Final-attempt reply sent, but the underlying 401/403 still needs the owner to fix the key — retrying doesn't help, it just bounds the delay before the user is told. | System-critical (owner must fix Secret Manager value) |
| Retired/renamed model (404) — e.g. `TyphoonOcrError` from `app/ocr/typhoon.py`'s endpoint-assumption breaking | Same silent-drop-after-3-retries pattern. | Deliberately **not** classified as a content error (a "resend your photo" reply would be false — the config is broken, not the image) — stays transient, gets the final-attempt generic reply instead. | System-critical (owner must fix the model/endpoint assumption) |
| LINE content-download failure (network/timeout/5xx from LINE's content API) | Retries, then silent drop. | Final-attempt reply. | System-critical (LINE outage or network issue) |
| GCS upload failure | Retries, then silent drop. | Final-attempt reply. | System-critical (GCS outage, IAM misconfiguration) |
| Google Sheets read failure (`store.read_cards()`) | Retries, then silent drop. | Final-attempt reply. | System-critical (Sheets API outage/quota) |
| Reply send itself fails (429/5xx from LINE's Messaging API, including the final-attempt best-effort reply) | Propagates for retry (or, for the new best-effort reply, is caught and logged as a WARNING so it can't mask the real error). | Same; the best-effort reply is explicitly best-effort — a second failure there is swallowed, not retried again. | System-critical (LINE API issue or push-quota exhaustion) |

## Not addressed by this task (explicit scope decision)

- **Image preprocessing** (EXIF-orientation auto-correction, deskewing, downscaling
  oversized images before sending to the OCR model) was considered and deliberately
  excluded — the fix here is entirely about making existing failures *visible* to the
  user, not about correcting the image server-side. A rotated/oversized photo still
  needs the user to retake or resend it; the difference is the bot now tells them to.
