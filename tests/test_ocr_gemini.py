import json
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from google import genai
from google.genai import types

from app.ocr.base import OcrParseError
from app.ocr.gemini import GEMINI_VISION_MODEL, GeminiOcr, default_gemini_client


def _client_returning(text: str | None) -> MagicMock:
    client = MagicMock()
    client.models.generate_content.return_value = SimpleNamespace(text=text)
    return client


def test_name_matches_factory_dispatch_key():
    assert GeminiOcr.name == "gemini"


def test_extract_returns_parsed_dict():
    payload = {
        "is_receipt": True,
        "date": "2026-03-05",
        "merchant": "Big C",
        "amount": 199.5,
        "last4": "1234",
        "details": "",
    }
    client = _client_returning(json.dumps(payload))

    result = GeminiOcr(client).extract(b"\xff\xd8\xff\xe0fake-jpeg")

    assert result == payload


def test_extract_sends_model_and_part():
    client = _client_returning('{"is_receipt": false}')

    GeminiOcr(client).extract(b"\xff\xd8\xff\xe0fake-jpeg")

    _, kwargs = client.models.generate_content.call_args
    assert kwargs["model"] == GEMINI_VISION_MODEL
    parts = [c for c in kwargs["contents"] if isinstance(c, types.Part)]
    assert len(parts) == 1


def test_extract_none_response_text_raises():
    client = _client_returning(None)

    with pytest.raises(OcrParseError):
        GeminiOcr(client).extract(b"\xff\xd8\xff\xe0fake-jpeg")


def test_default_gemini_client_configures_60s_timeout(monkeypatch):
    captured = {}

    class FakeClient:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    monkeypatch.setattr(genai, "Client", FakeClient)
    monkeypatch.setenv("GEMINI_API_KEY", "key-x")

    default_gemini_client()

    assert captured["http_options"].timeout == 60_000
