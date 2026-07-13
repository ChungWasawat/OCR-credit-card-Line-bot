import json
from types import SimpleNamespace
from unittest.mock import MagicMock

import anthropic
import httpx
import pytest

from app.ocr.base import OcrImageError, OcrParseError
from app.ocr.claude import CLAUDE_VISION_MODEL, MAX_TOKENS, ClaudeOcr, default_claude_client


def _client_returning(text: str) -> MagicMock:
    client = MagicMock()
    client.messages.create.return_value = SimpleNamespace(
        content=[SimpleNamespace(type="text", text=text)]
    )
    return client


def test_name_matches_factory_dispatch_key():
    assert ClaudeOcr.name == "claude"


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

    result = ClaudeOcr(client).extract(b"\xff\xd8\xff\xe0fake-jpeg")

    assert result == payload


def test_extract_sends_model_max_tokens_and_image_block():
    client = _client_returning('{"is_receipt": false}')

    ClaudeOcr(client).extract(b"\xff\xd8\xff\xe0fake-jpeg")

    _, kwargs = client.messages.create.call_args
    assert kwargs["model"] == CLAUDE_VISION_MODEL
    assert kwargs["max_tokens"] == MAX_TOKENS
    content = kwargs["messages"][0]["content"]
    image_blocks = [b for b in content if b["type"] == "image"]
    assert len(image_blocks) == 1
    assert image_blocks[0]["source"]["media_type"] == "image/jpeg"


def test_extract_non_json_response_raises():
    client = _client_returning("I cannot process this image.")

    with pytest.raises(OcrParseError):
        ClaudeOcr(client).extract(b"\xff\xd8\xff\xe0fake-jpeg")


def test_extract_no_text_block_raises_ocr_parse_error():
    client = MagicMock()
    client.messages.create.return_value = SimpleNamespace(
        content=[SimpleNamespace(type="thinking", text=None)]
    )

    with pytest.raises(OcrParseError):
        ClaudeOcr(client).extract(b"\xff\xd8\xff\xe0fake-jpeg")


def _status_error(status_code: int) -> anthropic.APIStatusError:
    request = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
    response = httpx.Response(status_code=status_code, request=request)
    return anthropic.APIStatusError("error", response=response, body=None)


def test_extract_400_raises_ocr_image_error():
    client = MagicMock()
    client.messages.create.side_effect = _status_error(400)

    with pytest.raises(OcrImageError):
        ClaudeOcr(client).extract(b"\xff\xd8\xff\xe0fake-jpeg")


def test_extract_429_propagates_raw():
    client = MagicMock()
    client.messages.create.side_effect = _status_error(429)

    with pytest.raises(anthropic.APIStatusError):
        ClaudeOcr(client).extract(b"\xff\xd8\xff\xe0fake-jpeg")


def test_default_claude_client_configures_60s_timeout(monkeypatch):
    captured = {}

    class FakeAnthropic:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    monkeypatch.setattr(anthropic, "Anthropic", FakeAnthropic)

    default_claude_client()

    assert captured["timeout"] == 60.0
