"""Tests for Stage 5 PDF Rendering."""
from datetime import date
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from models.design import DesignSystem
from models.enriched_photos import EnrichedPhoto, EnrichedPhotoSet
from models.manifest import ProjectManifest, WorkshopMeta
from models.page_plan import Page, PagePlan, PhotoSlot, TextBlock
from pipeline.stage5_render import _output_path, _render_html, _resolve_photo_path, _slugify, _validate_pdf_fonts, run
from settings import Settings


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _settings(tmp_path) -> Settings:
    for d in ("agenda", "fotos", "text", "template", "output", ".cache"):
        (tmp_path / d).mkdir(exist_ok=True)
    return Settings(openai_api_key="test", project_dir=tmp_path)


def _manifest(title="Workshop", workshop_date=None) -> ProjectManifest:
    return ProjectManifest(
        meta=WorkshopMeta(title=title, workshop_date=workshop_date),
        sessions=[],
        photos=[],
        text_snippets=[],
    )


def _cover_plan() -> PagePlan:
    return PagePlan(pages=[
        Page(
            page_number=1,
            page_type="cover",
            layout_variant="text-only",
            text_blocks=[
                TextBlock(content="Workshop Titel", role="heading", style_ref="heading"),
                TextBlock(content="9. Februar 2026", role="body", style_ref="body"),
            ],
        )
    ])


def _empty_photo_set() -> EnrichedPhotoSet:
    return EnrichedPhotoSet()


# ---------------------------------------------------------------------------
# _slugify
# ---------------------------------------------------------------------------

class TestSlugify:
    def test_simple_title(self):
        assert _slugify("Workshop") == "workshop"

    def test_spaces_become_underscores(self):
        assert _slugify("My Workshop") == "my_workshop"

    def test_german_umlauts(self):
        assert _slugify("Gelingensfaktoren für Schüler") == "gelingensfaktoren_fuer_schueler"

    def test_ae_umlaut(self):
        assert _slugify("Lärm") == "laerm"

    def test_oe_umlaut(self):
        assert _slugify("Töne") == "toene"

    def test_ss_eszett(self):
        assert _slugify("Straße") == "strasse"

    def test_special_chars_stripped(self):
        assert _slugify("A & B (2026)") == "a_b_2026"

    def test_empty_string_fallback(self):
        assert _slugify("") == "protokoll"

    def test_truncated_at_50_chars(self):
        long = "a" * 60
        assert len(_slugify(long)) == 50

    def test_no_leading_trailing_underscores(self):
        s = _slugify("  Workshop  ")
        assert not s.startswith("_")
        assert not s.endswith("_")


# ---------------------------------------------------------------------------
# _output_path
# ---------------------------------------------------------------------------

class TestOutputPath:
    def test_with_date(self, tmp_path):
        s = _settings(tmp_path)
        m = _manifest("Workshop Titel", date(2026, 2, 9))
        path = _output_path(s, m)
        assert path.name == "fotoprotokoll_workshop_titel_20260209.pdf"
        assert path.parent == s.output_dir

    def test_without_date(self, tmp_path):
        s = _settings(tmp_path)
        m = _manifest("Workshop")
        path = _output_path(s, m)
        assert path.name == "fotoprotokoll_workshop.pdf"

    def test_german_title(self, tmp_path):
        s = _settings(tmp_path)
        m = _manifest("Gelingensfaktoren für Schüler")
        path = _output_path(s, m)
        assert "fuer" in path.name
        assert "schueler" in path.name


# ---------------------------------------------------------------------------
# _render_html  (unit tests — no WeasyPrint)
# ---------------------------------------------------------------------------

