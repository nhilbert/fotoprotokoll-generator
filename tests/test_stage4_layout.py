"""Tests for Stage 4 Layout Planning."""
from datetime import date
from pathlib import Path

import pytest

from datetime import datetime, timezone

from models.content_plan import ContentItem, ContentPlan
from models.enriched_photos import CropBox, EnrichedPhoto, EnrichedPhotoSet
from models.manifest import AgendaSession, Photo, ProjectManifest, WorkshopMeta
from models.page_plan import Page, PagePlan
from pipeline.stage4_layout import _format_date_de, run
from settings import Settings

_NOW = datetime(2026, 2, 9, 12, 0, tzinfo=timezone.utc)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _settings(tmp_path, max_photos_per_page=2, section_dividers=False) -> Settings:
    for d in ("agenda", "fotos", "text", "template"):
        (tmp_path / d).mkdir(exist_ok=True)
    return Settings(
        openai_api_key="test",
        project_dir=tmp_path,
        max_photos_per_page=max_photos_per_page,
        section_dividers=section_dividers,
    )


def _manifest(title="Workshop", workshop_date=None, location=None) -> ProjectManifest:
    return ProjectManifest(
        meta=WorkshopMeta(title=title, workshop_date=workshop_date, location=location),
        sessions=[AgendaSession(id="session_001", order=1, name=title)],
        photos=[],
        text_snippets=[],
    )


def _manifest_with_photos(photo_orientations: dict[str, str]) -> ProjectManifest:
    """Build a manifest containing Photo entries with given orientations."""
    photos = [
        Photo(
            id=pid,
            filename=f"{pid}.jpg",
            path=Path(f"fotos/{pid}.jpg"),
            width=800 if orientation == "landscape" else 600,
            height=600 if orientation == "landscape" else 800,
            orientation=orientation,
            timestamp_file=_NOW,
        )
        for pid, orientation in photo_orientations.items()
    ]
    return ProjectManifest(
        meta=WorkshopMeta(title="Workshop"),
        sessions=[AgendaSession(id="session_001", order=1, name="Workshop")],
        photos=photos,
        text_snippets=[],
    )


def _item(
    item_id="item_001",
    session_ref="session_001",
    heading="Workshop",
    photo_ids=None,
) -> ContentItem:
    return ContentItem(
        id=item_id,
        session_ref=session_ref,
        heading=heading,
        photo_ids=photo_ids or [],
        temporal_confidence=0.8,
        semantic_confidence=0.8,
        needs_review=False,
    )


def _enriched(
    photo_id="photo_001",
    description="Ein Flipchart.",
    crop_box=None,
) -> EnrichedPhoto:
    return EnrichedPhoto(
        photo_id=photo_id,
        scene_type="flipchart",
        description=description,
        analysis_model="gpt-5",
        crop_box=crop_box,
    )


def _photo_set(enriched_list) -> EnrichedPhotoSet:
    return EnrichedPhotoSet(enriched_photos=enriched_list)


def _plan(items) -> ContentPlan:
    return ContentPlan(items=items)


# ---------------------------------------------------------------------------
# Cover page
# ---------------------------------------------------------------------------

class TestCoverPage:
    def test_first_page_is_cover(self, tmp_path):
        s = _settings(tmp_path)
        plan = run(s, _manifest(), _plan([]), _photo_set([]))
        assert plan.pages[0].page_type == "cover"

    def test_cover_has_title_block(self, tmp_path):
        s = _settings(tmp_path)
        plan = run(s, _manifest(title="Gelingensfaktoren"), _plan([]), _photo_set([]))
        cover = plan.pages[0]
        headings = [b for b in cover.text_blocks if b.role == "heading"]
        assert any("Gelingensfaktoren" in b.content for b in headings)

    def test_cover_includes_date_when_present(self, tmp_path):
        s = _settings(tmp_path)
        manifest = _manifest(workshop_date=date(2026, 2, 9))
        plan = run(s, manifest, _plan([]), _photo_set([]))
        cover = plan.pages[0]
        all_text = " ".join(b.content for b in cover.text_blocks)
        assert "2026" in all_text

    def test_cover_omits_date_when_absent(self, tmp_path):
        s = _settings(tmp_path)
        plan = run(s, _manifest(), _plan([]), _photo_set([]))
        # Only the title block, no date
        assert len(plan.pages[0].text_blocks) == 1

    def test_cover_includes_location_when_present(self, tmp_path):
        s = _settings(tmp_path)
        manifest = _manifest(location="Berlin")
        plan = run(s, manifest, _plan([]), _photo_set([]))
        all_text = " ".join(b.content for b in plan.pages[0].text_blocks)
        assert "Berlin" in all_text


# ---------------------------------------------------------------------------
# Page numbering
# ---------------------------------------------------------------------------

