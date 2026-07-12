import datetime as dt

import pytest

from app.payload import (
    MAX_DATA_LEN,
    STEP_CARD,
    STEP_CATEGORY,
    Payload,
    PayloadError,
    decode,
    decode_fill_in,
    encode,
    encode_fill_in,
)


def _payload(**overrides) -> Payload:
    defaults = dict(
        step=STEP_CARD,
        message_id="597123456789012345",
        blob="202607_597123456789012345.jpg",
        sender="U" + "a" * 32,
        ocr_model="claude",
        date=dt.date(2026, 7, 12),
        amount=3974.50,
        last4="5449",
        card_id="Card_A1",
        category="grocery",
        merchant="BIG C SUPERCENTER MEGA BANGNA",
        details="installment 10 months",
    )
    defaults.update(overrides)
    return Payload(**defaults)


def test_round_trip_all_fields_populated():
    p = _payload()
    decoded = decode(encode(p))

    assert decoded.step == p.step
    assert decoded.message_id == p.message_id
    assert decoded.blob == p.blob
    assert decoded.sender == p.sender
    assert decoded.ocr_model == p.ocr_model
    assert decoded.date == p.date
    assert decoded.amount == pytest.approx(p.amount)
    assert decoded.last4 == p.last4
    assert decoded.card_id == p.card_id
    assert decoded.category == p.category
    assert decoded.merchant == p.merchant
    assert decoded.details == p.details


def test_round_trip_thai_merchant_and_details():
    p = _payload(
        merchant="บิ๊กซี ซูเปอร์เซ็นเตอร์",
        details="ผ่อน 10 เดือน 0%",
    )
    decoded = decode(encode(p))

    assert decoded.merchant == p.merchant
    assert decoded.details == p.details


def test_round_trip_none_fields():
    p = _payload(date=None, amount=None, last4=None, card_id=None, category=None, merchant=None, details=None)
    decoded = decode(encode(p))

    assert decoded.date is None
    assert decoded.amount is None
    assert decoded.last4 is None
    assert decoded.card_id is None
    assert decoded.category is None
    assert decoded.merchant is None
    assert decoded.details is None


def test_pipe_and_percent_and_newline_in_fields_round_trip():
    p = _payload(
        merchant="A|B%C",
        details="line1\nline2\ttabbed\r\nend",
    )
    decoded = decode(encode(p))

    assert decoded.merchant == "A|B%C"
    # \r\n\t become spaces during escaping — this is expected lossy normalization
    assert "\n" not in decoded.details
    assert "\t" not in decoded.details
    assert "\r" not in decoded.details


def test_details_truncated_to_60_chars():
    p = _payload(details="x" * 100)
    decoded = decode(encode(p))

    assert decoded.details == "x" * 60


def test_merchant_truncated_to_40_chars():
    p = _payload(merchant="y" * 100)
    decoded = decode(encode(p))

    assert decoded.merchant == "y" * 40


def test_worst_case_payload_fits_within_300_chars():
    p = _payload(
        step=STEP_CATEGORY,
        merchant="y" * 40,
        details="x" * 60,
        category="eating out",
    )
    out = encode(p)

    assert len(out) <= MAX_DATA_LEN


def test_escape_bomb_still_fits_within_300_chars():
    # every char needs escaping -> triggers the trim loop
    p = _payload(merchant="|" * 40, details="%" * 60)
    out = encode(p)

    assert len(out) <= MAX_DATA_LEN
    decoded = decode(out)
    assert set(decoded.merchant) <= {"|"}
    assert set(decoded.details) <= {"%"}


def test_decode_rejects_wrong_version():
    with pytest.raises(PayloadError):
        decode("v0|card|msg|blob|sender|ocr|||||||")


def test_decode_rejects_wrong_field_count():
    with pytest.raises(PayloadError):
        decode("v1|card|msg")


def test_decode_rejects_garbage_amount():
    bad = "v1|card|msg|blob|sender|ocr||not-a-number|||||"
    with pytest.raises(PayloadError):
        decode(bad)


def test_decode_rejects_garbage_date():
    bad = "v1|card|msg|blob|sender|ocr|not-a-date||||||"
    with pytest.raises(PayloadError):
        decode(bad)


def test_encode_decode_fill_in_round_trip():
    p = _payload(details=None)
    text = encode_fill_in(p) + "birthday gift for mom"

    decoded_p, typed = decode_fill_in(text)

    assert decoded_p.merchant == p.merchant
    assert decoded_p.card_id == p.card_id
    assert typed == "birthday gift for mom"


def test_fill_in_typed_details_truncated_to_30_chars():
    p = _payload(details=None)
    text = encode_fill_in(p) + ("z" * 50)

    _, typed = decode_fill_in(text)

    assert typed == "z" * 30


def test_fill_in_typed_details_with_pipes_and_thai():
    p = _payload(details=None)
    text = encode_fill_in(p) + "ของขวัญ | วันเกิด"

    _, typed = decode_fill_in(text)

    assert typed == "ของขวัญ | วันเกิด"


def test_decode_fill_in_rejects_text_without_prefix():
    with pytest.raises(PayloadError):
        decode_fill_in("just a plain message")


def test_decode_fill_in_rejects_corrupt_payload():
    with pytest.raises(PayloadError):
        decode_fill_in("#not-a-valid-payload\nsome typed text")
