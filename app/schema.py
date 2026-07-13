from __future__ import annotations

import datetime as dt
import enum
import re
from zoneinfo import ZoneInfo

from pydantic import BaseModel, field_validator

BE_CE_OFFSET = 543
BANGKOK_TZ = ZoneInfo("Asia/Bangkok")
_AMOUNT_STRIP = re.compile(r"[,฿\s]|THB", re.IGNORECASE)


class BoundsViolation(enum.StrEnum):
    MISSING_AMOUNT = "missing_amount"
    AMOUNT_OUT_OF_RANGE = "amount_out_of_range"
    MISSING_DATE = "missing_date"
    DATE_OUT_OF_RANGE = "date_out_of_range"


class QualityIssue(enum.StrEnum):
    """The main image-quality problem the model reports when it couldn't confidently
    read the receipt — drives targeted retake advice (see app/buttons.py::bounds_message).
    """

    BLUR = "blur"
    DARK = "dark"
    GLARE = "glare"
    ROTATED = "rotated"
    CROPPED = "cropped"
    PARTIAL = "partial"


def normalize_be_year(d: dt.date, *, today: dt.date | None = None) -> dt.date:
    """Corrects a Buddhist Era year an LLM failed to convert to CE, as a safety net
    alongside the prompt's own BE->CE instruction. Only fires when the year is
    implausibly far in the future AND the corrected year lands in a sane range, so a
    genuinely garbled date (e.g. year 3012) stays visibly wrong and fails bounds instead
    of being silently "corrected" into something plausible-looking but false.
    """
    today = today or dt.datetime.now(BANGKOK_TZ).date()
    if d.year > today.year + 5:
        candidate_year = d.year - BE_CE_OFFSET
        try:
            candidate = d.replace(year=candidate_year)
        except ValueError:
            return d
        if today.year - 50 <= candidate.year <= today.year + 1:
            return candidate
    return d


class ReceiptExtraction(BaseModel):
    """Raw OCR extraction, always constructible even from a garbled or non-receipt
    image ("even when is_receipt=false, attempt best-effort extraction"). Sanity bounds
    are NOT enforced here — see check_bounds(). This is distinct from app/store.py's
    ReceiptRow, which is the final, user-confirmed data after button taps.
    """

    is_receipt: bool = False
    date: dt.date | None = None
    merchant: str | None = None
    amount: float | None = None
    last4: str | None = None
    details: str | None = None
    quality_issue: QualityIssue | None = None

    @field_validator("date", mode="before")
    @classmethod
    def _coerce_date(cls, v: object) -> object:
        if v is None or isinstance(v, dt.date):
            return v
        try:
            return dt.date.fromisoformat(str(v))
        except ValueError:
            return None

    @field_validator("amount", mode="before")
    @classmethod
    def _coerce_amount(cls, v: object) -> object:
        if v is None or isinstance(v, (int, float)):
            return v
        cleaned = _AMOUNT_STRIP.sub("", str(v)).strip()
        try:
            return float(cleaned)
        except ValueError:
            return None

    @field_validator("last4", mode="before")
    @classmethod
    def _coerce_last4(cls, v: object) -> object:
        if v is None:
            return None
        digits = re.sub(r"\D", "", str(v))
        return digits[-4:] or None

    @field_validator("date", mode="after")
    @classmethod
    def _fix_be_year(cls, v: dt.date | None) -> dt.date | None:
        return normalize_be_year(v) if v is not None else v

    @field_validator("quality_issue", mode="before")
    @classmethod
    def _coerce_quality_issue(cls, v: object) -> object:
        if v is None or isinstance(v, QualityIssue):
            return v
        try:
            return QualityIssue(str(v).strip().lower())
        except ValueError:
            return None


def check_bounds(
    extraction: ReceiptExtraction, *, today: dt.date | None = None
) -> list[BoundsViolation]:
    """Sanity bounds: 0 < amount <= 100000; date within -90d..+1d. Caller decides WHEN
    to enforce this (is_receipt True, or user tapped Process anyway) — this function has
    no is_receipt awareness. These are internal codes, not user-facing text — translate
    them before showing anything to a family member.
    """
    today = today or dt.datetime.now(BANGKOK_TZ).date()
    violations: list[BoundsViolation] = []

    if extraction.amount is None:
        violations.append(BoundsViolation.MISSING_AMOUNT)
    elif not (0 < extraction.amount <= 100_000):
        violations.append(BoundsViolation.AMOUNT_OUT_OF_RANGE)

    if extraction.date is None:
        violations.append(BoundsViolation.MISSING_DATE)
    else:
        lower, upper = today - dt.timedelta(days=90), today + dt.timedelta(days=1)
        if not (lower <= extraction.date <= upper):
            violations.append(BoundsViolation.DATE_OUT_OF_RANGE)

    return violations
