import json
from types import SimpleNamespace
from unittest.mock import MagicMock

import httpx
import openai
import pytest

from app.ocr.base import OcrImageError, OcrParseError
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


def _typhoon_status_error(status_code: int) -> openai.APIStatusError:
    request = httpx.Request("POST", "https://api.opentyphoon.ai/v1/chat/completions")
    response = httpx.Response(status_code=status_code, request=request)
    return openai.APIStatusError("error", response=response, body=None)


def test_typhoon_404_wrapped_as_typhoon_ocr_error():
    typhoon = MagicMock()
    typhoon.chat.completions.create.side_effect = _typhoon_status_error(404)
    claude = _claude_client_returning('{"is_receipt": false}')

    with pytest.raises(TyphoonOcrError):
        TyphoonOcr(typhoon, claude).extract(b"\xff\xd8\xff\xe0fake-jpeg")


def test_typhoon_400_raises_ocr_image_error():
    typhoon = MagicMock()
    typhoon.chat.completions.create.side_effect = _typhoon_status_error(400)
    claude = _claude_client_returning('{"is_receipt": false}')

    with pytest.raises(OcrImageError):
        TyphoonOcr(typhoon, claude).extract(b"\xff\xd8\xff\xe0fake-jpeg")


def test_typhoon_429_propagates_raw():
    typhoon = MagicMock()
    typhoon.chat.completions.create.side_effect = _typhoon_status_error(429)
    claude = _claude_client_returning('{"is_receipt": false}')

    with pytest.raises(openai.APIStatusError):
        TyphoonOcr(typhoon, claude).extract(b"\xff\xd8\xff\xe0fake-jpeg")


def test_typhoon_500_propagates_raw():
    typhoon = MagicMock()
    typhoon.chat.completions.create.side_effect = _typhoon_status_error(500)
    claude = _claude_client_returning('{"is_receipt": false}')

    with pytest.raises(openai.APIStatusError):
        TyphoonOcr(typhoon, claude).extract(b"\xff\xd8\xff\xe0fake-jpeg")


def test_parse_step_no_text_block_raises_ocr_parse_error():
    typhoon = _typhoon_client_returning("# Receipt\nBig C, 199.50 THB")
    claude = MagicMock()
    claude.messages.create.return_value = SimpleNamespace(
        content=[SimpleNamespace(type="thinking", text=None)]
    )

    with pytest.raises(OcrParseError):
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
