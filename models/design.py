"""Design system model — typed representation of design.yaml.

Loaded once at pipeline start and passed to Stage 5 (rendering).
All downstream stages reference style keys (e.g. "heading") defined here.
"""
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field


class PageDimensions(BaseModel):
    width_mm: float = 210.0
    height_mm: float = 297.0
    margin_top_mm: float = 20.0
    margin_bottom_mm: float = 20.0
    margin_left_mm: float = 20.0
    margin_right_mm: float = 20.0

    @property
    def content_width_mm(self) -> float:
        return self.width_mm - self.margin_left_mm - self.margin_right_mm

    @property
    def content_height_mm(self) -> float:
        return self.height_mm - self.margin_top_mm - self.margin_bottom_mm


class ColorPalette(BaseModel):
    primary: str = "#1A3A5C"
    secondary: str = "#F4F7FA"
    text: str = "#1A1A1A"
    caption: str = "#666666"


class TextStyle(BaseModel):
    font: str = "DejaVu Sans"
    size_pt: float = 10.0
    weight: Literal["normal", "bold", "italic"] = "normal"


class Typography(BaseModel):
    heading: TextStyle = Field(default_factory=lambda: TextStyle(size_pt=20.0, weight="bold"))
    body: TextStyle = Field(default_factory=lambda: TextStyle(size_pt=10.0))
    caption: TextStyle = Field(default_factory=lambda: TextStyle(size_pt=8.0))

    def get(self, style_ref: str) -> TextStyle:
        """Look up a TextStyle by its style_ref key (e.g. 'heading', 'body', 'caption').

        Falls back to body style for unknown keys.
        """
        return getattr(self, style_ref, self.body)


class Assets(BaseModel):
    logo: Path | None = None
    logo_position: Literal["top-left", "top-right", "bottom-left", "bottom-right"] = "top-right"


class DesignSystem(BaseModel):
    """Complete design system loaded from design.yaml.

    Provides defaults for every field so it is usable even when design.yaml
    is absent or partially specified.
    """
    page: PageDimensions = Field(default_factory=PageDimensions)
    colors: ColorPalette = Field(default_factory=ColorPalette)
    typography: Typography = Field(default_factory=Typography)
    assets: Assets = Field(default_factory=Assets)

    @classmethod
    def load(cls, path: Path) -> "DesignSystem":
        """Load from a YAML file. Missing fields use Pydantic defaults.

        Raises FileNotFoundError if path does not exist.
        """
        import yaml  # lazy — only needed at load time
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        return cls.model_validate(data)

    @classmethod
    def load_or_default(cls, path: Path) -> "DesignSystem":
        """Load from path if it exists, otherwise return default design system."""
        if path.exists():
            return cls.load(path)
        return cls()
