import datetime as dt
import logging
from unittest.mock import MagicMock

import pytest
from linebot.v3.webhooks import (
    ContentProvider,
    DeliveryContext,
    GroupSource,
    ImageMessageContent,
    MessageEvent,
    PostbackContent,
    PostbackEvent,
    StickerMessageContent,
    TextMessageContent,
    UserSource,
)

from app.buttons import (
    card_quick_reply,
    category_quick_reply,
    details_quick_reply,
    not_receipt_quick_reply,
)
from app.handlers import CleanupReply, Enqueue, handle_ocr_result, route_event
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
from app.reply import Reply
from app.schema import QualityIssue, ReceiptExtraction
from app.store import Card, TabNotFoundError

GROUP_ID = "Callowedgroupidxxxxxxxxxxxxxxxxxxx"


def _delivery_context() -> DeliveryContext:
    return DeliveryContext(isRedelivery=False)


def _image_event(
    *, group_id=GROUP_ID, message_id="msg-1", reply_token="tok-1", webhook_event_id="wh-1"
) -> MessageEvent:
    return MessageEvent(
        source=GroupSource(groupId=group_id, userId="U123"),
        timestamp=1234567890,
        mode="active",
        webhookEventId=webhook_event_id,
        deliveryContext=_delivery_context(),
        replyToken=reply_token,
        message=ImageMessageContent(
            id=message_id,
            contentProvider=ContentProvider(type="line"),
            quoteToken="qt-1",
        ),
    )


def _text_event(text, *, group_id=GROUP_ID, reply_token="tok-1") -> MessageEvent:
    return MessageEvent(
        source=GroupSource(groupId=group_id, userId="U123"),
        timestamp=1234567890,
        mode="active",
        webhookEventId="wh-2",
        deliveryContext=_delivery_context(),
        replyToken=reply_token,
        message=TextMessageContent(id="msg-text-1", text=text, quoteToken="qt-2"),
    )


def _sticker_event(*, group_id=GROUP_ID) -> MessageEvent:
    return MessageEvent(
        source=GroupSource(groupId=group_id, userId="U123"),
        timestamp=1234567890,
        mode="active",
        webhookEventId="wh-3",
        deliveryContext=_delivery_context(),
        replyToken="tok-3",
        message=StickerMessageContent(
            id="sticker-1",
            packageId="1",
            stickerId="1",
            stickerResourceType="STATIC",
            quoteToken="qt-4",
        ),
    )


def _postback_event(data, *, group_id=GROUP_ID, reply_token="tok-4") -> PostbackEvent:
    return PostbackEvent(
        source=GroupSource(groupId=group_id, userId="U123"),
        timestamp=1234567890,
        mode="active",
        webhookEventId="wh-4",
        deliveryContext=_delivery_context(),
        replyToken=reply_token,
        postback=PostbackContent(data=data),
    )


def _dm_event() -> MessageEvent:
    return MessageEvent(
        source=UserSource(userId="U999"),
        timestamp=1234567890,
        mode="active",
        webhookEventId="wh-5",
        deliveryContext=_delivery_context(),
        replyToken="tok-5",
        message=TextMessageContent(id="msg-dm-1", text="hi", quoteToken="qt-3"),
    )


def _payload(**overrides) -> Payload:
    defaults = dict(
        step=STEP_CARD,
        message_id="msg-1",
        blob="202607_msg-1.jpg",
        sender="Uoriginal-sender",
        ocr_model="claude",
        date=dt.date(2026, 7, 12),
        amount=3974.50,
        last4="5449",
        merchant="BIG C SUPERCENTER",
        details="",
    )
    defaults.update(overrides)
    return Payload(**defaults)


def _cards() -> list[Card]:
    return [Card(card_id="Card_A1", bank="Bank A", card_name="Plat", last4="1234", expiry="12/27")]


# --- allowlist ---


