from typing import Literal

from pydantic import BaseModel, Field


class PhotoAnalysis(BaseModel):
    """Raw structured output from GPT-4o Vision.

    Used directly as `response_format` in `client.beta.chat.completions.parse()`.
    All fields must be compatible with OpenAI Structured Outputs strict mode:
    - additionalProperties is implicitly false
    - all fields listed (required)
    - no unsupported constraints ($ref, min/max, root anyOf)
    """

    scene_type: Literal["flipchart", "group", "activity", "result", "unknown"]
    description: str
    ocr_text: str | None = None
    topic_keywords: list[str] = Field(default_factory=list)


class EnrichedPhoto(BaseModel):
    """Photo analysis result enriched with metadata for downstream stages."""

    photo_id: str
    scene_type: Literal["flipchart", "group", "activity", "result", "unknown"]
    description: str
    ocr_text: str | None = None
    topic_keywords: list[str] = Field(default_factory=list)
    analysis_model: str  # e.g. "gpt-4o-2024-11-20" — recorded for auditability


class EnrichedPhotoSet(BaseModel):
    enriched_photos: list[EnrichedPhoto] = Field(default_factory=list)

    def by_photo_id(self, photo_id: str) -> EnrichedPhoto | None:
        return next((p for p in self.enriched_photos if p.photo_id == photo_id), None)
