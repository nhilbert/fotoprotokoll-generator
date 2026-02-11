"""Round-trip and validation tests for all stage contract models."""

from datetime import date, datetime, time, timezone
from pathlib import Path

import pytest
from pydantic import ValidationError

from models.content_plan import ContentItem, ContentPlan, _TEMPORAL_WEIGHT, _SEMANTIC_WEIGHT
from models.enriched_photos import EnrichedPhoto, EnrichedPhotoSet, PhotoAnalysis
from models.events import PipelineEvent
from models.manifest import AgendaSession, Photo, ProjectManifest, TextSnippet, WorkshopMeta
from models.page_plan import Page, PagePlan, PhotoSlot, TextBlock


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def round_trip(model_instance):
    """Serialize to JSON and deserialize back; return the reconstructed instance."""
    json_str = model_instance.model_dump_json()
    return type(model_instance).model_validate_json(json_str)


# ---------------------------------------------------------------------------
# WorkshopMeta
# ---------------------------------------------------------------------------

class TestWorkshopMeta:
    def test_minimal(self):
        m = WorkshopMeta(title="Test-Workshop")
        assert m.title == "Test-Workshop"
        assert m.workshop_date is None
        assert m.participants is None

    def test_full(self):
        m = WorkshopMeta(title="Workshop", workshop_date=date(2026, 2, 9),
                         location="Berlin", participants=12)
        assert m.participants == 12

    def test_round_trip(self):
        m = WorkshopMeta(title="Workshop", workshop_date=date(2026, 2, 9),
                         location="Berlin", participants=12)
        assert round_trip(m) == m


# ---------------------------------------------------------------------------
# AgendaSession
# ---------------------------------------------------------------------------

class TestAgendaSession:
    def test_minimal(self):
        s = AgendaSession(id="s1", order=1, name="Begrüßung")
        assert s.start_time is None

    def test_with_times(self):
        s = AgendaSession(id="s1", order=1, name="Begrüßung",
                          start_time=time(9, 0), end_time=time(10, 0))
        assert s.start_time == time(9, 0)

    def test_round_trip(self):
        s = AgendaSession(id="s1", order=1, name="Begrüßung",
                          start_time=time(9, 0), end_time=time(10, 0))
        assert round_trip(s) == s


# ---------------------------------------------------------------------------
# Photo — timezone handling and path storage
# ---------------------------------------------------------------------------

class TestPhoto:
    def _make_photo(self, **kwargs):
        defaults = dict(
            id="photo_001",
            filename="IMG_001.jpg",
            path=Path("fotos/IMG_001.jpg"),  # relative path
            timestamp_file=datetime(2026, 2, 9, 9, 0, 0, tzinfo=timezone.utc),
            width=4032,
            height=3024,
            orientation="landscape",
        )
        return Photo(**{**defaults, **kwargs})

    def test_path_is_relative(self):
        p = self._make_photo()
        assert not p.path.is_absolute()
        assert str(p.path) == "fotos/IMG_001.jpg"

    def test_naive_timestamp_file_converted_to_utc(self):
        naive = datetime(2026, 2, 9, 9, 0, 0)  # no tzinfo
        p = self._make_photo(timestamp_file=naive)
        assert p.timestamp_file.tzinfo == timezone.utc

    def test_naive_timestamp_exif_converted_to_utc(self):
        naive = datetime(2026, 2, 9, 10, 30, 0)
        p = self._make_photo(timestamp_exif=naive)
        assert p.timestamp_exif.tzinfo == timezone.utc

    def test_aware_timestamp_preserved(self):
        aware = datetime(2026, 2, 9, 9, 0, 0, tzinfo=timezone.utc)
        p = self._make_photo(timestamp_file=aware)
        assert p.timestamp_file == aware

    def test_best_timestamp_prefers_exif(self):
        exif_ts = datetime(2026, 2, 9, 10, 30, tzinfo=timezone.utc)
        file_ts = datetime(2026, 2, 9, 12, 0, tzinfo=timezone.utc)
        p = self._make_photo(timestamp_exif=exif_ts, timestamp_file=file_ts)
        assert p.best_timestamp == exif_ts

    def test_best_timestamp_falls_back_to_file_mtime(self):
        file_ts = datetime(2026, 2, 9, 12, 0, tzinfo=timezone.utc)
        p = self._make_photo(timestamp_exif=None, timestamp_file=file_ts)
        assert p.best_timestamp == file_ts

    def test_invalid_width_raises(self):
        with pytest.raises(ValidationError):
            self._make_photo(width=0)

    def test_invalid_height_raises(self):
        with pytest.raises(ValidationError):
            self._make_photo(height=-1)

    def test_round_trip(self):
        p = self._make_photo(timestamp_exif=datetime(2026, 2, 9, 9, 0, tzinfo=timezone.utc))
        assert round_trip(p) == p


