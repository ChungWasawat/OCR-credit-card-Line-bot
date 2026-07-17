from __future__ import annotations

import datetime as dt
import logging
import os
from dataclasses import dataclass

from linebot.v3.messaging import TextMessage
from linebot.v3.webhooks import (
    GroupSource,
    ImageMessageContent,
    MessageEvent,
    PostbackEvent,
    TextMessageContent,
)

from app.buttons import (
    bounds_message,
    card_quick_reply,
    category_quick_reply,
    details_quick_reply,
    not_receipt_quick_reply,
    process_anyway_blocked_message,
    summary_line,
)
from app.gcs import view_link_for
from app.payload import (
    FILL_IN_SIGNATURE,
    STEP_CANCEL,
    STEP_CARD,
    STEP_CATEGORY,
    STEP_DETAILS,
    STEP_PROCESS_ANYWAY,
    STEP_SKIP,
    Payload,
    PayloadError,
    decode,
    decode_fill_in,
)
from app.reply import Reply
from app.schema import ReceiptExtraction, check_bounds
from app.store import Card, ReceiptRow, ReceiptStore

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Enqueue:
    """Trigger 1 (image message): webhook_main turns this into a Cloud Task. No
    network I/O happens here — Task 7 only decides that an image event should be
    enqueued, Task 8 owns Cloud Tasks and idempotent task naming.
    """

    message_id: str
    webhook_event_id: str
    group_id: str
    user_id: str | None
    reply_token: str


@dataclass(frozen=True)
class CleanupReply:
    """A reply that dead-ends the flow (no further buttons, or the quick reply that
    would have led to a delete is now gone) AND the receipt image it's attached to
    will never be referenced by a written row. The caller deletes the already-uploaded
    GCS image (best-effort) around sending `reply` — route_event/handle_ocr_result
    never touch the network themselves, same split as Enqueue.

    Covers: STEP_CANCEL (any step), a bounds-violation reply after OCR
    (handle_ocr_result), and Process-anyway blocked by bounds (still no usable
    amount/date, so resending would OCR identically).

    The caller decides delete-vs-send ORDER, not this dataclass: webhook_main deletes
    before sending so "Cancelled" is honest; worker_main sends first, deletes after —
    if the send raises (429/5xx) and Cloud Tasks retries, the blob must still be there
    for that retry's upload to hit the 412 already-uploaded path instead of re-paying
    for OCR (same rationale as the existing OcrContentError branch).

    Deliberately NOT covers: an ignored "doesn't look like a receipt" prompt (nobody
    tapped Process anyway or Cancel) — whether a tap is still coming is unknowable, so
    that blob is not cleaned up here (known limitation, 7-day soft-delete is the
    safety net). Nor a Cloud Tasks retry that exhausts all attempts — that path deletes
    its own blob separately in worker_main's outer `except`, since no Reply/CleanupReply
    return value exists there (it's an exception, not the happy return path).
    """

    blob: str
    reply: Reply


def _group_id(event: object) -> str | None:
    source = getattr(event, "source", None)
    if isinstance(source, GroupSource):
        return source.group_id
    return None


def allowed_group_id() -> str:
    return os.environ.get("ALLOWED_GROUP_ID", "")


def route_event(event: object, store: ReceiptStore) -> Enqueue | Reply | CleanupReply | None:
    """Routes one already-parsed Line webhook event. Never touches the network —
    returns an action for the caller (Task 8's webhook_main) to execute.
    """
    group_id = _group_id(event)
    if group_id is None:
        logger.info("ignoring event with no group source")
        return None

    allowed = allowed_group_id()
    if not allowed or group_id != allowed:
        logger.warning("rejected event from non-allowlisted group id=%s", group_id)
        return None

    if isinstance(event, MessageEvent) and isinstance(event.message, ImageMessageContent):
        return Enqueue(
            message_id=event.message.id,
            webhook_event_id=event.webhook_event_id,
            group_id=group_id,
            user_id=getattr(event.source, "user_id", None),
            reply_token=event.reply_token,
        )

    if isinstance(event, MessageEvent) and isinstance(event.message, TextMessageContent):
        text = event.message.text
        if text.startswith(FILL_IN_SIGNATURE):
            return _handle_typed_details(
                text, group_id=group_id, reply_token=event.reply_token, store=store
            )
        return None  # plain chat text ignored silently

    if isinstance(event, PostbackEvent):
        try:
            p = decode(event.postback.data)
        except PayloadError:
            logger.warning("rejected corrupt or stale postback data")
            return None
        return _handle_postback(p, group_id=group_id, reply_token=event.reply_token, store=store)

    return None  # stickers, video, audio, files, joins, unsends... — ignored silently


def _build_row(p: Payload, *, details: str) -> ReceiptRow:
    return ReceiptRow(
        card_id=p.card_id,
        date=p.date,
        category=p.category,
        amount=p.amount,
        details=details,
        receipt_link=view_link_for(p.blob),
        submitted_by=p.sender,
        ocr_model=p.ocr_model,
        message_id=p.message_id,
    )


