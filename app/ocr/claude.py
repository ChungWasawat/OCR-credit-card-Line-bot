from __future__ import annotations

import base64

import anthropic

from app.ocr.base import (
    DETERMINISTIC_IMAGE_STATUSES,
    IMAGE_PROMPT,
    OcrImageError,
    OcrProvider,
    detect_mime,
    first_text_block,
    parse_llm_json,
)

CLAUDE_VISION_MODEL = "claude-haiku-4-5"
MAX_TOKENS = 1024


def default_claude_client() -> anthropic.Anthropic:
    return anthropic.Anthropic(timeout=60.0)


class ClaudeOcr(OcrProvider):
    name = "claude"

    def __init__(self, client: anthropic.Anthropic | None = None) -> None:
        self._client = client or default_claude_client()

    def extract(self, image: bytes) -> dict:
        image_b64 = base64.standard_b64encode(image).decode("utf-8")
        try:
            response = self._client.messages.create(
                model=CLAUDE_VISION_MODEL,
                max_tokens=MAX_TOKENS,
                messages=[
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "image",
                                "source": {
                                    "type": "base64",
                                    "media_type": detect_mime(image),
                                    "data": image_b64,
                                },
                            },
                            {"type": "text", "text": IMAGE_PROMPT},
                        ],
                    }
                ],
            )
        except anthropic.APIStatusError as exc:
            if exc.status_code in DETERMINISTIC_IMAGE_STATUSES:
                raise OcrImageError(
                    f"Claude rejected the image (HTTP {exc.status_code})"
                ) from exc
            raise
        return parse_llm_json(first_text_block(response))