class TestPageNumbering:
    def test_pages_are_numbered_sequentially(self, tmp_path):
        s = _settings(tmp_path)
        photos = ["photo_001", "photo_002", "photo_003"]
        enriched = [_enriched(pid) for pid in photos]
        item = _item(photo_ids=photos)
        plan = run(s, _manifest(), _plan([item]), _photo_set(enriched))
        for i, page in enumerate(plan.pages, start=1):
            assert page.page_number == i

    def test_no_gaps_in_page_numbers(self, tmp_path):
        s = _settings(tmp_path, section_dividers=True)
        item = _item(photo_ids=["photo_001"])
        enriched = [_enriched("photo_001")]
        plan = run(s, _manifest(), _plan([item]), _photo_set(enriched))
        numbers = [p.page_number for p in plan.pages]
        assert numbers == list(range(1, len(numbers) + 1))


# ---------------------------------------------------------------------------
# Section dividers
# ---------------------------------------------------------------------------

class TestSectionDividers:
    def test_section_divider_inserted_when_enabled(self, tmp_path):
        s = _settings(tmp_path, section_dividers=True)
        item = _item(heading="Morgen-Block")
        plan = run(s, _manifest(), _plan([item]), _photo_set([]))
        dividers = [p for p in plan.pages if p.page_type == "section_divider"]
        assert len(dividers) == 1
        assert dividers[0].text_blocks[0].content == "Morgen-Block"

    def test_no_section_divider_when_disabled(self, tmp_path):
        s = _settings(tmp_path, section_dividers=False)
        item = _item(heading="Morgen-Block")
        plan = run(s, _manifest(), _plan([item]), _photo_set([]))
        dividers = [p for p in plan.pages if p.page_type == "section_divider"]
        assert len(dividers) == 0

    def test_multiple_items_each_get_divider(self, tmp_path):
        s = _settings(tmp_path, section_dividers=True)
        items = [_item("item_001", heading="Block A"), _item("item_002", "session_002", "Block B")]
        plan = run(s, _manifest(), _plan(items), _photo_set([]))
        dividers = [p for p in plan.pages if p.page_type == "section_divider"]
        assert len(dividers) == 2


# ---------------------------------------------------------------------------
# Content pages — photo distribution
# ---------------------------------------------------------------------------

class TestContentPages:
    def test_single_photo_produces_one_content_page(self, tmp_path):
        s = _settings(tmp_path)
        item = _item(photo_ids=["photo_001"])
        plan = run(s, _manifest(), _plan([item]), _photo_set([_enriched("photo_001")]))
        content = [p for p in plan.pages if p.page_type == "content"]
        assert len(content) == 1

    def test_two_photos_on_one_page_when_max_is_two(self, tmp_path):
        s = _settings(tmp_path, max_photos_per_page=2)
        item = _item(photo_ids=["photo_001", "photo_002"])
        enriched = [_enriched("photo_001"), _enriched("photo_002")]
        plan = run(s, _manifest(), _plan([item]), _photo_set(enriched))
        content = [p for p in plan.pages if p.page_type == "content"]
        assert len(content) == 1
        assert len(content[0].photo_slots) == 2

    def test_three_photos_split_across_two_pages(self, tmp_path):
        s = _settings(tmp_path, max_photos_per_page=2)
        photos = ["photo_001", "photo_002", "photo_003"]
        item = _item(photo_ids=photos)
        enriched = [_enriched(pid) for pid in photos]
        plan = run(s, _manifest(), _plan([item]), _photo_set(enriched))
        content = [p for p in plan.pages if p.page_type == "content"]
        assert len(content) == 2
        assert len(content[0].photo_slots) == 2
        assert len(content[1].photo_slots) == 1

    def test_max_one_photo_per_page(self, tmp_path):
        s = _settings(tmp_path, max_photos_per_page=1)
        photos = ["photo_001", "photo_002"]
        item = _item(photo_ids=photos)
        enriched = [_enriched(pid) for pid in photos]
        plan = run(s, _manifest(), _plan([item]), _photo_set(enriched))
        content = [p for p in plan.pages if p.page_type == "content"]
        assert len(content) == 2

    def test_no_photos_produces_text_only_page(self, tmp_path):
        s = _settings(tmp_path)
        item = _item(photo_ids=[])
        plan = run(s, _manifest(), _plan([item]), _photo_set([]))
        content = [p for p in plan.pages if p.page_type == "content"]
        assert len(content) == 1
        assert content[0].layout_variant == "text-only"

    def test_heading_appears_on_first_content_page_only(self, tmp_path):
        s = _settings(tmp_path, max_photos_per_page=1)
        photos = ["photo_001", "photo_002"]
        item = _item(heading="Ergebnisse", photo_ids=photos)
        enriched = [_enriched(pid) for pid in photos]
        plan = run(s, _manifest(), _plan([item]), _photo_set(enriched))
        content = [p for p in plan.pages if p.page_type == "content"]
        first_headings = [b for b in content[0].text_blocks if b.role == "heading"]
        second_headings = [b for b in content[1].text_blocks if b.role == "heading"]
        assert len(first_headings) == 1
        assert first_headings[0].content == "Ergebnisse"
        assert len(second_headings) == 0


# ---------------------------------------------------------------------------
# Layout variants
# ---------------------------------------------------------------------------

