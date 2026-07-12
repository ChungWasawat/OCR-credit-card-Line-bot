from __future__ import annotations

import base64

import anthropic

from app.ocr.base import IMAGE_PROMPT, OcrProvider, detect_mime, parse_llm_json

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
        text = next(block.text for block in response.content if block.type == "text")
        return parse_llm_json(text)
