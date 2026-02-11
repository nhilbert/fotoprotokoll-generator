from datetime import date, datetime, time, timezone
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, field_validator


class WorkshopMeta(BaseModel):
    title: str
    workshop_date: date | None = None
    location: str | None = None
    participants: int | None = None


class AgendaSession(BaseModel):
    id: str
    order: int
    name: str
    start_time: time | None = None
    end_time: time | None = None


class Photo(BaseModel):
    """Represents a raw photo as inventoried during Stage 1.

    Paths are stored **relative to `settings.project_dir`** (e.g. `fotos/IMG_001.jpg`).
    Resolve to absolute at runtime with: `settings.project_dir / photo.path`

    All datetimes are stored as UTC-aware. Naive datetimes supplied at construction
    (e.g. from os.stat() or EXIF parsing) are automatically treated as UTC.
    """

    id: str
    filename: str
    path: Path  # relative to project_dir — e.g. fotos/IMG_001.jpg
    timestamp_exif: datetime | None = None
    timestamp_file: datetime
    width: int
    height: int
    orientation: Literal["landscape", "portrait", "square"]

    @field_validator("timestamp_exif", "timestamp_file", mode="before")
    @classmethod
    def ensure_utc(cls, v: datetime | str | None) -> datetime | str | None:
        # During JSON deserialization Pydantic passes a string; let Pydantic parse
        # it first, then the after-validator below attaches UTC if needed.
        # During direct construction v is already a datetime object.
        if isinstance(v, datetime) and v.tzinfo is None:
            return v.replace(tzinfo=timezone.utc)
        return v

    @field_validator("width", "height")
    @classmethod
    def must_be_positive(cls, v: int) -> int:
        if v <= 0:
            raise ValueError("width and height must be positive")
        return v

    @property
    def best_timestamp(self) -> datetime:
        """EXIF timestamp preferred; falls back to file mtime."""
        return self.timestamp_exif or self.timestamp_file


class TextSnippet(BaseModel):
    """A workshop-specific text file read from `data/text/`.

    Not to be confused with template content — these are per-workshop notes.
    """

    id: str
    filename: str
    content: str
    word_count: int = Field(ge=0)


class ProjectManifest(BaseModel):
    meta: WorkshopMeta
    sessions: list[AgendaSession] = Field(default_factory=list)
    photos: list[Photo] = Field(default_factory=list)
    text_snippets: list[TextSnippet] = Field(default_factory=list)
