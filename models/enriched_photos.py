from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


def _strict_schema(schema: dict) -> dict:
    """Force all properties into `required[]` and set additionalProperties: false.

    OpenAI Structured Outputs strict mode requires:
    - Every property listed in required[] (including those with defaults)
    - additionalProperties: false at every object level
    - Nullable fields as anyOf: [{type: X}, {type: null}]  ← Pydantic generates this correctly

    This modifier is applied via model_config json_schema_extra.
    """
    schema["required"] = list(schema.get("properties", {}).keys())
    schema["additionalProperties"] = False
    return schema


class PhotoAnalysis(BaseModel):
    """Raw structured output from GPT-4o Vision.

    Used directly as `response_format` in `client.beta.chat.completions.parse()`.
    Schema is configured for OpenAI Structured Outputs strict mode.
    """

    model_config = ConfigDict(json_schema_extra=_strict_schema)

    scene_type: Literal["flipchart", "group", "activity", "result", "unknown"]
    description: str
    ocr_text: str | None = None
    topic_keywords: list[str] = Field(default_factory=list)


class EnrichedPhoto(BaseModel):
    """Photo analysis result enriched with pipeline metadata.

    Wraps the raw `PhotoAnalysis` fields and adds `photo_id` and `analysis_model`
    for traceability. Stored per-photo in `.cache/analyses/<content_hash>.json`.
    """

    photo_id: str
    scene_type: Literal["flipchart", "group", "activity", "result", "unknown"]
    description: str
    ocr_text: str | None = None
    topic_keywords: list[str] = Field(default_factory=list)
    analysis_model: str  # e.g. "gpt-5" — value of settings.vision_model at analysis time

    @classmethod
    def from_analysis(cls, photo_id: str, analysis: PhotoAnalysis, model: str) -> "EnrichedPhoto":
        """Construct from a raw PhotoAnalysis response, adding pipeline metadata."""
        return cls(
            photo_id=photo_id,
            scene_type=analysis.scene_type,
            description=analysis.description,
            ocr_text=analysis.ocr_text,
            topic_keywords=analysis.topic_keywords,
            analysis_model=model,
        )


class EnrichedPhotoSet(BaseModel):
    enriched_photos: list[EnrichedPhoto] = Field(default_factory=list)

    def by_photo_id(self, photo_id: str) -> EnrichedPhoto | None:
        return next((p for p in self.enriched_photos if p.photo_id == photo_id), None)
