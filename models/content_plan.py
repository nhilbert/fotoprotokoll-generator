from pydantic import BaseModel, Field


class ContentItem(BaseModel):
    id: str
    session_ref: str
    heading: str
    photo_ids: list[str] = Field(default_factory=list)
    text_snippet_ref: str | None = None
    temporal_confidence: float = Field(ge=0.0, le=1.0)
    semantic_confidence: float = Field(ge=0.0, le=1.0)
    combined_confidence: float = Field(ge=0.0, le=1.0)
    needs_review: bool


class ContentPlan(BaseModel):
    items: list[ContentItem] = Field(default_factory=list)