def test_unknown_group_rejected_and_group_id_logged(monkeypatch, caplog):
    monkeypatch.setenv("ALLOWED_GROUP_ID", GROUP_ID)
    store = MagicMock()
    event = _image_event(group_id="Csomeothergroup")

    with caplog.at_level(logging.WARNING):
        result = route_event(event, store)

    assert result is None
    assert "Csomeothergroup" in caplog.text


def test_empty_allowlist_rejects_and_logs_group_id(monkeypatch, caplog):
    monkeypatch.delenv("ALLOWED_GROUP_ID", raising=False)
    store = MagicMock()
    event = _image_event(group_id="Cdiscoverme")

    with caplog.at_level(logging.WARNING):
        result = route_event(event, store)

    assert result is None
    assert "Cdiscoverme" in caplog.text


def test_dm_source_ignored(monkeypatch):
    monkeypatch.setenv("ALLOWED_GROUP_ID", GROUP_ID)
    store = MagicMock()

    assert route_event(_dm_event(), store) is None


# --- trigger 1: image ---


def test_image_in_allowed_group_returns_enqueue(monkeypatch):
    monkeypatch.setenv("ALLOWED_GROUP_ID", GROUP_ID)
    store = MagicMock()
    event = _image_event(message_id="msg-42", reply_token="tok-42", webhook_event_id="wh-42")

    result = route_event(event, store)

    assert isinstance(result, Enqueue)
    assert result.message_id == "msg-42"
    assert result.webhook_event_id == "wh-42"
    assert result.reply_token == "tok-42"
    assert result.group_id == GROUP_ID
    assert result.user_id == "U123"


# --- silently ignored message types ---


def test_sticker_ignored(monkeypatch):
    monkeypatch.setenv("ALLOWED_GROUP_ID", GROUP_ID)
    store = MagicMock()

    assert route_event(_sticker_event(), store) is None


def test_plain_text_ignored(monkeypatch):
    monkeypatch.setenv("ALLOWED_GROUP_ID", GROUP_ID)
    store = MagicMock()

    assert route_event(_text_event("just chatting"), store) is None


# --- trigger 2: card tapped ---


def test_card_postback_returns_category_buttons(monkeypatch):
    monkeypatch.setenv("ALLOWED_GROUP_ID", GROUP_ID)
    store = MagicMock()
    p = _payload(step=STEP_CARD, card_id="Card_A1")
    event = _postback_event(encode(p), reply_token="tok-card")

    result = route_event(event, store)

    assert isinstance(result, Reply)
    assert result.reply_token == "tok-card"
    text_msg = result.messages[0]
    assert "BIG C SUPERCENTER" in text_msg.text
    assert "3,974.50" in text_msg.text
    assert text_msg.quick_reply is not None
    store.append_receipt.assert_not_called()


# --- category tapped -> details step, no write ---


def test_category_postback_returns_details_buttons_no_write(monkeypatch):
    monkeypatch.setenv("ALLOWED_GROUP_ID", GROUP_ID)
    store = MagicMock()
    p = _payload(step=STEP_CATEGORY, card_id="Card_A1", category="grocery")
    event = _postback_event(encode(p))

    result = route_event(event, store)

    assert isinstance(result, Reply)
    assert "Add details" in result.messages[0].text
    assert result.messages[0].quick_reply is not None
    store.append_receipt.assert_not_called()


# --- skip -> writes with OCR details ---


def test_skip_writes_row_with_ocr_details(monkeypatch):
    monkeypatch.setenv("ALLOWED_GROUP_ID", GROUP_ID)
    monkeypatch.setenv("GCS_BUCKET", "my-bucket")
    store = MagicMock()
    p = _payload(
        step=STEP_SKIP,
        card_id="Card_A1",
        category="grocery",
        details="original ocr details",
        sender="Uoriginal-sender",
    )
    event = _postback_event(encode(p))

    result = route_event(event, store)

    store.append_receipt.assert_called_once()
    row = store.append_receipt.call_args[0][0]
    assert row.card_id == "Card_A1"
    assert row.category == "grocery"
    assert row.details == "original ocr details"
    assert row.receipt_link == "https://storage.cloud.google.com/my-bucket/202607_msg-1.jpg"
    assert row.submitted_by == "Uoriginal-sender"
    assert row.message_id == "msg-1"
    assert isinstance(result, Reply)
    assert "✓" in result.messages[0].text


