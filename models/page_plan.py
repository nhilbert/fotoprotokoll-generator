from typing import Literal

from pydantic import BaseModel, Field


class PhotoSlot(BaseModel):
    photo_id: str
    caption: str
    display_size: Literal["full-width", "half-width", "portrait-pair"]
    subtitle: str = ""  # 1-3 word label shown below the image


class TextBlock(BaseModel):
    content: str
    role: Literal["heading", "body", "caption", "footer"]
    style_ref: str  # Key into design.yaml typography section, e.g. "heading"


class Page(BaseModel):
    page_number: int = Field(ge=1)
    page_type: Literal["cover", "section_divider", "content", "closing"]
    layout_variant: Literal["1-photo", "2-photo", "text-only", "photo-left", "photo-right"]
    content_item_ref: str | None = None
    page_heading: str | None = None  # Topic heading shown in the running header
    photo_slots: list[PhotoSlot] = Field(default_factory=list)
    text_blocks: list[TextBlock] = Field(default_factory=list)


class PagePlan(BaseModel):
    pages: list[Page] = Field(default_factory=list)
