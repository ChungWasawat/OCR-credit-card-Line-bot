from __future__ import annotations

import base64
import os

import anthropic
import openai

from app.ocr.base import (
    DETERMINISTIC_IMAGE_STATUSES,
    MARKDOWN_PROMPT_TEMPLATE,
    OcrImageError,
    OcrProvider,
    blank_extraction,
    detect_mime,
    first_text_block,
    parse_llm_json,
)
from app.ocr.claude import default_claude_client

TYPHOON_BASE_URL = "https://api.opentyphoon.ai/v1"
TYPHOON_OCR_MODEL = "typhoon-ocr"  # Verified against the real API (2026-07-11) via
# scripts/ocr_smoke.py --model typhoon: /v1/chat/completions with an image_url content
# block works for this model, despite Typhoon's docs only describing a dedicated
# typhoon_ocr pip package. No longer an open assumption.
#
# One real gotcha found the same way: sending ONLY the image (no text block) makes the
# model echo back what looks like its own internal formatting-instructions template
# instead of performing OCR — not an error, just wrong/useless content. A minimal text
# instruction alongside the image fixes it and returns real extracted text. "Fixed-prompt"
# (per checklist3.md) means it can't be steered to return custom structured fields, not
# that it needs zero text input at all.
_OCR_TRIGGER_PROMPT = "Extract all text from this image as markdown."
PARSE_MODEL = "claude-haiku-4-5"


class TyphoonOcrError(Exception):
    """Wraps a 404 from the Typhoon OCR call with a self-diagnosing message, since a
    bare openai.APIStatusError from a client pointed at a non-OpenAI host is confusing
    on its own. A 404 here means the endpoint/model assumption this integration is
    built on may be wrong — a config problem, not a content problem, so it is
    deliberately NOT part of the OcrContentError family: it keeps propagating for a
    Cloud Tasks retry (and Commit 2's final-attempt generic reply) rather than telling
    the user to resend their photo, which would be the wrong advice.
    """


def default_typhoon_client() -> openai.OpenAI:
    return openai.OpenAI(
        api_key=os.environ["TYPHOON_API_KEY"], base_url=TYPHOON_BASE_URL, timeout=60.0
    )


def ocr_markdown(image: bytes, client: openai.OpenAI) -> str:
    """Raw Typhoon OCR call: image -> markdown text. Shared by every provider that uses
    Typhoon for the OCR step (TyphoonOcr, TyphoonGeminiOcr) so the text-prompt fix and
    error wrapping live in exactly one place.
    """
    image_b64 = base64.standard_b64encode(image).decode("utf-8")
    data_url = f"data:{detect_mime(image)};base64,{image_b64}"
    try:
        response = client.chat.completions.create(
            model=TYPHOON_OCR_MODEL,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": _OCR_TRIGGER_PROMPT},
                        {"type": "image_url", "image_url": {"url": data_url}},
                    ],
                }
            ],
        )
    except openai.APIStatusError as exc:
        if exc.status_code in DETERMINISTIC_IMAGE_STATUSES:
            raise OcrImageError(
                f"Typhoon OCR rejected the image (HTTP {exc.status_code})"
            ) from exc
        if exc.status_code == 404:
            raise TyphoonOcrError(
                f"Typhoon OCR call failed (HTTP {exc.status_code}). The assumption "
                f"that model {TYPHOON_OCR_MODEL!r} works via /v1/chat/completions may "
                "be wrong — see TODO(verify) in app/ocr/typhoon.py"
            ) from exc
        # Everything else (429 rate limit, 5xx) is transient — propagate raw so it
        # retries, instead of the previous over-broad wrap into TyphoonOcrError.
        raise
    return response.choices[0].message.content or ""


class TyphoonOcr(OcrProvider):
    name = "typhoon"

    def __init__(
        self,
        typhoon_client: openai.OpenAI | None = None,
        claude_client: anthropic.Anthropic | None = None,
    ) -> None:
        self._typhoon = typhoon_client or default_typhoon_client()
        self._claude = claude_client or default_claude_client()

    def extract(self, image: bytes) -> dict:
        markdown = ocr_markdown(image, self._typhoon)
        if not markdown.strip():
            return blank_extraction()
        return self._parse_markdown(markdown)

    def _parse_markdown(self, markdown: str) -> dict:
        response = self._claude.messages.create(
            model=PARSE_MODEL,
            max_tokens=1024,
            messages=[
                {
                    "role": "user",
                    "content": MARKDOWN_PROMPT_TEMPLATE.format(markdown=markdown),
                }
            ],
        )
        return parse_llm_json(first_text_block(response))
