import pytest

from app.ocr.base import OcrParseError, detect_mime, parse_llm_json


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
