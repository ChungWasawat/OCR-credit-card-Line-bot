import pytest

from app.ocr.claude import ClaudeOcr
from app.ocr.factory import get_ocr_provider
from app.ocr.gemini import GeminiOcr
from app.ocr.typhoon import TyphoonOcr
from app.ocr.typhoon_gemini import TyphoonGeminiOcr


def test_get_ocr_provider_defaults_to_claude(monkeypatch):
    monkeypatch.delenv("OCR_MODEL", raising=False)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "dummy")

    assert isinstance(get_ocr_provider(), ClaudeOcr)


def test_get_ocr_provider_picks_typhoon(monkeypatch):
    monkeypatch.setenv("OCR_MODEL", "typhoon")
    monkeypatch.setenv("TYPHOON_API_KEY", "dummy")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "dummy")

    assert isinstance(get_ocr_provider(), TyphoonOcr)


def test_get_ocr_provider_picks_gemini(monkeypatch):
    monkeypatch.setenv("OCR_MODEL", "gemini")
    monkeypatch.setenv("GEMINI_API_KEY", "dummy")

    assert isinstance(get_ocr_provider(), GeminiOcr)


def test_get_ocr_provider_picks_typhoon_gemini_with_no_anthropic_key(monkeypatch):
    # Proves the zero-Anthropic-dependency property is real, not just asserted in a
    # comment: this provider must construct with no ANTHROPIC_API_KEY present at all.
    monkeypatch.setenv("OCR_MODEL", "typhoon_gemini")
    monkeypatch.setenv("TYPHOON_API_KEY", "dummy")
    monkeypatch.setenv("GEMINI_API_KEY", "dummy")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    assert isinstance(get_ocr_provider(), TyphoonGeminiOcr)


def test_get_ocr_provider_rejects_unknown(monkeypatch):
    monkeypatch.setenv("OCR_MODEL", "bogus")

    with pytest.raises(ValueError):
        get_ocr_provider()