class TestLayoutVariants:
    def test_single_landscape_is_1photo_fullwidth(self, tmp_path):
        s = _settings(tmp_path)
        manifest = _manifest_with_photos({"photo_001": "landscape"})
        item = _item(photo_ids=["photo_001"])
        plan = run(s, manifest, _plan([item]), _photo_set([_enriched("photo_001")]))
        content = [p for p in plan.pages if p.page_type == "content"][0]
        assert content.layout_variant == "1-photo"
        assert content.photo_slots[0].display_size == "full-width"

    def test_single_portrait_is_1photo_portrait_pair(self, tmp_path):
        s = _settings(tmp_path)
        manifest = _manifest_with_photos({"photo_001": "portrait"})
        item = _item(photo_ids=["photo_001"])
        plan = run(s, manifest, _plan([item]), _photo_set([_enriched("photo_001")]))
        content = [p for p in plan.pages if p.page_type == "content"][0]
        assert content.layout_variant == "1-photo"
        assert content.photo_slots[0].display_size == "portrait-pair"

    def test_manifest_orientation_takes_priority_over_crop_box(self, tmp_path):
        # Photo is portrait in manifest, but crop_box says landscape — manifest wins
        s = _settings(tmp_path)
        manifest = _manifest_with_photos({"photo_001": "portrait"})
        landscape_cb = CropBox(x_min=0.1, y_min=0.2, x_max=0.9, y_max=0.8)
        item = _item(photo_ids=["photo_001"])
        plan = run(s, manifest, _plan([item]),
                   _photo_set([_enriched("photo_001", crop_box=landscape_cb)]))
        content = [p for p in plan.pages if p.page_type == "content"][0]
        assert content.photo_slots[0].display_size == "portrait-pair"

    def test_two_photos_is_2photo(self, tmp_path):
        s = _settings(tmp_path, max_photos_per_page=2)
        item = _item(photo_ids=["photo_001", "photo_002"])
        enriched = [_enriched("photo_001"), _enriched("photo_002")]
        plan = run(s, _manifest(), _plan([item]), _photo_set(enriched))
        content = [p for p in plan.pages if p.page_type == "content"][0]
        assert content.layout_variant == "2-photo"


# ---------------------------------------------------------------------------
# Date formatting
# ---------------------------------------------------------------------------

class TestDateFormatting:
    def test_german_month_name(self):
        assert _format_date_de(date(2026, 2, 9)) == "9. Februar 2026"

    def test_no_leading_zero_on_day(self):
        assert _format_date_de(date(2026, 1, 3)).startswith("3.")

    def test_all_months_in_german(self):
        expected = [
            "Januar", "Februar", "März", "April", "Mai", "Juni",
            "Juli", "August", "September", "Oktober", "November", "Dezember",
        ]
        for month_num, name in enumerate(expected, start=1):
            d = date(2026, month_num, 1)
            assert name in _format_date_de(d)

    def test_cover_date_uses_german_month(self, tmp_path):
        s = _settings(tmp_path)
        manifest = _manifest(workshop_date=date(2026, 2, 9))
        plan = run(s, manifest, _plan([]), _photo_set([]))
        cover_text = " ".join(b.content for b in plan.pages[0].text_blocks)
        assert "Februar" in cover_text
        assert "February" not in cover_text


# ---------------------------------------------------------------------------
# Photo captions
# ---------------------------------------------------------------------------

class TestCaptions:
    def test_caption_comes_from_enriched_description(self, tmp_path):
        s = _settings(tmp_path)
        enriched = _enriched("photo_001", description="Moderationskarten zum Thema Vernetzung.")
        item = _item(photo_ids=["photo_001"])
        plan = run(s, _manifest(), _plan([item]), _photo_set([enriched]))
        slot = plan.pages[1].photo_slots[0]
        assert slot.caption == "Moderationskarten zum Thema Vernetzung."

    def test_missing_enriched_gives_empty_caption(self, tmp_path):
        s = _settings(tmp_path)
        item = _item(photo_ids=["photo_001"])
        plan = run(s, _manifest(), _plan([item]), _photo_set([]))  # no enriched data
        slot = plan.pages[1].photo_slots[0]
        assert slot.caption == ""


# ---------------------------------------------------------------------------
# Artifact
# ---------------------------------------------------------------------------

class TestArtifact:
    def test_artifact_written(self, tmp_path):
        s = _settings(tmp_path)
        run(s, _manifest(), _plan([]), _photo_set([]))
        assert (s.cache_dir / "page_plan.json").exists()

    def test_artifact_roundtrips(self, tmp_path):
        s = _settings(tmp_path)
        item = _item(photo_ids=["photo_001"])
        result = run(s, _manifest(), _plan([item]), _photo_set([_enriched("photo_001")]))
        loaded = PagePlan.model_validate_json(
            (s.cache_dir / "page_plan.json").read_text()
        )
        assert len(loaded.pages) == len(result.pages)
        assert loaded.pages[0].page_type == "cover"
