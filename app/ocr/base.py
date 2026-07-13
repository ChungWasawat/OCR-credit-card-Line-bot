from __future__ import annotations

import abc
import json
from typing import ClassVar

PNG_MAGIC = b"\x89PNG"


class OcrContentError(Exception):
    """Family root for deterministic, content-shaped OCR failures: retrying the same
    image reproduces the same failure, so the worker replies "cannot read" and returns
    200 (no Cloud Tasks retry) instead of propagating for a retry.
    """


class OcrParseError(OcrContentError):
    """Raised when the LLM's response isn't valid JSON after recovery attempts, isn't a
    JSON object, or carried no text block at all (e.g. a safety refusal). Same
    treatment as a downstream pydantic.ValidationError from
    ReceiptExtraction.model_validate: both are content errors (reply "cannot read", no
    Cloud Tasks retry), never transient errors.
    """


class OcrImageError(OcrContentError):
    """Raised when the OCR provider's API deterministically rejected the image itself
    (oversized payload, corrupt bytes, unsupported format) with a 4xx that would never
    succeed on retry — same "cannot read" treatment as OcrParseError.
    """


# HTTP/API statuses that mean "this request body/image will never be accepted, no
# matter how many times it's retried" — deliberately excludes 401/403/404 (config
# errors the owner must fix, not the user) and 429 (transient rate limiting).
DETERMINISTIC_IMAGE_STATUSES = frozenset({400, 413, 415, 422})


class OcrProvider(abc.ABC):
    """Every provider returns the same dict shape regardless of how many internal calls
    it makes. ReceiptExtraction.model_validate is the single source of truth for the
    expected keys/types — providers don't re-validate beyond "is this a JSON object".
    """

    # Matches the OCR_MODEL value app/ocr/factory.py dispatched on to construct this
    # provider — the single source of truth for the sheet's ocr_model column, so the
    # worker never has to re-read OCR_MODEL itself and risk drifting from the factory.
    name: ClassVar[str]

    @abc.abstractmethod
    def extract(self, image: bytes) -> dict: ...


EXTRACTION_RULES = """\
Respond with ONLY a single JSON object. No prose, no markdown code fences, no explanation
before or after.

First field must be "is_receipt": true or false — is this a credit card purchase
receipt/slip? Even if is_receipt is false, still attempt best-effort extraction of every
other field from whatever is visible.

Fields, in this order:
  is_receipt: bool
  date: string "YYYY-MM-DD" or null. Thai receipts often print the year in Buddhist Era
    (BE = CE + 543, e.g. a printed year of 2569 means 2026) — always convert to the
    Common Era (CE) Gregorian year before outputting.
  merchant: string or null — the merchant/store name.
  amount: number (plain, no currency symbol, no thousands separator, THB) or null — see
    the installment rule below.
  last4: string (exactly 4 digits) or null — last 4 digits of the card number if printed.
  details: string — free-text notes (e.g. installment term); "" if none.
  quality_issue: one of "blur", "dark", "glare", "rotated", "cropped", "partial", or
    null — the main image-quality problem that stopped you reading any field with
    confidence (cropped = an edge of the slip is cut off in the frame; partial = only a
    fragment of the slip is visible). null when the image was readable.

Installment / ผ่อนชำระ / IPP / Smart Pay / 0% plans: the slip shows both a monthly
installment amount and a total purchase amount. Set "amount" to the TOTAL purchase amount
(never the monthly figure), and append the term to "details", e.g. "ผ่อน 10 เดือน".

Any field you cannot determine → null (or "" for details). Do not guess.\
"""

IMAGE_PROMPT = f"""\
You are extracting structured data from a photo of a Thai credit-card receipt or slip.

{EXTRACTION_RULES}\
"""

MARKDOWN_PROMPT_TEMPLATE = f"""\
Below is OCR-extracted markdown text of a Thai credit-card receipt or slip. It may be
noisy, partial, or contain layout artifacts from the OCR step — use your judgement.

{EXTRACTION_RULES}

Receipt OCR markdown:
{{markdown}}\
"""


def first_text_block(response) -> str:
    """Extracts the first text block from an Anthropic message response. Raises
    OcrParseError instead of letting a bare StopIteration escape when the response
    carries no text block at all (e.g. a safety refusal) — that's a content error, not
    an unhandled exception that would propagate as if transient.
    """
    text = next((block.text for block in response.content if block.type == "text"), None)
    if text is None:
        raise OcrParseError("no text block in model response (possible refusal)")
    return text


def parse_llm_json(text: str) -> dict:
    """Recovers a JSON object from LLM output that may include markdown fences, a
    preamble, or trailing prose despite being told not to. Raises OcrParseError if no
    JSON object can be recovered.
    """
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        start, end = text.find("{"), text.rfind("}")
        if start == -1 or end == -1 or end < start:
            raise OcrParseError(f"model did not return JSON: {text[:200]!r}") from None
        try:
            parsed = json.loads(text[start : end + 1])
        except json.JSONDecodeError as exc:
            raise OcrParseError(f"model did not return valid JSON: {text[:200]!r}") from exc

    if not isinstance(parsed, dict):
        raise OcrParseError(f"model returned non-object JSON: {text[:200]!r}")
    return parsed


def detect_mime(image: bytes) -> str:
    if image.startswith(PNG_MAGIC):
        return "image/png"
    return "image/jpeg"


def blank_extraction() -> dict:
    """The all-null shape returned when an OCR step produced nothing to parse (e.g.
    blank Typhoon markdown) — skips the parse-step call entirely rather than asking an
    LLM to extract structured data from nothing. Returns a fresh dict per call.
    """
    return {
        "is_receipt": False,
        "date": None,
        "merchant": None,
        "amount": None,
        "last4": None,
        "details": "",
        "quality_issue": None,
    }
