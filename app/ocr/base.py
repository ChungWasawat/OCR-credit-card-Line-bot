from __future__ import annotations

import abc
import json
from typing import ClassVar

PNG_MAGIC = b"\x89PNG"


class OcrParseError(Exception):
    """Raised when the LLM's response isn't valid JSON after recovery attempts, or
    isn't a JSON object. Same treatment as a downstream pydantic.ValidationError from
    ReceiptExtraction.model_validate: both are content errors (reply "cannot read", no
    Cloud Tasks retry), never transient errors.
    """


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
    }