def test_skip_duplicate_write_replies_already_recorded(monkeypatch):
    monkeypatch.setenv("ALLOWED_GROUP_ID", GROUP_ID)
    monkeypatch.setenv("GCS_BUCKET", "my-bucket")
    store = MagicMock()
    store.append_receipt.return_value = False
    p = _payload(step=STEP_SKIP, card_id="Card_A1", category="grocery")
    event = _postback_event(encode(p))

    result = route_event(event, store)

    assert isinstance(result, Reply)
    assert "Already recorded" in result.messages[0].text


def test_skip_with_missing_required_field_replies_failed_to_record(monkeypatch):
    # A corrupted fill-in/postback payload (e.g. date blanked out) must fail loudly
    # with a reply, not raise an unhandled ValidationError past the reply token.
    monkeypatch.setenv("ALLOWED_GROUP_ID", GROUP_ID)
    monkeypatch.setenv("GCS_BUCKET", "my-bucket")
    store = MagicMock()
    p = _payload(step=STEP_SKIP, card_id="Card_A1", category="grocery", date=None)
    event = _postback_event(encode(p))

    result = route_event(event, store)

    assert isinstance(result, Reply)
    assert "Failed to record" in result.messages[0].text
    store.append_receipt.assert_not_called()


def test_details_step_postback_is_noop(monkeypatch):
    monkeypatch.setenv("ALLOWED_GROUP_ID", GROUP_ID)
    store = MagicMock()
    p = _payload(step=STEP_DETAILS, card_id="Card_A1", category="grocery")
    event = _postback_event(encode(p))

    result = route_event(event, store)

    assert result is None
    store.append_receipt.assert_not_called()


# --- typed details via fill-in text ---


def test_typed_details_text_writes_row(monkeypatch):
    monkeypatch.setenv("ALLOWED_GROUP_ID", GROUP_ID)
    monkeypatch.setenv("GCS_BUCKET", "my-bucket")
    store = MagicMock()
    p = _payload(step=STEP_DETAILS, card_id="Card_A1", category="grocery", details=None)
    fill_in_text = encode_fill_in(p) + "birthday gift for mom"
    event = _text_event(fill_in_text)

    result = route_event(event, store)

    store.append_receipt.assert_called_once()
    row = store.append_receipt.call_args[0][0]
    assert row.details == "birthday gift for mom"
    assert isinstance(result, Reply)
    assert "✓" in result.messages[0].text


def test_mangled_fill_in_text_returns_error_reply_no_write(monkeypatch):
    monkeypatch.setenv("ALLOWED_GROUP_ID", GROUP_ID)
    store = MagicMock()
    # Starts with the real fill-in signature ("#v1|") but the payload is corrupt —
    # the user mangled the prefilled text before sending.
    event = _text_event("#v1|not-a-valid-payload\nsome typed text")

    result = route_event(event, store)

    assert isinstance(result, Reply)
    store.append_receipt.assert_not_called()
    assert "Couldn't match" in result.messages[0].text


def test_plain_hashtag_text_ignored_silently(monkeypatch):
    # "#" alone is not a fill-in — ordinary chat hashtags must not trigger an error
    # reply in the group.
    monkeypatch.setenv("ALLOWED_GROUP_ID", GROUP_ID)
    store = MagicMock()
    event = _text_event("#dinner was great")

    result = route_event(event, store)

    assert result is None
    store.append_receipt.assert_not_called()


# --- write-step error handling ---