class TestRenderHtml:
    def test_cover_title_in_output(self):
        html = _render_html(_cover_plan(), DesignSystem(), {}, None)
        assert "Workshop Titel" in html

    def test_cover_meta_date_in_output(self):
        html = _render_html(_cover_plan(), DesignSystem(), {}, None)
        assert "9. Februar 2026" in html

    def test_page_dimensions_in_css(self):
        html = _render_html(_cover_plan(), DesignSystem(), {}, None)
        assert "210.0mm" in html
        assert "297.0mm" in html

    def test_primary_color_in_css(self):
        html = _render_html(_cover_plan(), DesignSystem(), {}, None)
        assert "#1A3A5C" in html

    def test_photo_src_embedded(self, tmp_path):
        img_path = tmp_path / "processed.jpg"
        img_path.write_bytes(b"FAKEJPEG")
        plan = PagePlan(pages=[
            Page(
                page_number=1,
                page_type="content",
                layout_variant="1-photo",
                photo_slots=[PhotoSlot(photo_id="photo_001", caption="Test", display_size="full-width")],
            )
        ])
        photo_srcs = {"photo_001": img_path.resolve().as_uri()}
        html = _render_html(plan, DesignSystem(), photo_srcs, None)
        assert "processed.jpg" in html
        assert '<img class="photo-img"' in html

    def test_caption_in_alt_attribute(self, tmp_path):
        # Captions are not shown as visible text but still present as alt text
        img_path = tmp_path / "photo.jpg"
        img_path.write_bytes(b"FAKEJPEG")
        plan = PagePlan(pages=[
            Page(
                page_number=1,
                page_type="content",
                layout_variant="1-photo",
                photo_slots=[
                    PhotoSlot(photo_id="p1", caption="Moderationskarten", display_size="full-width")
                ],
            )
        ])
        html = _render_html(plan, DesignSystem(), {"p1": img_path.resolve().as_uri()}, None)
        assert 'alt="Moderationskarten"' in html
        # Caption not rendered as a separate visible div
        assert '<div class="photo-caption">' not in html

    def test_section_divider_rendered(self):
        plan = PagePlan(pages=[
            Page(
                page_number=1,
                page_type="section_divider",
                layout_variant="text-only",
                text_blocks=[TextBlock(content="Morgen-Block", role="heading", style_ref="heading")],
            )
        ])
        html = _render_html(plan, DesignSystem(), {}, None)
        assert "Morgen-Block" in html
        assert "section-divider" in html

    def test_page_heading_shown_in_header(self):
        plan = PagePlan(pages=[
            Page(
                page_number=2,
                page_type="content",
                layout_variant="text-only",
                page_heading="Ideensammlung",
                text_blocks=[TextBlock(content="Ideensammlung", role="heading", style_ref="heading")],
            )
        ])
        html = _render_html(plan, DesignSystem(), {}, None)
        assert "Ideensammlung" in html
        assert "page-header-title" in html

    def test_page_heading_shown_on_all_content_pages(self):
        plan = PagePlan(pages=[
            Page(page_number=2, page_type="content", layout_variant="1-photo",
                 page_heading="Arbeiten im Team",
                 photo_slots=[PhotoSlot(photo_id="p1", caption="", display_size="full-width")]),
            Page(page_number=3, page_type="content", layout_variant="1-photo",
                 page_heading="Arbeiten im Team",
                 photo_slots=[PhotoSlot(photo_id="p2", caption="", display_size="full-width")]),
        ])
        html = _render_html(plan, DesignSystem(), {}, None)
        assert html.count("Arbeiten im Team") == 2

    def test_no_logo_when_none(self):
        plan = _cover_plan()
        html = _render_html(plan, DesignSystem(), {}, None)
        # No img with alt="Logo" on cover
        assert 'alt="Logo"' not in html

    def test_logo_rendered_when_provided(self, tmp_path):
        logo = tmp_path / "logo.png"
        logo.write_bytes(b"PNG")
        plan = _cover_plan()
        html = _render_html(plan, DesignSystem(), {}, logo.resolve().as_uri())
        assert "logo.png" in html

    def test_two_landscape_photos_stacked(self):
        plan = PagePlan(pages=[
            Page(
                page_number=1,
                page_type="content",
                layout_variant="2-photo",
                photo_slots=[
                    PhotoSlot(photo_id="p1", caption="A", display_size="full-width"),
                    PhotoSlot(photo_id="p2", caption="B", display_size="full-width"),
                ],
            )
        ])
        html = _render_html(plan, DesignSystem(), {}, None)
        assert html.count('photo-cell photo-cell--full-width') == 2
        assert "photo-grid--stacked" in html

    def test_two_portrait_photos_side_by_side(self):
        plan = PagePlan(pages=[
            Page(
                page_number=1,
                page_type="content",
                layout_variant="2-photo",
                photo_slots=[
                    PhotoSlot(photo_id="p1", caption="A", display_size="portrait-pair"),
                    PhotoSlot(photo_id="p2", caption="B", display_size="portrait-pair"),
                ],
            )
        ])
        html = _render_html(plan, DesignSystem(), {}, None)
        assert html.count('photo-cell photo-cell--portrait-pair') == 2
        assert 'photo-grid photo-grid--stacked' not in html

    def test_portrait_pair_css_class(self):
        plan = PagePlan(pages=[
            Page(
                page_number=1,
                page_type="content",
                layout_variant="1-photo",
                photo_slots=[
                    PhotoSlot(photo_id="p1", caption="", display_size="portrait-pair"),
                ],
            )
        ])
        html = _render_html(plan, DesignSystem(), {}, None)
        assert "photo-cell--portrait-pair" in html
        assert "photo-grid--single-portrait" in html

    def test_multiple_pages_all_rendered(self):
        plan = PagePlan(pages=[
            Page(page_number=1, page_type="cover", layout_variant="text-only",
                 text_blocks=[TextBlock(content="Titel", role="heading", style_ref="heading")]),
            Page(page_number=2, page_type="section_divider", layout_variant="text-only",
                 text_blocks=[TextBlock(content="Block A", role="heading", style_ref="heading")]),
            Page(page_number=3, page_type="content", layout_variant="text-only",
                 text_blocks=[TextBlock(content="Inhalt", role="heading", style_ref="heading")]),
        ])
        html = _render_html(plan, DesignSystem(), {}, None)
        assert "Titel" in html
        assert "Block A" in html
        assert "Inhalt" in html


