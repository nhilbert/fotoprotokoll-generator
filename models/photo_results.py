from pathlib import Path

from pydantic import BaseModel, Field


class ProcessedPhoto(BaseModel):
    """Stage 2 output for a single photo.

    `processed_path` is relative to `settings.project_dir`
    (e.g. `.cache/processed/abc123.jpg`).
    """

    photo_id: str
    processed_path: Path  # relative to project_dir
    is_flipchart: bool
    crop_applied: bool
    quality_score: float = Field(ge=0.0, le=1.0)
    duplicate_of: str | None = None
    content_hash: str  # SHA-256 of processed image bytes — used as cache key in Stage 3a


class PhotoResults(BaseModel):
    processed_photos: list[ProcessedPhoto] = Field(default_factory=list)

    def by_photo_id(self, photo_id: str) -> ProcessedPhoto | None:
        return next((p for p in self.processed_photos if p.photo_id == photo_id), None)