def test_tab_not_found_error_returns_error_reply_and_logs_error(monkeypatch, caplog):
    monkeypatch.setenv("ALLOWED_GROUP_ID", GROUP_ID)
    monkeypatch.setenv("GCS_BUCKET", "my-bucket")
    store = MagicMock()
    store.append_receipt.side_effect = TabNotFoundError("no tab named Card_Z9")
    p = _payload(step=STEP_SKIP, card_id="Card_Z9", category="grocery")
    event = _postback_event(encode(p))

    with caplog.at_level(logging.ERROR):
        result = route_event(event, store)

    assert isinstance(result, Reply)
    assert "Failed to record" in result.messages[0].text
    assert "✓" not in result.messages[0].text
    assert "msg-1" in caplog.text


# --- process anyway ---


def test_process_anyway_valid_returns_card_buttons(monkeypatch):
    monkeypatch.setenv("ALLOWED_GROUP_ID", GROUP_ID)
    store = MagicMock()
    store.read_cards.return_value = _cards()
    p = _payload(step=STEP_PROCESS_ANYWAY)
    event = _postback_event(encode(p))

    result = route_event(event, store)

    assert isinstance(result, Reply)
    store.read_cards.assert_called_once()
    assert result.messages[0].quick_reply is not None


def test_process_anyway_missing_amount_is_honest_dead_end_no_cards_read(monkeypatch):
    monkeypatch.setenv("ALLOWED_GROUP_ID", GROUP_ID)
    store = MagicMock()
    p = _payload(step=STEP_PROCESS_ANYWAY, amount=None)
    event = _postback_event(encode(p))

    result = route_event(event, store)

    # A dead end (resending the same image would OCR identically): CleanupReply, not
    # a plain Reply, so the caller cleans up the orphaned blob too.
    assert isinstance(result, CleanupReply)
    assert result.blob == p.blob
    store.read_cards.assert_not_called()
    assert "Can't record" in result.reply.messages[0].text
    # Resending the same image would OCR identically — no false "resend" hope.
    assert "resend" not in result.reply.messages[0].text.lower()
    assert result.reply.messages[0].quick_reply is None


# --- cancel at every step ---


@pytest.mark.parametrize(
    "build_quick_reply",
    [
        lambda p: card_quick_reply(_cards(), p),
        lambda p: category_quick_reply(p),
        lambda p: details_quick_reply(p),
        lambda p: not_receipt_quick_reply(p),
    ],
    ids=["from-card-set", "from-category-set", "from-details-set", "from-not-receipt-set"],
)
def test_cancel_from_every_button_set_ends_flow_without_writing(monkeypatch, build_quick_reply):
    monkeypatch.setenv("ALLOWED_GROUP_ID", GROUP_ID)
    store = MagicMock()
    p = _payload(card_id="Card_A1", category="grocery")
    qr = build_quick_reply(p)
    cancel_data = qr.items[-1].action.data
    assert qr.items[-1].action.label == "Cancel"
    event = _postback_event(cancel_data)

    result = route_event(event, store)

    assert isinstance(result, CleanupReply)
    assert result.blob == p.blob
    assert "Cancelled" in result.reply.messages[0].text
    store.append_receipt.assert_not_called()


# --- corrupt / unknown postback data ---


def test_corrupt_postback_data_returns_none_and_logs_warning(monkeypatch, caplog):
    monkeypatch.setenv("ALLOWED_GROUP_ID", GROUP_ID)
    store = MagicMock()
    event = _postback_event("this-is-not-a-valid-payload")

    with caplog.at_level(logging.WARNING):
        result = route_event(event, store)

    assert result is None
    assert caplog.text


def test_unknown_step_returns_none_and_logs_warning(monkeypatch, caplog):
    monkeypatch.setenv("ALLOWED_GROUP_ID", GROUP_ID)
    store = MagicMock()
    p = _payload(step="bogus-step")
    event = _postback_event(encode(p))

    with caplog.at_level(logging.WARNING):
        result = route_event(event, store)

    assert result is None
    store.append_receipt.assert_not_called()


# --- handle_ocr_result ---


