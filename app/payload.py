from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, replace

VERSION = "v1"
FIELD_COUNT = 13  # version + 12 data fields

STEP_CARD = "card"
STEP_CATEGORY = "cat"
STEP_SKIP = "skip"
STEP_DETAILS = "det"
STEP_PROCESS_ANYWAY = "pa"
STEP_CANCEL = "cancel"

MAX_DATA_LEN = 300  # Line's postback `data` field hard limit (characters)
DETAILS_MAX = 60
MERCHANT_MAX = 40
TYPED_DETAILS_MAX = 30
FILL_IN_PREFIX = "#"
# What a genuine fill-in text starts with ("#v1|"). Routing matches on this, not the
# bare "#", so ordinary chat hashtags ("#dinner") are ignored silently instead of
# getting a "couldn't match that to a receipt" error reply.
FILL_IN_SIGNATURE = f"{FILL_IN_PREFIX}{VERSION}|"


class PayloadError(Exception):
    """Raised on a corrupt, wrong-version, or unparseable payload. A stale/tampered
    postback must fail loudly here rather than being half-parsed by the caller.
    """


@dataclass(frozen=True)
class Payload:
    step: str
    message_id: str
    blob: str
    sender: str
    ocr_model: str
    date: dt.date | None = None
    amount: float | None = None
    last4: str | None = None
    card_id: str | None = None
    category: str | None = None
    merchant: str | None = None
    details: str | None = None


def _escape(value: str) -> str:
    # Order matters: escape the escape character itself first.
    value = value.replace("%", "%25").replace("|", "%7C")
    for ch in ("\r", "\n", "\t"):
        value = value.replace(ch, " ")
    return value


def _unescape(value: str) -> str:
    return value.replace("%7C", "|").replace("%25", "%")


def _fmt(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, dt.date):
        return value.isoformat()
    if isinstance(value, float):
        return f"{value:.2f}"
    return str(value)


def _fields(p: Payload) -> list[str]:
    details = (p.details or "")[:DETAILS_MAX]
    merchant = (p.merchant or "")[:MERCHANT_MAX]
    return [
        p.step,
        p.message_id,
        p.blob,
        p.sender,
        p.ocr_model,
        _fmt(p.date),
        _fmt(p.amount),
        _fmt(p.last4),
        _fmt(p.card_id),
        _fmt(p.category),
        merchant,
        details,
    ]


def encode(p: Payload) -> str:
    """Encodes `p` into a `|`-delimited, versioned string safe for Line's postback
    `data` field. `details`/`merchant` are truncated before escaping — the truncated
    value is what later lands in the sheet, per the checklist's resolved assumption.

    If escaping pushes the result past the 300-char limit (only possible with
    pipe/percent-heavy free text), progressively trims the longer of details/merchant
    and retries. Raises PayloadError if it still doesn't fit — an explicit raise, not
    an `assert` (asserts vanish under `python -O`), so the 300-char guarantee always
    holds.
    """
    details = (p.details or "")[:DETAILS_MAX]
    merchant = (p.merchant or "")[:MERCHANT_MAX]

    while True:
        candidate = replace(p, details=details, merchant=merchant)
        raw_fields = _fields(candidate)
        out = "|".join([VERSION, *(_escape(f) for f in raw_fields)])
        if len(out) <= MAX_DATA_LEN:
            return out
        if len(details) >= len(merchant) and details:
            details = details[:-1]
        elif merchant:
            merchant = merchant[:-1]
        else:
            raise PayloadError(f"payload exceeds {MAX_DATA_LEN} chars and cannot be trimmed further")


def decode(data: str) -> Payload:
    parts = data.split("|")
    if not parts or parts[0] != VERSION:
        raise PayloadError(f"unsupported payload version: {parts[0] if parts else '<empty>'!r}")
    if len(parts) != FIELD_COUNT:
        raise PayloadError(f"expected {FIELD_COUNT} fields, got {len(parts)}")

    (
        step,
        message_id,
        blob,
        sender,
        ocr_model,
        date_s,
        amount_s,
        last4_s,
        card_id_s,
        category_s,
        merchant_s,
        details_s,
    ) = (_unescape(f) for f in parts[1:])

    date_val: dt.date | None = None
    if date_s:
        try:
            date_val = dt.date.fromisoformat(date_s)
        except ValueError:
            raise PayloadError(f"invalid date field: {date_s!r}") from None

    amount_val: float | None = None
    if amount_s:
        try:
            amount_val = float(amount_s)
        except ValueError:
            raise PayloadError(f"invalid amount field: {amount_s!r}") from None

    return Payload(
        step=step,
        message_id=message_id,
        blob=blob,
        sender=sender,
        ocr_model=ocr_model,
        date=date_val,
        amount=amount_val,
        last4=last4_s or None,
        card_id=card_id_s or None,
        category=category_s or None,
        merchant=merchant_s or None,
        details=details_s or None,
    )


def encode_fill_in(p: Payload) -> str:
    """Encodes `p` (with `details` cleared — the user is about to type it) as a
    keyboard fill-in prefix: `"#" + encoded + "\\n"`. The user types after this prefix
    (Line's fillInText pre-fills the input box) and sends it as a plain text message;
    `decode_fill_in` recovers both. A newline (not a space) separates the encoded
    payload from the typed text: merchant/details text legitimately contains spaces,
    but `_escape` already guarantees the encoded payload itself never contains a raw
    `\\n` (converted to a space during escaping), so splitting on the first newline is
    unambiguous even when the user's typed text is empty or contains spaces.
    """
    stripped = replace(p, details=None)
    return f"{FILL_IN_PREFIX}{encode(stripped)}\n"


def decode_fill_in(text: str) -> tuple[Payload, str]:
    """Reverses `encode_fill_in`: splits a text message into the embedded payload and
    the user-typed details that follow it. Typed details are trimmed and capped at
    `TYPED_DETAILS_MAX` chars.
    """
    if not text.startswith(FILL_IN_PREFIX):
        raise PayloadError("text does not start with the fill-in prefix")
    body = text[len(FILL_IN_PREFIX) :]
    encoded, _, typed = body.partition("\n")
    p = decode(encoded)
    return p, typed.strip()[:TYPED_DETAILS_MAX]
