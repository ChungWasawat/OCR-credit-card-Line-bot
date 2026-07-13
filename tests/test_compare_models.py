from scripts.compare_models import (
    amount_match,
    date_match,
    is_receipt_match,
    merchant_match,
    normalize_merchant,
    score_rows,
    _summarize_usage,
)


def test_normalize_merchant_casefolds_and_strips_punctuation():
    assert normalize_merchant("Big-C, Supercenter!") == "bigc supercenter"


def test_normalize_merchant_collapses_whitespace():
    assert normalize_merchant("Big   C   Supercenter") == "big c supercenter"


def test_merchant_match_case_and_whitespace_insensitive():
    assert merchant_match("BIG C SUPERCENTER", "big c   supercenter")


def test_merchant_match_substring_either_direction():
    assert merchant_match("Big C", "Big C Supercenter PCL")
    assert merchant_match("Big C Supercenter PCL", "Big C")


def test_merchant_match_ratio_boundary():
    # "Big C Supercenter" vs "Big D Supercenter" — one character off, high ratio.
    assert merchant_match("Big C Supercenter", "Big D Supercenter")
    # Genuinely different merchants must not match.
    assert not merchant_match("Big C Supercenter", "7-Eleven")


def test_merchant_match_both_null_is_correct():
    assert merchant_match("", "")
    assert merchant_match(None, None)


def test_merchant_match_one_null_is_wrong():
    assert not merchant_match("Big C", "")
    assert not merchant_match("", "Big C")


def test_amount_match_exact():
    assert amount_match("199.50", "199.50")


def test_amount_match_within_tolerance():
    assert amount_match("199.50", "199.505")


def test_amount_match_outside_tolerance():
    assert not amount_match("199.50", "199.60")


def test_amount_match_both_null_is_correct():
    assert amount_match("", "")


def test_amount_match_one_null_is_wrong():
    assert not amount_match("199.50", "")
    assert not amount_match("", "199.50")


def test_amount_match_unparseable_treated_as_null():
    assert amount_match("not a number", "")


def test_date_match_exact():
    assert date_match("2026-07-10", "2026-07-10")
    assert not date_match("2026-07-10", "2026-07-11")


def test_date_match_both_null_is_correct():
    assert date_match("", "")


def test_is_receipt_match_accepts_common_truthy_forms():
    assert is_receipt_match("True", "true")
    assert is_receipt_match("1", "yes")
    assert not is_receipt_match("False", "True")


def test_summarize_usage_empty_calls():
    assert _summarize_usage([]) == ("", "", "", "")


def test_summarize_usage_priced_model():
    usage_detail, in_tok, out_tok, cost = _summarize_usage([("claude-haiku-4-5", 1000, 200)])
    assert usage_detail == "claude-haiku-4-5:1000/200"
    assert in_tok == "1000"
    assert out_tok == "200"
    assert cost == "0.002000"  # 1000/1e6*1.0 + 200/1e6*5.0


def test_summarize_usage_free_tier_model_is_zero_cost_no_prefix():
    _, _, _, cost = _summarize_usage([("gemini-3.1-flash-lite", 500, 100)])
    assert cost == "0.000000"


def test_summarize_usage_unpriced_model_gets_lower_bound_prefix():
    _, _, _, cost = _summarize_usage([("typhoon-ocr", 500, 100)])
    assert cost.startswith(">=")


def test_summarize_usage_missing_tokens_degrades_gracefully():
    usage_detail, in_tok, out_tok, cost = _summarize_usage([("typhoon-ocr", None, None)])
    assert usage_detail == "typhoon-ocr:?/?"
    assert in_tok == ""
    assert out_tok == ""
    assert cost.startswith(">=")


def test_summarize_usage_multi_step_provider_sums_across_calls():
    calls = [("typhoon-ocr", 1000, 500), ("claude-haiku-4-5", 800, 150)]
    usage_detail, in_tok, out_tok, cost = _summarize_usage(calls)
    assert usage_detail == "typhoon-ocr:1000/500; claude-haiku-4-5:800/150"
    assert in_tok == "1800"
    assert out_tok == "650"
    # typhoon-ocr unpriced -> lower-bound prefix, only claude leg counted
    assert cost == ">=0.001550"