# ---------------------------------------------------------------------------
# TextSnippet
# ---------------------------------------------------------------------------

class TestTextSnippet:
    def test_round_trip(self):
        s = TextSnippet(id="t1", filename="notes.md",
                        content="Ergebnis: drei Themen.", word_count=4)
        assert round_trip(s) == s

    def test_negative_word_count_raises(self):
        with pytest.raises(ValidationError):
            TextSnippet(id="t1", filename="notes.md", content="x", word_count=-1)


# ---------------------------------------------------------------------------
# ProjectManifest
# ---------------------------------------------------------------------------

class TestProjectManifest:
    def test_empty_manifest(self):
        m = ProjectManifest(meta=WorkshopMeta(title="Test"))
        assert m.photos == []
        assert m.sessions == []
        assert m.text_snippets == []

    def test_round_trip(self):
        m = ProjectManifest(
            meta=WorkshopMeta(title="Workshop", workshop_date=date(2026, 2, 9)),
            sessions=[AgendaSession(id="s1", order=1, name="Einstieg")],
            photos=[],
            text_snippets=[TextSnippet(id="t1", filename="notes.md",
                                       content="x", word_count=1)],
        )
        assert round_trip(m) == m



# ---------------------------------------------------------------------------
# PhotoAnalysis — OpenAI strict-mode schema validation
# ---------------------------------------------------------------------------

class TestPhotoAnalysis:
    def test_all_scene_types(self):
        for scene in ("flipchart", "group", "activity", "result", "unknown"):
            a = PhotoAnalysis(scene_type=scene, description="Test.")
            assert a.scene_type == scene

    def test_invalid_scene_type_raises(self):
        with pytest.raises(ValidationError):
            PhotoAnalysis(scene_type="selfie", description="Test.")

    def test_round_trip(self):
        a = PhotoAnalysis(
            scene_type="flipchart",
            description="Ein Flipchart mit Stichpunkten.",
            ocr_text="Thema 1\nThema 2",
            topic_keywords=["Kommunikation", "Prozesse"],
        )
        assert round_trip(a) == a

    def test_schema_all_properties_in_required(self):
        """OpenAI strict mode requires all properties in required[]."""
        schema = PhotoAnalysis.model_json_schema()
        props = set(schema.get("properties", {}).keys())
        required = set(schema.get("required", []))
        assert props == required, f"Missing from required: {props - required}"

    def test_schema_additional_properties_false(self):
        """OpenAI strict mode requires additionalProperties: false."""
        schema = PhotoAnalysis.model_json_schema()
        assert schema.get("additionalProperties") is False

    def test_schema_nullable_field_uses_any_of(self):
        """OpenAI strict mode expects nullable as anyOf: [type, null]."""
        schema = PhotoAnalysis.model_json_schema()
        ocr_text_schema = schema["properties"]["ocr_text"]
        assert "anyOf" in ocr_text_schema
        types = {item.get("type") for item in ocr_text_schema["anyOf"]}
        assert types == {"string", "null"}


# ---------------------------------------------------------------------------
# EnrichedPhoto / EnrichedPhotoSet
# ---------------------------------------------------------------------------

class TestEnrichedPhotos:
    def test_from_analysis_factory(self):
        analysis = PhotoAnalysis(
            scene_type="flipchart",
            description="Ein Flipchart.",
            ocr_text="Stichpunkt A",
            topic_keywords=["Thema"],
        )
        enriched = EnrichedPhoto.from_analysis("photo_001", analysis, "gpt-5")
        assert enriched.photo_id == "photo_001"
        assert enriched.scene_type == analysis.scene_type
        assert enriched.ocr_text == analysis.ocr_text
        assert enriched.analysis_model == "gpt-5"

    def test_enriched_photo_round_trip(self):
        e = EnrichedPhoto(
            photo_id="photo_001",
            scene_type="group",
            description="Gruppenarbeit im Plenum.",
            ocr_text=None,
            topic_keywords=["Gruppe", "Arbeit"],
            analysis_model="gpt-5",
        )
        assert round_trip(e) == e

    def test_enriched_photo_set_by_photo_id(self):
        e = EnrichedPhoto(photo_id="photo_001", scene_type="activity",
                          description="x", analysis_model="gpt-5")
        es = EnrichedPhotoSet(enriched_photos=[e])
        assert es.by_photo_id("photo_001") is e
        assert es.by_photo_id("missing") is None

    def test_enriched_photo_set_round_trip(self):
        es = EnrichedPhotoSet(enriched_photos=[
            EnrichedPhoto(photo_id="p1", scene_type="result",
                          description="Ergebnis.", analysis_model="gpt-5"),
        ])
        assert round_trip(es) == es


# ---------------------------------------------------------------------------
# ContentItem / ContentPlan — auto-computed combined_confidence
# ---------------------------------------------------------------------------

