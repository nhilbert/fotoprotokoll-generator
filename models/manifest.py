from datetime import date, datetime, time
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
    id: str
    filename: str
    path: Path
    timestamp_exif: datetime | None = None
    timestamp_file: datetime
    width: int
    height: int
    orientation: Literal["landscape", "portrait", "square"]

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
    id: str
    filename: str
    content: str
    word_count: int = Field(ge=0)


class ProjectManifest(BaseModel):
    meta: WorkshopMeta
    sessions: list[AgendaSession] = Field(default_factory=list)
    photos: list[Photo] = Field(default_factory=list)
    text_snippets: list[TextSnippet] = Field(default_factory=list)