def _write_and_confirm(
    p: Payload, *, details: str, group_id: str, reply_token: str, store: ReceiptStore
) -> Reply:
    """Shared write path for STEP_SKIP and the typed-details text message. Wraps both
    row construction and the append in try/except: there is no Cloud Tasks retry queue
    behind this synchronous write, so any exception that escapes here (including a
    corrupted fill-in payload failing ReceiptRow validation) burns the reply token and
    the user sees nothing. Logs ERROR with message_id/step and replies something other
    than "Recorded ✓" instead.
    """
    try:
        row = _build_row(p, details=details)
        written = store.append_receipt(row)
    except Exception:
        logger.error(
            "failed to append receipt row message_id=%s step=%s", p.message_id, p.step,
            exc_info=True,
        )
        return Reply(
            reply_token=reply_token,
            group_id=group_id,
            messages=[TextMessage(text="Failed to record — please resend the photo.")],
        )
    verb = "Recorded" if written else "Already recorded"
    text = f"{verb} ✓ {summary_line(p)} → {p.card_id} / {p.category}"
    return Reply(reply_token=reply_token, group_id=group_id, messages=[TextMessage(text=text)])


def _handle_typed_details(
    text: str, *, group_id: str, reply_token: str, store: ReceiptStore
) -> Reply:
    try:
        p, typed = decode_fill_in(text)
    except PayloadError:
        logger.warning("rejected corrupt or stale fill-in text")
        return Reply(
            reply_token=reply_token,
            group_id=group_id,
            messages=[
                TextMessage(text="Couldn't match that to a receipt — tap Type details again.")
            ],
        )
    return _write_and_confirm(
        p, details=typed, group_id=group_id, reply_token=reply_token, store=store
    )


def _handle_postback(
    p: Payload, *, group_id: str, reply_token: str, store: ReceiptStore
) -> Reply | CleanupReply | None:
    if p.step == STEP_CARD:
        text = f"Pick a category — {summary_line(p)}"
        return Reply(
            reply_token=reply_token,
            group_id=group_id,
            messages=[TextMessage(text=text, quickReply=category_quick_reply(p))],
        )

    if p.step == STEP_CATEGORY:
        text = f"Add details? — {summary_line(p)}"
        return Reply(
            reply_token=reply_token,
            group_id=group_id,
            messages=[TextMessage(text=text, quickReply=details_quick_reply(p))],
        )

    if p.step == STEP_SKIP:
        return _write_and_confirm(
            p, details=p.details or "", group_id=group_id, reply_token=reply_token, store=store
        )

    if p.step == STEP_DETAILS:
        # Keyboard opening is entirely client-side (inputOption=openKeyboard); the
        # payload rides inside fillInText, not this postback's data. Nothing to do.
        return None

    if p.step == STEP_PROCESS_ANYWAY:
        extraction = ReceiptExtraction(
            is_receipt=True,
            date=p.date,
            merchant=p.merchant,
            amount=p.amount,
            last4=p.last4,
            details=p.details,
        )
        violations = check_bounds(extraction)
        if violations:
            # Dead end: resending the same image would OCR identically, so no future
            # tap will ever reference this blob — clean it up (see CleanupReply).
            reply = Reply(
                reply_token=reply_token,
                group_id=group_id,
                messages=[TextMessage(text=process_anyway_blocked_message(violations))],
            )
            return CleanupReply(blob=p.blob, reply=reply)
        cards = store.read_cards()
        text = f"Pick a card — {summary_line(p)}"
        return Reply(
            reply_token=reply_token,
            group_id=group_id,
            messages=[TextMessage(text=text, quickReply=card_quick_reply(cards, p))],
        )

    if p.step == STEP_CANCEL:
        text = f"Cancelled — {summary_line(p)}"
        reply = Reply(reply_token=reply_token, group_id=group_id, messages=[TextMessage(text=text)])
        return CleanupReply(blob=p.blob, reply=reply)

    logger.warning("rejected postback with unknown step=%s", p.step)
    return None


def handle_ocr_result(
    extraction: ReceiptExtraction,
    *,
    message_id: str,
    blob: str,
    sender: str,
    ocr_model: str,
    group_id: str,
    reply_token: str,
    cards: list[Card],
    today: dt.date | None = None,
) -> Reply | CleanupReply:
    """Trigger 1's post-OCR branching (worker calls this after OCR + Pydantic
    validation). Kept in Task 7, not Task 8, because it's conversation logic (which
    buttons, which text) — the worker only feeds it a finished extraction.
    """
    p = Payload(
        step=STEP_CARD,
        message_id=message_id,
        blob=blob,
        sender=sender,
        ocr_model=ocr_model,
        date=extraction.date,
        amount=extraction.amount,
        last4=extraction.last4,
        merchant=extraction.merchant,
        details=extraction.details,
    )

    if not extraction.is_receipt:
        text = f"Doesn't look like a receipt — {summary_line(p)}"
        return Reply(
            reply_token=reply_token,
            group_id=group_id,
            messages=[TextMessage(text=text, quickReply=not_receipt_quick_reply(p))],
        )

    violations = check_bounds(extraction, today=today)
    if violations:
        # Dead end: no further buttons, so the uploaded image will never be
        # referenced by a written row — clean it up (see CleanupReply).
        reply = Reply(
            reply_token=reply_token,
            group_id=group_id,
            messages=[
                TextMessage(text=bounds_message(violations, extraction.quality_issue))
            ],
        )
        return CleanupReply(blob=blob, reply=reply)

    text = f"Pick a card — {summary_line(p)}"
    return Reply(
        reply_token=reply_token,
        group_id=group_id,
        messages=[TextMessage(text=text, quickReply=card_quick_reply(cards, p))],
    )
