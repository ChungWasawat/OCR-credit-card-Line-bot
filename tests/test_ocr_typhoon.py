import json
from types import SimpleNamespace
from unittest.mock import MagicMock

import httpx
import openai
import pytest

from app.ocr.typhoon import TyphoonOcr, TyphoonOcrError, default_typhoon_client


def _typhoon_client_returning(markdown: str) -> MagicMock:
    client = MagicMock()
    client.chat.completions.create.return_value = SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=markdown))]
    )
    return client


def _claude_client_returning(text: str) -> MagicMock:
    client = MagicMock()
    client.messages.create.return_value = SimpleNamespace(
        content=[SimpleNamespace(type="text", text=text)]
    )
    return client


def test_name_matches_factory_dispatch_key():
    assert TyphoonOcr.name == "typhoon"


def test_extract_orchestrates_typhoon_then_haiku():
    typhoon = _typhoon_client_returning("# Receipt\nBig C, 199.50 THB")
    payload = {
        "is_receipt": True,
        "date": "2026-03-05",
        "merchant": "Big C",
        "amount": 199.5,
        "last4": None,
        "details": "",
    }
    claude = _claude_client_returning(json.dumps(payload))

    result = TyphoonOcr(typhoon, claude).extract(b"\xff\xd8\xff\xe0fake-jpeg")

    assert result == payload
    _, kwargs = claude.messages.create.call_args
    prompt = kwargs["messages"][0]["content"]
    assert "Big C, 199.50 THB" in prompt


def test_ocr_call_includes_text_prompt_alongside_image():
    # Regression test: sending only the image (no text block) makes typhoon-ocr echo
    # its internal instructions template instead of performing OCR (found via a real
    # API call, not simulated) — a text block must always be present.
    typhoon = _typhoon_client_returning("# Receipt")
    claude = _claude_client_returning('{"is_receipt": false}')

    TyphoonOcr(typhoon, claude).extract(b"\xff\xd8\xff\xe0fake-jpeg")

    _, kwargs = typhoon.chat.completions.create.call_args
    content = kwargs["messages"][0]["content"]
    text_blocks = [b for b in content if b["type"] == "text"]
    image_blocks = [b for b in content if b["type"] == "image_url"]
    assert len(text_blocks) == 1
    assert len(image_blocks) == 1


def test_blank_markdown_short_circuits_without_calling_haiku():
    typhoon = _typhoon_client_returning("   ")
    claude = _claude_client_returning('{"is_receipt": false}')

    result = TyphoonOcr(typhoon, claude).extract(b"\xff\xd8\xff\xe0fake-jpeg")

    assert result["is_receipt"] is False
    claude.messages.create.assert_not_called()


def test_typhoon_api_error_wrapped_as_typhoon_ocr_error():
    typhoon = MagicMock()
    request = httpx.Request("POST", "https://api.opentyphoon.ai/v1/chat/completions")
    response = httpx.Response(status_code=404, request=request)
    typhoon.chat.completions.create.side_effect = openai.APIStatusError(
        "not found", response=response, body=None
    )
    claude = _claude_client_returning('{"is_receipt": false}')

    with pytest.raises(TyphoonOcrError):
        TyphoonOcr(typhoon, claude).extract(b"\xff\xd8\xff\xe0fake-jpeg")


def test_default_typhoon_client_configures_60s_timeout(monkeypatch):
    captured = {}

    class FakeOpenAI:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    monkeypatch.setattr(openai, "OpenAI", FakeOpenAI)
    monkeypatch.setenv("TYPHOON_API_KEY", "key-x")

    default_typhoon_client()

    assert captured["timeout"] == 60.0


def test_typhoon_ocr_uses_shared_default_claude_client_when_none_injected(monkeypatch):
    sentinel = MagicMock()
    monkeypatch.setattr("app.ocr.typhoon.default_claude_client", lambda: sentinel)
    monkeypatch.setattr("app.ocr.typhoon.default_typhoon_client", lambda: MagicMock())

    ocr = TyphoonOcr()

    assert ocr._claude is sentinel
