import datetime

from app.schema import (
    BoundsViolation,
    QualityIssue,
    ReceiptExtraction,
    check_bounds,
    normalize_be_year,
)


def test_valid_extraction_constructs():
    extraction = ReceiptExtraction(
        is_receipt=True,
        date="2026-03-05",
        merchant="Big C",
        amount=199.5,
        last4="1234",
        details="",
    )

    assert extraction.is_receipt is True
    assert extraction.date == datetime.date(2026, 3, 5)
    assert extraction.amount == 199.5
    assert check_bounds(extraction, today=datetime.date(2026, 3, 6)) == []


def test_is_receipt_false_still_parses_best_effort_fields():
    extraction = ReceiptExtraction(is_receipt=False, merchant="unclear photo")

    assert extraction.is_receipt is False
    assert extraction.merchant == "unclear photo"
    assert extraction.amount is None
    assert extraction.date is None


def test_missing_is_receipt_defaults_false_instead_of_raising():
    extraction = ReceiptExtraction()

    assert extraction.is_receipt is False


def test_missing_amount_fails_bounds():
    extraction = ReceiptExtraction(is_receipt=True, date="2026-03-05")

    assert check_bounds(extraction, today=datetime.date(2026, 3, 6)) == [
        BoundsViolation.MISSING_AMOUNT
    ]


def test_amount_zero_or_negative_fails_bounds():
    extraction = ReceiptExtraction(is_receipt=True, date="2026-03-05", amount=0)

    assert BoundsViolation.AMOUNT_OUT_OF_RANGE in check_bounds(
        extraction, today=datetime.date(2026, 3, 6)
    )


def test_amount_too_large_fails_bounds():
    extraction = ReceiptExtraction(is_receipt=True, date="2026-03-05", amount=100_001)

    assert BoundsViolation.AMOUNT_OUT_OF_RANGE in check_bounds(
        extraction, today=datetime.date(2026, 3, 6)
    )


def test_missing_date_fails_bounds():
    extraction = ReceiptExtraction(is_receipt=True, amount=100)

    assert BoundsViolation.MISSING_DATE in check_bounds(
        extraction, today=datetime.date(2026, 3, 6)
    )


def test_date_too_far_in_past_fails_bounds():
    extraction = ReceiptExtraction(is_receipt=True, amount=100, date="2025-01-01")

    assert BoundsViolation.DATE_OUT_OF_RANGE in check_bounds(
        extraction, today=datetime.date(2026, 3, 6)
    )


def test_date_too_far_in_future_fails_bounds():
    extraction = ReceiptExtraction(is_receipt=True, amount=100, date="2026-03-10")

    assert BoundsViolation.DATE_OUT_OF_RANGE in check_bounds(
        extraction, today=datetime.date(2026, 3, 6)
    )


def test_normalize_be_year_converts_implausible_future_year():
    be_date = datetime.date(2569, 7, 11)

    assert normalize_be_year(be_date, today=datetime.date(2026, 7, 11)) == datetime.date(
        2026, 7, 11
    )


def test_normalize_be_year_leaves_plausible_near_future_date_alone():
    near_future = datetime.date(2027, 1, 1)

    assert normalize_be_year(near_future, today=datetime.date(2026, 7, 11)) == near_future


def test_normalize_be_year_leaves_garbled_year_alone():
    garbled = datetime.date(3012, 1, 1)

    result = normalize_be_year(garbled, today=datetime.date(2026, 7, 11))

    assert result == garbled  # stays visibly wrong, will fail bounds downstream


def test_be_year_validator_wired_into_construction():
    extraction = ReceiptExtraction(date="2569-07-11")

    assert extraction.date == datetime.date(2026, 7, 11)


def test_installment_total_round_trips_unchanged():
    extraction = ReceiptExtraction(
        is_receipt=True,
        date="2026-03-05",
        merchant="Power Buy",
        amount=12000,
        details="ผ่อน 10 เดือน",
    )

    assert extraction.amount == 12000
    assert extraction.details == "ผ่อน 10 เดือน"


def test_last4_coerces_int():
    extraction = ReceiptExtraction(last4=1234)

    assert extraction.last4 == "1234"


def test_last4_coerces_prefixed_string_to_last_four_digits():
    extraction = ReceiptExtraction(last4="XXXX-1234")

    assert extraction.last4 == "1234"


def test_amount_coerces_comma_and_baht_sign():
    extraction = ReceiptExtraction(amount="฿1,234.50")

    assert extraction.amount == 1234.5


def test_unparseable_date_becomes_none_not_a_validation_error():
    extraction = ReceiptExtraction(date="not a date")

    assert extraction.date is None


def test_quality_issue_defaults_to_none():
    extraction = ReceiptExtraction(is_receipt=True, date="2026-03-05", amount=100)

    assert extraction.quality_issue is None


def test_quality_issue_coerces_from_string():
    extraction = ReceiptExtraction(quality_issue="blur")

    assert extraction.quality_issue is QualityIssue.BLUR


def test_quality_issue_garbage_becomes_none_not_a_validation_error():
    extraction = ReceiptExtraction(quality_issue="not a real issue")

    assert extraction.quality_issue is None


def test_quality_issue_none_string_becomes_none():
    extraction = ReceiptExtraction(quality_issue="none")

    assert extraction.quality_issue is None


def test_old_six_key_dict_without_quality_issue_still_validates():
    extraction = ReceiptExtraction.model_validate(
        {
            "is_receipt": True,
            "date": "2026-03-05",
            "merchant": "Big C",
            "amount": 199.5,
            "last4": "1234",
            "details": "",
        }
    )

    assert extraction.quality_issue is None
