from types import SimpleNamespace

import pytest

from app.ocr.base import (
    EXTRACTION_RULES,
    OcrContentError,
    OcrImageError,
    OcrParseError,
    blank_extraction,
    detect_mime,
    first_text_block,
    parse_llm_json,
)
from app.schema import ReceiptExtraction


def test_parse_llm_json_clean():
    assert parse_llm_json('{"is_receipt": true}') == {"is_receipt": True}


def test_parse_llm_json_fenced():
    text = '```json\n{"is_receipt": true}\n```'
    assert parse_llm_json(text) == {"is_receipt": True}


def test_parse_llm_json_preamble_and_fence():
    text = 'Here is the JSON:\n```json\n{"is_receipt": true}\n```'
    assert parse_llm_json(text) == {"is_receipt": True}


def test_parse_llm_json_trailing_prose():
    text = '{"is_receipt": true}\nLet me know if you need anything else.'
    assert parse_llm_json(text) == {"is_receipt": True}


def test_parse_llm_json_garbage_raises():
    with pytest.raises(OcrParseError):
        parse_llm_json("not json at all")


def test_parse_llm_json_top_level_null_raises():
    with pytest.raises(OcrParseError):
        parse_llm_json("null")


def test_parse_llm_json_top_level_array_raises():
    with pytest.raises(OcrParseError):
        parse_llm_json("[1, 2, 3]")


def test_detect_mime_png():
    assert detect_mime(b"\x89PNG\r\n\x1a\n...") == "image/png"


def test_detect_mime_jpeg_default():
    assert detect_mime(b"\xff\xd8\xff\xe0...") == "image/jpeg"


def test_ocr_parse_error_is_ocr_content_error():
    assert issubclass(OcrParseError, OcrContentError)


def test_ocr_image_error_is_ocr_content_error():
    assert issubclass(OcrImageError, OcrContentError)


def test_first_text_block_returns_text():
    response = SimpleNamespace(content=[SimpleNamespace(type="text", text="hello")])
    assert first_text_block(response) == "hello"


def test_first_text_block_no_text_block_raises_ocr_parse_error():
    response = SimpleNamespace(content=[SimpleNamespace(type="thinking", text=None)])
    with pytest.raises(OcrParseError):
        first_text_block(response)


def test_first_text_block_empty_content_raises_ocr_parse_error():
    response = SimpleNamespace(content=[])
    with pytest.raises(OcrParseError):
        first_text_block(response)


def test_extraction_rules_mentions_quality_issue():
    assert "quality_issue" in EXTRACTION_RULES


def test_blank_extraction_validates_and_has_null_quality_issue():
    extraction = ReceiptExtraction.model_validate(blank_extraction())
    assert extraction.quality_issue is None


def test_blank_extraction_returns_fresh_dict_per_call():
    a, b = blank_extraction(), blank_extraction()
    assert a == b
    assert a is not b
