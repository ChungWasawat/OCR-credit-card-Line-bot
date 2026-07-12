from __future__ import annotations

import os

from google import genai
from google.genai import types

from app.ocr.base import IMAGE_PROMPT, OcrParseError, OcrProvider, detect_mime, parse_llm_json

GEMINI_VISION_MODEL = "gemini-3.1-flash-lite"  # Verified against the real API
# (2026-07-11) on the actual vision+JSON-extraction path (not just a bare text call):
# correct YYYY-MM-DD date, correct merchant/amount/last4 against a real receipt photo,
# using the real IMAGE_PROMPT. Confirmed via Google's pricing page to have a free tier.
#
# Model-choice history, both directions of risk noted: gemini-2.5-flash-lite is
# permanently 404 "no longer available to new users" despite being listed by
# models.list() — a pinned model was NOT safe here. gemini-2.0-flash-lite/
# gemini-2.0-flash returned 429 RESOURCE_EXHAUSTED (tight/exhausted free quota on this
# key). gemini-flash-lite-latest (the rolling alias) worked throughout, but could move
# to a non-free model later without a code change here. Pinning to 3.1-flash-lite is a
# deliberate choice for determinism, not a guarantee against future retro-restriction —
# a smoke run after any Gemini model change is the actual safety net either way.


def default_gemini_client() -> genai.Client:
    # Explicit api_key, not left to auto-read: google-genai prefers GOOGLE_API_KEY over
    # GEMINI_API_KEY if both happen to be set, which could silently pick up an unrelated
    # key in a GCP-heavy environment. Kept in one place so every Gemini-using provider
    # gets this for free instead of risking a second call site regressing to
    # genai.Client() with no args.
    return genai.Client(
        api_key=os.environ["GEMINI_API_KEY"],
        http_options=types.HttpOptions(timeout=60_000),  # ms, not seconds
    )


class GeminiOcr(OcrProvider):
    name = "gemini"

    def __init__(self, client: genai.Client | None = None) -> None:
        self._client = client or default_gemini_client()

    def extract(self, image: bytes) -> dict:
        response = self._client.models.generate_content(
            model=GEMINI_VISION_MODEL,
            contents=[
                IMAGE_PROMPT,
                types.Part.from_bytes(data=image, mime_type=detect_mime(image)),
            ],
        )
        if not response.text:
            raise OcrParseError("empty Gemini response (possible safety block)")
        return parse_llm_json(response.text)
