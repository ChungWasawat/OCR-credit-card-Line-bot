from __future__ import annotations

import os

from app.ocr.base import OcrProvider


def get_ocr_provider() -> OcrProvider:
    model = os.environ.get("OCR_MODEL", "claude")
    if model == "claude":
        from app.ocr.claude import ClaudeOcr

        return ClaudeOcr()
    if model == "typhoon":
        from app.ocr.typhoon import TyphoonOcr

        return TyphoonOcr()
    if model == "gemini":
        from app.ocr.gemini import GeminiOcr

        return GeminiOcr()
    if model == "typhoon_gemini":
        # If a third markdown-parser combination ever appears, the right refactor is
        # one Typhoon pipeline class with an injected parse step, not a fourth file.
        from app.ocr.typhoon_gemini import TyphoonGeminiOcr

        return TyphoonGeminiOcr()
    raise ValueError(f"unknown OCR_MODEL={model!r}")
