"""Tests for the DesignSystem model and design.yaml loader."""
from pathlib import Path

import pytest

from models.design import Assets, ColorPalette, DesignSystem, PageDimensions, TextStyle, Typography


# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------

class TestDefaults:
    def test_default_page_is_a4(self):
        d = DesignSystem()
        assert d.page.width_mm == 210.0
        assert d.page.height_mm == 297.0

    def test_default_margins(self):
        d = DesignSystem()
        assert d.page.margin_top_mm == 20.0

    def test_content_width_excludes_margins(self):
        d = DesignSystem()
        assert d.page.content_width_mm == 210.0 - 20.0 - 20.0

    def test_content_height_excludes_margins(self):
        d = DesignSystem()
        assert d.page.content_height_mm == 297.0 - 20.0 - 20.0

    def test_default_colors(self):
        d = DesignSystem()
        assert d.colors.primary == "#1A3A5C"

    def test_default_heading_is_bold(self):
        d = DesignSystem()
        assert d.typography.heading.weight == "bold"

    def test_default_heading_size(self):
        d = DesignSystem()
        assert d.typography.heading.size_pt == 20.0

    def test_default_logo_is_none(self):
        d = DesignSystem()
        assert d.assets.logo is None


# ---------------------------------------------------------------------------
# load() from real design.yaml
# ---------------------------------------------------------------------------

class TestLoad:
    def test_loads_fixture_design_yaml(self):
        path = Path("tests/fixtures/sample_project/template/design.yaml")
        d = DesignSystem.load(path)
        assert d.page.width_mm == 210.0
        assert d.colors.primary == "#1A3A5C"
        assert d.typography.heading.weight == "bold"
        assert d.typography.caption.size_pt == 8.0

    def test_load_missing_file_raises(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            DesignSystem.load(tmp_path / "nonexistent.yaml")

    def test_load_or_default_returns_default_when_missing(self, tmp_path):
        d = DesignSystem.load_or_default(tmp_path / "nonexistent.yaml")
        assert d.page.width_mm == 210.0

    def test_load_or_default_loads_when_present(self, tmp_path):
        yaml_content = """
page:
  width_mm: 148
  height_mm: 210
colors:
  primary: "#FF0000"
typography:
  heading: {font: Arial, size_pt: 24, weight: bold}
  body:    {font: Arial, size_pt: 11, weight: normal}
  caption: {font: Arial, size_pt: 9,  weight: normal}
assets:
  logo: null
  logo_position: top-left
"""
        (tmp_path / "design.yaml").write_text(yaml_content, encoding="utf-8")
        d = DesignSystem.load_or_default(tmp_path / "design.yaml")
        assert d.page.width_mm == 148.0
        assert d.colors.primary == "#FF0000"

    def test_partial_yaml_uses_defaults_for_missing_fields(self, tmp_path):
        (tmp_path / "design.yaml").write_text("page:\n  width_mm: 148\n", encoding="utf-8")
        d = DesignSystem.load_or_default(tmp_path / "design.yaml")
        assert d.page.width_mm == 148.0
        assert d.page.height_mm == 297.0  # default
        assert d.colors.primary == "#1A3A5C"  # default


# ---------------------------------------------------------------------------
# Typography.get()
# ---------------------------------------------------------------------------

class TestTypographyGet:
    def test_get_heading(self):
        d = DesignSystem()
        style = d.typography.get("heading")
        assert style.weight == "bold"

    def test_get_body(self):
        d = DesignSystem()
        style = d.typography.get("body")
        assert style.size_pt == 10.0

    def test_get_unknown_falls_back_to_body(self):
        d = DesignSystem()
        style = d.typography.get("nonexistent_style")
        assert style == d.typography.body


# ---------------------------------------------------------------------------
# Round-trip via Pydantic
# ---------------------------------------------------------------------------

class TestRoundTrip:
    def test_serializes_and_reloads(self):
        path = Path("tests/fixtures/sample_project/template/design.yaml")
        original = DesignSystem.load(path)
        reloaded = DesignSystem.model_validate_json(original.model_dump_json())
        assert reloaded.page.width_mm == original.page.width_mm
        assert reloaded.colors.primary == original.colors.primary
        assert reloaded.typography.heading.weight == original.typography.heading.weight
