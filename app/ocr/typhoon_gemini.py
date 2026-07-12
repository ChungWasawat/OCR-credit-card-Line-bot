from __future__ import annotations

from google import genai

from app.ocr.base import (
    MARKDOWN_PROMPT_TEMPLATE,
    OcrParseError,
    OcrProvider,
    blank_extraction,
    parse_llm_json,
)
from app.ocr.gemini import default_gemini_client
from app.ocr.typhoon import default_typhoon_client, ocr_markdown

GEMINI_PARSE_MODEL = "gemini-3.1-flash-lite"


class TyphoonGeminiOcr(OcrProvider):
    """Typhoon OCR step 1 (image -> markdown) + Gemini step 2 (markdown -> structured
    JSON), instead of Claude Haiku. Runs fully free with zero Anthropic dependency,
    unlike TyphoonOcr (whose parse step needs a billed ANTHROPIC_API_KEY).
    """

    name = "typhoon_gemini"

    def __init__(
        self,
        typhoon_client=None,
        gemini_client: genai.Client | None = None,
    ) -> None:
        self._typhoon = typhoon_client or default_typhoon_client()
        self._gemini = gemini_client or default_gemini_client()

    def extract(self, image: bytes) -> dict:
        markdown = ocr_markdown(image, self._typhoon)
        if not markdown.strip():
            return blank_extraction()
        return self._parse_markdown(markdown)

    def _parse_markdown(self, markdown: str) -> dict:
        # No wrapper exception here (unlike TyphoonOcrError for the OCR step) —
        # google.genai.errors.ClientError (429/403/404, a real observed failure mode)
        # must propagate raw so a future retry-classification layer treats it as
        # transient, not miscategorized as a content error. google.genai errors already
        # name their endpoint/model and are self-diagnosing. Matches how TyphoonOcr
        # already treats its own Anthropic parse-step failures (raw propagation).
        response = self._gemini.models.generate_content(
            model=GEMINI_PARSE_MODEL,
            contents=[MARKDOWN_PROMPT_TEMPLATE.format(markdown=markdown)],
        )
        if not response.text:
            raise OcrParseError("empty Gemini response (possible safety block)")
        return parse_llm_json(response.text)