# --- score_rows: end-to-end over small in-memory CSV-shaped dicts ---


def _truth_row(**overrides) -> dict:
    row = {
        "filename": "r1.jpg", "kind": "receipt", "is_receipt": "True",
        "date": "2026-07-10", "merchant": "Big C", "amount": "199.50", "last4": "1234",
    }
    row.update(overrides)
    return row


def _result_row(**overrides) -> dict:
    row = {
        "filename": "r1.jpg", "model": "claude", "status": "ok", "error": "",
        "latency_s": "1.50", "is_receipt": "True", "date": "2026-07-10",
        "merchant": "Big C", "amount": "199.50", "last4": "1234", "details": "",
        "quality_issue": "", "est_cost_usd": "0.002000",
    }
    row.update(overrides)
    return row


def test_score_rows_all_correct_gives_100_percent():
    summary, mismatches = score_rows([_result_row()], [_truth_row()])
    assert summary["claude"]["is_receipt_acc"] == 1.0
    assert summary["claude"]["date_acc"] == 1.0
    assert summary["claude"]["amount_acc"] == 1.0
    assert summary["claude"]["merchant_acc"] == 1.0
    assert mismatches == []


def test_score_rows_wrong_amount_recorded_as_mismatch():
    result = _result_row(amount="250.00")
    summary, mismatches = score_rows([result], [_truth_row()])
    assert summary["claude"]["amount_acc"] == 0.0
    assert any(m["field"] == "amount" for m in mismatches)


def test_score_rows_error_status_counts_as_is_receipt_wrong_and_skips_field_scoring():
    result = _result_row(status="error", error="OcrParseError: bad json", is_receipt="", date="", amount="", merchant="")
    summary, mismatches = score_rows([result], [_truth_row()])
    assert summary["claude"]["n_error"] == 1
    assert summary["claude"]["n_ok"] == 0
    assert summary["claude"]["is_receipt_acc"] == 0.0
    # error cells excluded from field-accuracy denominators, not treated as wrong there
    assert summary["claude"]["date_acc"] is None
    assert any(m["field"] == "is_receipt" and "ERROR" in m["predicted"] for m in mismatches)


def test_score_rows_non_receipt_kind_excluded_from_field_scoring():
    truth = _truth_row(kind="non_receipt", is_receipt="False", date="", merchant="", amount="")
    result = _result_row(is_receipt="False", date="", merchant="", amount="")
    summary, _ = score_rows([result], [truth])
    assert summary["claude"]["is_receipt_acc"] == 1.0
    assert summary["claude"]["date_acc"] is None
    assert summary["claude"]["amount_acc"] is None
    assert summary["claude"]["merchant_acc"] is None


def test_score_rows_missing_result_for_a_model_is_skipped_not_counted():
    # ground truth has 2 files, but claude only ran on one
    truths = [_truth_row(filename="r1.jpg"), _truth_row(filename="r2.jpg")]
    results = [_result_row(filename="r1.jpg")]
    summary, _ = score_rows(results, truths)
    assert summary["claude"]["is_receipt_acc"] == 1.0  # only r1 counted, not r2


def test_score_rows_last_row_wins_on_duplicate_key():
    first = _result_row(amount="1.00")
    second = _result_row(amount="199.50")  # correct, appended later (e.g. a retry)
    summary, _ = score_rows([first, second], [_truth_row()])
    assert summary["claude"]["amount_acc"] == 1.0


def test_score_rows_total_cost_sums_and_flags_partial():
    result = _result_row(est_cost_usd=">=0.001550")
    summary, _ = score_rows([result], [_truth_row()])
    assert summary["claude"]["cost_partial"] is True
    assert summary["claude"]["total_est_cost_usd"] == 0.001550
