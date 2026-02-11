from pydantic import BaseModel, Field


class PipelineEvent(BaseModel):
    """Emitted by each stage's stream() interface for progress reporting.

    The CLI prints these to stdout. A future web UI forwards them over WebSocket.
    """

    stage: str   # e.g. "stage3a"
    step: str    # e.g. "analyzing_photo"
    progress: float = Field(ge=0.0, le=1.0)
    message: str  # Human-readable German status message
    payload: dict | None = None