def test_handle_ocr_result_not_a_receipt_skips_bounds_check():
    extraction = ReceiptExtraction(is_receipt=False, merchant="Unknown", amount=None)

    reply = handle_ocr_result(
        extraction,
        message_id="msg-1",
        blob="202607_msg-1.jpg",
        sender="U1",
        ocr_model="claude",
        group_id=GROUP_ID,
        reply_token="tok-1",
        cards=_cards(),
    )

    assert "Doesn't look like a receipt" in reply.messages[0].text
    assert reply.messages[0].quick_reply is not None


def test_handle_ocr_result_bounds_violation_replies_cannot_read():
    extraction = ReceiptExtraction(
        is_receipt=True, date=dt.date(2020, 1, 1), merchant="X", amount=100.0
    )

    result = handle_ocr_result(
        extraction,
        message_id="msg-1",
        blob="202607_msg-1.jpg",
        sender="U1",
        ocr_model="claude",
        group_id=GROUP_ID,
        reply_token="tok-1",
        cards=_cards(),
        today=dt.date(2026, 7, 12),
    )

    # Dead end (no card buttons ever follow this reply): CleanupReply, not a plain
    # Reply, so the caller (worker_main) deletes the now-unreferenceable blob.
    assert isinstance(result, CleanupReply)
    assert result.blob == "202607_msg-1.jpg"
    assert "Cannot read" in result.reply.messages[0].text
    assert result.reply.messages[0].quick_reply is None


def test_handle_ocr_result_valid_returns_card_buttons():
    extraction = ReceiptExtraction(
        is_receipt=True,
        date=dt.date(2026, 7, 10),
        merchant="Big C",
        amount=100.0,
        last4="1234",
    )

    reply = handle_ocr_result(
        extraction,
        message_id="msg-1",
        blob="202607_msg-1.jpg",
        sender="U1",
        ocr_model="claude",
        group_id=GROUP_ID,
        reply_token="tok-1",
        cards=_cards(),
        today=dt.date(2026, 7, 12),
    )

    assert "Pick a card" in reply.messages[0].text
    assert reply.messages[0].quick_reply is not None


def test_handle_ocr_result_bounds_violation_with_quality_issue_adds_targeted_tip():
    extraction = ReceiptExtraction(
        is_receipt=True, date=None, merchant="X", amount=100.0, quality_issue=QualityIssue.DARK
    )

    result = handle_ocr_result(
        extraction,
        message_id="msg-1",
        blob="202607_msg-1.jpg",
        sender="U1",
        ocr_model="claude",
        group_id=GROUP_ID,
        reply_token="tok-1",
        cards=_cards(),
        today=dt.date(2026, 7, 12),
    )

    assert isinstance(result, CleanupReply)
    assert "too dark" in result.reply.messages[0].text


def test_handle_ocr_result_not_a_receipt_ignores_quality_issue():
    extraction = ReceiptExtraction(
        is_receipt=False, merchant="Unknown", amount=None, quality_issue=QualityIssue.BLUR
    )

    reply = handle_ocr_result(
        extraction,
        message_id="msg-1",
        blob="202607_msg-1.jpg",
        sender="U1",
        ocr_model="claude",
        group_id=GROUP_ID,
        reply_token="tok-1",
        cards=_cards(),
    )

    assert "Doesn't look like a receipt" in reply.messages[0].text
    assert reply.messages[0].quick_reply is not None


def test_handle_ocr_result_valid_extraction_ignores_quality_issue():
    extraction = ReceiptExtraction(
        is_receipt=True,
        date=dt.date(2026, 7, 10),
        merchant="Big C",
        amount=100.0,
        last4="1234",
        quality_issue=QualityIssue.GLARE,
    )

    reply = handle_ocr_result(
        extraction,
        message_id="msg-1",
        blob="202607_msg-1.jpg",
        sender="U1",
        ocr_model="claude",
        group_id=GROUP_ID,
        reply_token="tok-1",
        cards=_cards(),
        today=dt.date(2026, 7, 12),
    )

    assert "Pick a card" in reply.messages[0].text