# ---------------------------------------------------------------------------
# run()  (integration — mock WeasyPrint)
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# _resolve_photo_path
# ---------------------------------------------------------------------------

class TestResolvePhotoPath:
    def test_processed_path_wins_over_manifest(self, tmp_path):
        s = _settings(tmp_path)
        processed = tmp_path / ".cache" / "processed" / "photo_001.jpg"
        processed.parent.mkdir(parents=True, exist_ok=True)
        processed.write_bytes(b"PROCESSED")
        original = tmp_path / "fotos" / "IMG_original.jpg"
        original.write_bytes(b"ORIGINAL")
        manifest_paths = {"photo_001": original}
        result = _resolve_photo_path(
            Path(".cache/processed/photo_001.jpg"), "photo_001", manifest_paths, s
        )
        assert result is not None
        assert result.read_bytes() == b"PROCESSED"

    def test_falls_back_to_manifest_path_when_no_processed(self, tmp_path):
        s = _settings(tmp_path)
        original = tmp_path / "fotos" / "IMG_original.jpg"
        original.write_bytes(b"ORIGINAL")
        manifest_paths = {"photo_001": original}
        result = _resolve_photo_path(None, "photo_001", manifest_paths, s)
        assert result == original

    def test_returns_none_when_nothing_found(self, tmp_path):
        s = _settings(tmp_path)
        result = _resolve_photo_path(None, "photo_999", {}, s)
        assert result is None

    def test_processed_path_missing_falls_back_to_manifest(self, tmp_path):
        s = _settings(tmp_path)
        original = tmp_path / "fotos" / "IMG_original.jpg"
        original.write_bytes(b"ORIGINAL")
        manifest_paths = {"photo_001": original}
        # processed_path set but file doesn't exist → fall back
        result = _resolve_photo_path(
            Path(".cache/processed/missing.jpg"), "photo_001", manifest_paths, s
        )
        assert result == original


def _mock_weasyprint():
    """Context manager: patch the module-level _weasyprint with a fake that writes %PDF."""
    mock_wp = MagicMock()
    mock_html_instance = MagicMock()
    mock_wp.HTML.return_value = mock_html_instance
    mock_html_instance.write_pdf.side_effect = lambda path, **kw: Path(path).write_bytes(b"%PDF")
    return patch("pipeline.stage5_render._weasyprint", mock_wp)


