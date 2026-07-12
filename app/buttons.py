from __future__ import annotations

import logging
from dataclasses import replace

from linebot.v3.messaging import PostbackAction, QuickReply, QuickReplyItem

from app.payload import (
    STEP_CANCEL,
    STEP_CARD,
    STEP_CATEGORY,
    STEP_DETAILS,
    STEP_PROCESS_ANYWAY,
    STEP_SKIP,
    Payload,
    encode,
    encode_fill_in,
)
from app.schema import BoundsViolation
from app.store import Card

logger = logging.getLogger(__name__)

CATEGORIES: list[str] = [
    "grocery",
    "eating out",
    "car fuel",
    "health",
    "travel",
    "shopping",
    "household",
    "others",
]
assert len(CATEGORIES) <= 12, "13-item Line quick-reply cap includes the Cancel item"

CANCEL_LABEL = "Cancel"
SKIP_LABEL = "Skip"
TYPE_DETAILS_LABEL = "Type details"
PROCESS_ANYWAY_LABEL = "Process anyway"

QUICK_REPLY_LABEL_MAX = 20

_BOUNDS_MESSAGES = {
    BoundsViolation.MISSING_AMOUNT: "amount is missing",
    BoundsViolation.AMOUNT_OUT_OF_RANGE: "amount looks out of range",
    BoundsViolation.MISSING_DATE: "date is missing",
    BoundsViolation.DATE_OUT_OF_RANGE: "date looks out of range",
}


def summary_line(p: Payload) -> str:
    merchant = p.merchant or "?"
    amount = f"THB {p.amount:,.2f}" if p.amount is not None else "?"
    return f"{merchant} — {amount}"


def _reasons(violations: list[BoundsViolation]) -> str:
    return ", ".join(_BOUNDS_MESSAGES.get(v, str(v)) for v in violations)


def bounds_message(violations: list[BoundsViolation]) -> str:
    return f"Cannot read this receipt clearly ({_reasons(violations)}). Please resend the photo."


def process_anyway_blocked_message(violations: list[BoundsViolation]) -> str:
    """The honest dead end for "Process anyway": OCR found no usable amount/date, so
    there is nothing to record and resending the same image would OCR identically —
    unlike bounds_message, this must NOT suggest resending the photo.
    """
    return (
        f"Can't record this ({_reasons(violations)}). Recording needs a readable "
        "amount and date — this image can't be processed."
    )


def _cancel_item(p: Payload) -> QuickReplyItem:
    cancel_payload = replace(p, step=STEP_CANCEL)
    return QuickReplyItem(
        action=PostbackAction(
            label=CANCEL_LABEL,
            data=encode(cancel_payload),
            displayText=CANCEL_LABEL,
        )
    )


MAX_CARD_ITEMS = 12  # 13-item Line quick-reply cap minus the Cancel item


def card_quick_reply(cards: list[Card], p: Payload) -> QuickReply:
    """OCR-matched last4 ordered first (stable sort — never auto-skips the tap; a
    wrong auto-match would write to the wrong tab silently).
    """
    ordered = sorted(cards, key=lambda c: c.last4 != p.last4)
    if len(ordered) > MAX_CARD_ITEMS:
        logger.warning(
            "Cards tab has %d active cards, only the first %d fit in a quick reply",
            len(ordered),
            MAX_CARD_ITEMS,
        )
        ordered = ordered[:MAX_CARD_ITEMS]
    items = []
    for card in ordered:
        label = f"{card.bank} •{card.last4}"[:QUICK_REPLY_LABEL_MAX]
        item_payload = replace(p, step=STEP_CARD, card_id=card.card_id)
        items.append(
            QuickReplyItem(
                action=PostbackAction(
                    label=label,
                    data=encode(item_payload),
                    displayText=label,
                )
            )
        )
    items.append(_cancel_item(p))
    return QuickReply(items=items)


def category_quick_reply(p: Payload) -> QuickReply:
    items = []
    for name in CATEGORIES:
        item_payload = replace(p, step=STEP_CATEGORY, category=name)
        items.append(
            QuickReplyItem(
                action=PostbackAction(
                    label=name[:QUICK_REPLY_LABEL_MAX],
                    data=encode(item_payload),
                    displayText=name,
                )
            )
        )
    items.append(_cancel_item(p))
    return QuickReply(items=items)


def details_quick_reply(p: Payload) -> QuickReply:
    skip_payload = replace(p, step=STEP_SKIP)
    details_payload = replace(p, step=STEP_DETAILS)
    items = [
        QuickReplyItem(
            action=PostbackAction(
                label=SKIP_LABEL,
                data=encode(skip_payload),
                displayText=SKIP_LABEL,
            )
        ),
        QuickReplyItem(
            action=PostbackAction(
                label=TYPE_DETAILS_LABEL,
                data=encode(details_payload),
                inputOption="openKeyboard",
                fillInText=encode_fill_in(p),
            )
        ),
        _cancel_item(p),
    ]
    return QuickReply(items=items)


def not_receipt_quick_reply(p: Payload) -> QuickReply:
    process_payload = replace(p, step=STEP_PROCESS_ANYWAY)
    items = [
        QuickReplyItem(
            action=PostbackAction(
                label=PROCESS_ANYWAY_LABEL,
                data=encode(process_payload),
                displayText=PROCESS_ANYWAY_LABEL,
            )
        ),
        _cancel_item(p),
    ]
    return QuickReply(items=items)
