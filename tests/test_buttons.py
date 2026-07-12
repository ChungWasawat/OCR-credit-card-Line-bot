import datetime as dt

from app.buttons import (
    CANCEL_LABEL,
    CATEGORIES,
    MAX_CARD_ITEMS,
    PROCESS_ANYWAY_LABEL,
    SKIP_LABEL,
    TYPE_DETAILS_LABEL,
    bounds_message,
    card_quick_reply,
    category_quick_reply,
    details_quick_reply,
    not_receipt_quick_reply,
    process_anyway_blocked_message,
    summary_line,
)
from app.payload import (
    STEP_CANCEL,
    STEP_CARD,
    STEP_CATEGORY,
    STEP_DETAILS,
    STEP_PROCESS_ANYWAY,
    STEP_SKIP,
    Payload,
    decode,
)
from app.schema import BoundsViolation
from app.store import Card


def _payload(**overrides) -> Payload:
    defaults = dict(
        step=STEP_CARD,
        message_id="msg-1",
        blob="202607_msg-1.jpg",
        sender="U123",
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
    return [
        Card(card_id="Card_A1", bank="Bank A", card_name="Plat", last4="1234", expiry="12/27"),
        Card(card_id="Card_A2", bank="Bank A", card_name="Gold", last4="5449", expiry="01/28"),
        Card(card_id="Card_B1", bank="Bank B", card_name="Cash", last4="9012", expiry="02/29"),
    ]


def test_card_quick_reply_orders_matched_last4_first():
    p = _payload(last4="5449")
    qr = card_quick_reply(_cards(), p)

    card_items = qr.items[:-1]
    decoded_card_ids = [decode(item.action.data).card_id for item in card_items]
    assert decoded_card_ids[0] == "Card_A2"
    assert set(decoded_card_ids) == {"Card_A1", "Card_A2", "Card_B1"}


def test_card_quick_reply_no_match_keeps_original_order():
    p = _payload(last4="0000")
    qr = card_quick_reply(_cards(), p)

    card_items = qr.items[:-1]
    decoded_card_ids = [decode(item.action.data).card_id for item in card_items]
    assert decoded_card_ids == ["Card_A1", "Card_A2", "Card_B1"]


def test_card_quick_reply_cancel_is_last():
    p = _payload()
    qr = card_quick_reply(_cards(), p)

    last = qr.items[-1]
    assert last.action.label == CANCEL_LABEL
    decoded = decode(last.action.data)
    assert decoded.step == STEP_CANCEL


def test_card_quick_reply_caps_cards_at_line_item_limit():
    # 13-item Line quick-reply cap: at most MAX_CARD_ITEMS cards + the Cancel item.
    # The OCR-matched card must survive the cut even if it sorts from the far end.
    many = [
        Card(card_id=f"Card_{i}", bank="Bank", card_name="X", last4=f"{i:04d}", expiry="12/27")
        for i in range(15)
    ]
    p = _payload(last4="0014")

    qr = card_quick_reply(many, p)

    assert len(qr.items) == MAX_CARD_ITEMS + 1
    assert qr.items[-1].action.label == CANCEL_LABEL
    assert decode(qr.items[0].action.data).card_id == "Card_14"


def test_card_quick_reply_item_data_decodes_with_right_step_and_card():
    p = _payload()
    qr = card_quick_reply(_cards(), p)

    for item in qr.items[:-1]:
        decoded = decode(item.action.data)
        assert decoded.step == STEP_CARD
        assert decoded.card_id is not None


def test_card_quick_reply_labels_within_20_chars():
    p = _payload()
    qr = card_quick_reply(_cards(), p)

    for item in qr.items:
        assert len(item.action.label) <= 20


def test_category_quick_reply_matches_categories_plus_cancel():
    p = _payload(step=STEP_CARD, card_id="Card_A1")
    qr = category_quick_reply(p)

    labels = [item.action.label for item in qr.items]
    assert labels == CATEGORIES + [CANCEL_LABEL]


def test_category_quick_reply_item_data_carries_category_and_card():
    p = _payload(card_id="Card_A1")
    qr = category_quick_reply(p)

    for item, expected_category in zip(qr.items[:-1], CATEGORIES):
        decoded = decode(item.action.data)
        assert decoded.step == STEP_CATEGORY
        assert decoded.category == expected_category
        assert decoded.card_id == "Card_A1"


def test_details_quick_reply_is_skip_type_details_cancel():
    p = _payload(card_id="Card_A1", category="grocery")
    qr = details_quick_reply(p)

    labels = [item.action.label for item in qr.items]
    assert labels == [SKIP_LABEL, TYPE_DETAILS_LABEL, CANCEL_LABEL]


def test_details_quick_reply_skip_item_decodes_to_skip_step():
    p = _payload(card_id="Card_A1", category="grocery")
    qr = details_quick_reply(p)

    skip_item = qr.items[0]
    decoded = decode(skip_item.action.data)
    assert decoded.step == STEP_SKIP
    assert decoded.category == "grocery"


def test_details_quick_reply_type_details_item_has_fill_in_text():
    p = _payload(card_id="Card_A1", category="grocery")
    qr = details_quick_reply(p)

    type_item = qr.items[1]
    assert type_item.action.input_option == "openKeyboard"
    assert type_item.action.fill_in_text is not None
    assert type_item.action.fill_in_text.startswith("#")
    decoded = decode(type_item.action.data)
    assert decoded.step == STEP_DETAILS


def test_not_receipt_quick_reply_is_process_anyway_and_cancel():
    p = _payload()
    qr = not_receipt_quick_reply(p)

    labels = [item.action.label for item in qr.items]
    assert labels == [PROCESS_ANYWAY_LABEL, CANCEL_LABEL]

    decoded = decode(qr.items[0].action.data)
    assert decoded.step == STEP_PROCESS_ANYWAY


def test_summary_line_contains_merchant_and_amount():
    p = _payload(merchant="BIG C SUPERCENTER", amount=3974.50)
    line = summary_line(p)

    assert "BIG C SUPERCENTER" in line
    assert "3,974.50" in line


def test_summary_line_handles_missing_fields():
    p = _payload(merchant=None, amount=None)
    line = summary_line(p)

    assert "?" in line


def test_bounds_message_covers_each_violation_with_readable_text():
    for violation in BoundsViolation:
        msg = bounds_message([violation])
        assert violation.value not in msg  # translated, not the raw code
        assert len(msg) > 0


def test_process_anyway_blocked_message_does_not_suggest_resending():
    # Resending the same image OCRs identically — the message must be an honest dead
    # end, unlike bounds_message's "please resend".
    for violation in BoundsViolation:
        msg = process_anyway_blocked_message([violation])
        assert "resend" not in msg.lower()
        assert violation.value not in msg
        assert len(msg) > 0