class TestRun:
    def test_pdf_written_to_output_dir(self, tmp_path):
        s = _settings(tmp_path)
        m = _manifest("Workshop", date(2026, 2, 9))
        with _mock_weasyprint():
            result = run(s, _cover_plan(), _empty_photo_set(), m)
        assert result.exists()
        assert result.suffix == ".pdf"
        assert result.parent == s.output_dir

    def test_pdf_filename_includes_title_and_date(self, tmp_path):
        s = _settings(tmp_path)
        m = _manifest("Gelingensfaktoren", date(2026, 2, 9))
        with _mock_weasyprint():
            result = run(s, _cover_plan(), _empty_photo_set(), m)
        assert "gelingensfaktoren" in result.name
        assert "20260209" in result.name

    def test_design_loaded_from_default_when_absent(self, tmp_path):
        s = _settings(tmp_path)
        with _mock_weasyprint():
            result = run(s, _cover_plan(), _empty_photo_set(), _manifest())
        assert result.exists()

    def test_custom_design_passed_through(self, tmp_path):
        s = _settings(tmp_path)
        with _mock_weasyprint():
            run(s, _cover_plan(), _empty_photo_set(), _manifest(), design=DesignSystem())

    def test_processed_path_used_when_present(self, tmp_path):
        s = _settings(tmp_path)
        processed = tmp_path / ".cache" / "processed" / "photo_001.jpg"
        processed.parent.mkdir(parents=True, exist_ok=True)
        processed.write_bytes(b"FAKEJPEG")

        photo_set = EnrichedPhotoSet(enriched_photos=[
            EnrichedPhoto(
                photo_id="photo_001",
                scene_type="flipchart",
                description="Test",
                topic_keywords=[],
                analysis_model="gpt-5",
                processed_path=Path(".cache/processed/photo_001.jpg"),
            )
        ])
        plan = PagePlan(pages=[
            Page(page_number=1, page_type="content", layout_variant="1-photo",
                 photo_slots=[PhotoSlot(photo_id="photo_001", caption="", display_size="full-width")])
        ])
        rendered_html: list[str] = []
        mock_wp = MagicMock()
        def fake_HTML(string, base_url):
            rendered_html.append(string)
            inst = MagicMock()
            inst.write_pdf.side_effect = lambda path, **kw: Path(path).write_bytes(b"%PDF")
            return inst
        mock_wp.HTML.side_effect = fake_HTML
        with patch("pipeline.stage5_render._weasyprint", mock_wp):
            run(s, plan, photo_set, _manifest())
        assert rendered_html, "HTML should have been rendered"
        assert "photo_001.jpg" in rendered_html[0]

    def test_manifest_original_used_when_no_processed_path(self, tmp_path):
        s = _settings(tmp_path)
        original = tmp_path / "fotos" / "IMG_workshop.jpg"
        original.write_bytes(b"FAKEJPEG")
        from models.manifest import AgendaSession, Photo
        from datetime import datetime, timezone
        _NOW = datetime(2026, 2, 9, 12, 0, tzinfo=timezone.utc)
        photo_set = EnrichedPhotoSet(enriched_photos=[
            EnrichedPhoto(
                photo_id="photo_001",
                scene_type="group",
                description="People",
                topic_keywords=[],
                analysis_model="gpt-5",
                processed_path=None,  # no processed file
            )
        ])
        manifest = ProjectManifest(
            meta=WorkshopMeta(title="Workshop"),
            sessions=[AgendaSession(id="s1", order=1, name="S1")],
            photos=[Photo(
                id="photo_001",
                filename="IMG_workshop.jpg",
                path=Path("fotos/IMG_workshop.jpg"),
                width=800, height=600,
                orientation="landscape",
                timestamp_file=_NOW,
            )],
            text_snippets=[],
        )
        plan = PagePlan(pages=[
            Page(page_number=1, page_type="content", layout_variant="1-photo",
                 photo_slots=[PhotoSlot(photo_id="photo_001", caption="", display_size="full-width")])
        ])
        rendered_html: list[str] = []
        mock_wp = MagicMock()
        def fake_HTML(string, base_url):
            rendered_html.append(string)
            inst = MagicMock()
            inst.write_pdf.side_effect = lambda path, **kw: Path(path).write_bytes(b"%PDF")
            return inst
        mock_wp.HTML.side_effect = fake_HTML
        with patch("pipeline.stage5_render._weasyprint", mock_wp):
            run(s, plan, photo_set, manifest)
        assert rendered_html
        assert "IMG_workshop.jpg" in rendered_html[0]

    def test_output_dir_created_if_missing(self, tmp_path):
        s = _settings(tmp_path)
        (tmp_path / "output").rmdir()
        with _mock_weasyprint():
            result = run(s, _cover_plan(), _empty_photo_set(), _manifest())
        assert result.parent.exists()