class TestContentPlan:
    def _make_item(self, **kwargs):
        defaults = dict(
            id="item_001",
            session_ref="s1",
            heading="Begrüßung und Einstieg",
            photo_ids=["photo_001"],
            text_snippet_ref=None,
            temporal_confidence=0.9,
            semantic_confidence=0.5,
            needs_review=False,
        )
        return ContentItem(**{**defaults, **kwargs})

    def test_combined_confidence_is_computed(self):
        item = self._make_item(temporal_confidence=1.0, semantic_confidence=0.0)
        assert item.combined_confidence == pytest.approx(_TEMPORAL_WEIGHT)

    def test_combined_confidence_weighted_average(self):
        item = self._make_item(temporal_confidence=0.8, semantic_confidence=0.6)
        expected = round(_TEMPORAL_WEIGHT * 0.8 + _SEMANTIC_WEIGHT * 0.6, 4)
        assert item.combined_confidence == pytest.approx(expected)

    def test_combined_confidence_cannot_be_set_directly(self):
        """combined_confidence is a computed_field — passing it in has no effect."""
        item = ContentItem(
            id="i1", session_ref="s1", heading="Test",
            temporal_confidence=1.0, semantic_confidence=1.0,
            needs_review=False,
        )
        # Should always equal the computed value, not any injected value
        assert item.combined_confidence == pytest.approx(1.0)

    def test_confidence_out_of_range_raises(self):
        with pytest.raises(ValidationError):
            self._make_item(temporal_confidence=1.1)
        with pytest.raises(ValidationError):
            self._make_item(semantic_confidence=-0.1)

    def test_needs_review_flag(self):
        item = self._make_item(temporal_confidence=0.3, semantic_confidence=0.2,
                               needs_review=True)
        assert item.needs_review is True

    def test_combined_confidence_in_json(self):
        """combined_confidence must appear in serialized JSON for inspection."""
        item = self._make_item(temporal_confidence=0.8, semantic_confidence=0.6)
        data = item.model_dump()
        assert "combined_confidence" in data

    def test_round_trip(self):
        plan = ContentPlan(items=[self._make_item(), self._make_item(id="item_002")])
        assert round_trip(plan) == plan


# ---------------------------------------------------------------------------
# PhotoSlot / TextBlock / Page / PagePlan
# ---------------------------------------------------------------------------

class TestPagePlan:
    def test_page_number_must_be_positive(self):
        with pytest.raises(ValidationError):
            Page(page_number=0, page_type="cover", layout_variant="text-only")

    def test_cover_page_no_photos(self):
        page = Page(page_number=1, page_type="cover", layout_variant="text-only",
                    text_blocks=[TextBlock(content="Workshop", role="heading",
                                          style_ref="heading")])
        assert page.photo_slots == []

    def test_content_page_with_photo(self):
        page = Page(
            page_number=2,
            page_type="content",
            layout_variant="1-photo",
            content_item_ref="item_001",
            photo_slots=[PhotoSlot(photo_id="photo_001", caption="Gruppenarbeit",
                                   display_size="full-width")],
            text_blocks=[TextBlock(content="Einstieg", role="heading",
                                   style_ref="heading")],
        )
        assert len(page.photo_slots) == 1
        assert page.photo_slots[0].display_size == "full-width"

    def test_invalid_display_size_raises(self):
        with pytest.raises(ValidationError):
            PhotoSlot(photo_id="p1", caption="x", display_size="thumbnail")

    def test_round_trip(self):
        plan = PagePlan(pages=[
            Page(page_number=1, page_type="cover", layout_variant="text-only"),
            Page(page_number=2, page_type="content", layout_variant="1-photo",
                 content_item_ref="item_001",
                 photo_slots=[PhotoSlot(photo_id="p1", caption="Test",
                                        display_size="full-width")]),
            Page(page_number=3, page_type="closing", layout_variant="text-only"),
        ])
        assert round_trip(plan) == plan


# ---------------------------------------------------------------------------
# PipelineEvent
# ---------------------------------------------------------------------------

class TestPipelineEvent:
    def test_minimal(self):
        e = PipelineEvent(stage="stage1", step="reading_agenda", progress=0.0,
                          message="Agenda wird gelesen…")
        assert e.payload is None

    def test_with_payload(self):
        e = PipelineEvent(stage="stage3a", step="analyzing_photo", progress=0.5,
                          message="Foto 5 von 10 wird analysiert…",
                          payload={"photo_id": "photo_005"})
        assert e.payload["photo_id"] == "photo_005"

    def test_progress_out_of_range_raises(self):
        with pytest.raises(ValidationError):
            PipelineEvent(stage="s", step="x", progress=1.1, message="x")

    def test_round_trip(self):
        e = PipelineEvent(stage="stage2", step="cropping", progress=0.75,
                          message="Flipchart wird zugeschnitten…",
                          payload={"photo_id": "photo_003"})
        assert round_trip(e) == e
