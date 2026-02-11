from pydantic import BaseModel, Field, computed_field


# These weights match the defaults in settings.py (temporal_weight / semantic_weight).
# If you change the weights in settings, update these constants to match so that
# combined_confidence stored in content_plan.json stays consistent.
_TEMPORAL_WEIGHT = 0.6
_SEMANTIC_WEIGHT = 0.4


class ContentItem(BaseModel):
    """A matched unit of content: one agenda session with its photos and text.

    `combined_confidence` is always computed from `temporal_confidence` and
    `semantic_confidence` — it cannot be set directly. This prevents stale or
    inconsistent values being stored to disk.
    """

    id: str
    session_ref: str
    heading: str
    photo_ids: list[str] = Field(default_factory=list)
    text_snippet_ref: str | None = None
    temporal_confidence: float = Field(ge=0.0, le=1.0)
    semantic_confidence: float = Field(ge=0.0, le=1.0)
    needs_review: bool

    @computed_field  # type: ignore[misc]
    @property
    def combined_confidence(self) -> float:
        """Weighted average of temporal and semantic confidence scores.

        Always recomputed — never accepted as input. Included in JSON serialization.
        """
        return round(_TEMPORAL_WEIGHT * self.temporal_confidence
                     + _SEMANTIC_WEIGHT * self.semantic_confidence, 4)


class ContentPlan(BaseModel):
    items: list[ContentItem] = Field(default_factory=list)
