import json
from types import SimpleNamespace
from unittest.mock import MagicMock

from google.genai import errors as genai_errors
import pytest

from app.ocr.base import OcrParseError
from app.ocr.typhoon_gemini import TyphoonGeminiOcr


def _typhoon_client_returning(markdown: str) -> MagicMock:
    client = MagicMock()
    client.chat.completions.create.return_value = SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=markdown))]
    )
    return client


def _gemini_client_returning(text: str | None) -> MagicMock:
    client = MagicMock()
    client.models.generate_content.return_value = SimpleNamespace(text=text)
    return client


def test_name_matches_factory_dispatch_key():
    assert TyphoonGeminiOcr.name == "typhoon_gemini"


def test_extract_orchestrates_typhoon_then_gemini():
    typhoon = _typhoon_client_returning("# Receipt\nBig C, 199.50 THB")
    payload = {
        "is_receipt": True,
        "date": "2026-03-05",
        "merchant": "Big C",
        "amount": 199.5,
        "last4": None,
        "details": "",
    }
    gemini = _gemini_client_returning(json.dumps(payload))

    result = TyphoonGeminiOcr(typhoon, gemini).extract(b"\xff\xd8\xff\xe0fake-jpeg")

    assert result == payload
    _, kwargs = gemini.models.generate_content.call_args
    prompt = kwargs["contents"][0]
    assert "Big C, 199.50 THB" in prompt


def test_blank_markdown_short_circuits_without_calling_gemini():
    typhoon = _typhoon_client_returning("   ")
    gemini = _gemini_client_returning('{"is_receipt": false}')

    result = TyphoonGeminiOcr(typhoon, gemini).extract(b"\xff\xd8\xff\xe0fake-jpeg")

    assert result["is_receipt"] is False
    gemini.models.generate_content.assert_not_called()


def test_none_response_text_raises_ocr_parse_error():
    typhoon = _typhoon_client_returning("# Receipt")
    gemini = _gemini_client_returning(None)

    with pytest.raises(OcrParseError):
        TyphoonGeminiOcr(typhoon, gemini).extract(b"\xff\xd8\xff\xe0fake-jpeg")


def test_gemini_client_error_propagates_unwrapped():
    # Must NOT be miscategorized as a content error (OcrParseError) or a
    # Typhoon-specific error (TyphoonOcrError) — a quota/auth failure needs to stay
    # classifiable as transient by a future retry layer. pytest.raises(ClientError)
    # only passes if exactly that type (or a subclass) escapes; OcrParseError or
    # TyphoonOcrError would fail this test, not silently pass it.
    typhoon = _typhoon_client_returning("# Receipt")
    gemini = MagicMock()
    gemini.models.generate_content.side_effect = genai_errors.ClientError(
        429, {"error": {"message": "quota exceeded"}}
    )

    with pytest.raises(genai_errors.ClientError):
        TyphoonGeminiOcr(typhoon, gemini).extract(b"\xff\xd8\xff\xe0fake-jpeg")