# ---------------------------------------------------------------------------
# _validate_pdf_fonts
# ---------------------------------------------------------------------------

def _make_minimal_pdf(tmp_path: Path, with_fonts: bool) -> Path:
    """Create a minimal PDF file for validation tests."""
    import zlib

    if with_fonts:
        # Build an ObjStm containing a FontDescriptor with /FontFile2
        objstm_content = (
            b"1 0\n"
            b"<</Type /FontDescriptor/FontName /ABCDEF+DejaVu-Sans"
            b"/FontFile2 99 0 R>>\n"
        )
        compressed = zlib.compress(objstm_content)
        objstm = (
            b"1 0 obj\n"
            b"<</Type /ObjStm/N 1/First 4/Filter /FlateDecode"
            b"/Length " + str(len(compressed)).encode() + b">>\n"
            b"stream\n" + compressed + b"\nendstream\nendobj\n"
        )
        body = objstm
    else:
        body = b"1 0 obj\n<</Type /Catalog>>\nendobj\n"

    pdf = (
        b"%PDF-1.7\n"
        + body
        + b"xref\n0 2\n0000000000 65535 f \n0000000009 00000 n \n"
        b"trailer\n<</Size 2/Root 1 0 R>>\nstartxref\n"
        + str(len(b"%PDF-1.7\n") + len(body)).encode()
        + b"\n%%EOF\n"
    )
    path = tmp_path / "test.pdf"
    path.write_bytes(pdf)
    return path


class TestValidatePdfFonts:
    def test_warns_when_no_fonts_embedded(self, tmp_path, caplog):
        import logging
        pdf = _make_minimal_pdf(tmp_path, with_fonts=False)
        with caplog.at_level(logging.WARNING, logger="pipeline.stage5_render"):
            _validate_pdf_fonts(pdf, "DejaVu Sans")
        assert any("FAILED" in r.message for r in caplog.records)

    def test_ok_when_fonts_embedded(self, tmp_path, caplog):
        import logging
        pdf = _make_minimal_pdf(tmp_path, with_fonts=True)
        with caplog.at_level(logging.INFO, logger="pipeline.stage5_render"):
            _validate_pdf_fonts(pdf, "DejaVu Sans")
        assert not any("FAILED" in r.message for r in caplog.records)
        assert any("OK" in r.message for r in caplog.records)

    def test_subset_name_detected(self, tmp_path, caplog):
        import logging
        pdf = _make_minimal_pdf(tmp_path, with_fonts=True)
        with caplog.at_level(logging.INFO, logger="pipeline.stage5_render"):
            _validate_pdf_fonts(pdf, "DejaVu Sans")
        ok_records = [r for r in caplog.records if "OK" in r.message]
        assert ok_records
        assert "ABCDEF+DejaVu-Sans" in ok_records[0].message

    def test_run_calls_validation(self, tmp_path):
        s = _settings(tmp_path)
        with _mock_weasyprint(), patch(
            "pipeline.stage5_render._validate_pdf_fonts"
        ) as mock_validate:
            result = run(s, _cover_plan(), _empty_photo_set(), _manifest())
        mock_validate.assert_called_once_with(result, "DejaVu Sans")
