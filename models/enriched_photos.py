from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from utils.openai_utils import strict_schema as _strict_schema


class CropBox(BaseModel):
    """Normalized crop coordinates (0.0–1.0) relative to image dimensions."""
    x_min: float
    y_min: float
    x_max: float
    y_max: float


class PhotoAnalysis(BaseModel):
    """Raw structured output from GPT Vision.

    Used directly as `response_format` in `client.beta.chat.completions.parse()`.
    Schema is configured for OpenAI Structured Outputs strict mode.

    `crop_box` is only populated when `scene_type` is "flipchart" — normalized
    coordinates (0–1) that tightly bound the rectangular document in the frame.
    """

    model_config = ConfigDict(json_schema_extra=_strict_schema)

    scene_type: Literal["flipchart", "group", "activity", "result", "unknown"]
    description: str
    ocr_text: str | None = None
    topic_keywords: list[str] = Field(default_factory=list)
    crop_box: CropBox | None = None


class EnrichedPhoto(BaseModel):
    """Photo analysis result enriched with pipeline metadata.

    Wraps the raw `PhotoAnalysis` fields and adds pipeline tracking fields.
    Stored per-photo in `.cache/analyses/<content_hash>.json`.

    `processed_path` is relative to `project_dir` and points to the cropped
    image for document photos, or the original for all other scene types.
    """

    photo_id: str
    scene_type: Literal["flipchart", "group", "activity", "result", "unknown"]
    description: str
    ocr_text: str | None = None
    topic_keywords: list[str] = Field(default_factory=list)
    crop_box: CropBox | None = None
    processed_path: Path | None = None   # relative to project_dir; set after cropping
    analysis_model: str

    @classmethod
    def from_analysis(
        cls,
        photo_id: str,
        analysis: PhotoAnalysis,
        model: str,
        processed_path: Path | None = None,
    ) -> "EnrichedPhoto":
        """Construct from a raw PhotoAnalysis response, adding pipeline metadata."""
        return cls(
            photo_id=photo_id,
            scene_type=analysis.scene_type,
            description=analysis.description,
            ocr_text=analysis.ocr_text,
            topic_keywords=analysis.topic_keywords,
            crop_box=analysis.crop_box,
            processed_path=processed_path,
            analysis_model=model,
        )


class EnrichedPhotoSet(BaseModel):
    enriched_photos: list[EnrichedPhoto] = Field(default_factory=list)

    def by_photo_id(self, photo_id: str) -> EnrichedPhoto | None:
        return next((p for p in self.enriched_photos if p.photo_id == photo_id), None)
