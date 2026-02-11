"""Tests for Stage 3b Matching."""
from datetime import date, datetime, time, timezone
from pathlib import Path

import pytest

from models.content_plan import ContentItem, ContentPlan
from models.enriched_photos import EnrichedPhoto, EnrichedPhotoSet
from models.manifest import AgendaSession, Photo, ProjectManifest, TextSnippet, WorkshopMeta
from pipeline.stage3b_match import (
    _semantic_score,
    _temporal_score,
    _tokenize,
    run,
)
from settings import Settings


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------

def _settings(tmp_path) -> Settings:
    for d in ("agenda", "fotos", "text", "template"):
        (tmp_path / d).mkdir(exist_ok=True)
    return Settings(openai_api_key="test", project_dir=tmp_path)


def _photo(
    photo_id="photo_001",
    filename="img.jpg",
    ts: datetime | None = None,
) -> Photo:
    return Photo(
        id=photo_id,
        filename=filename,
        path=Path(f"fotos/{filename}"),
        width=800, height=600, orientation="landscape",
        timestamp_exif=ts,
        timestamp_file=ts or datetime(2026, 2, 9, 10, 0, tzinfo=timezone.utc),
    )


def _enriched(
    photo_id="photo_001",
    keywords=None,
    ocr_text="",
    description="",
) -> EnrichedPhoto:
    return EnrichedPhoto(
        photo_id=photo_id,
        scene_type="result",
        description=description,
        ocr_text=ocr_text,
        topic_keywords=keywords or [],
        analysis_model="gpt-5",
    )


def _session(
    session_id="session_001",
    order=1,
    name="Workshop",
    start: time | None = None,
    end: time | None = None,
) -> AgendaSession:
    return AgendaSession(id=session_id, order=order, name=name,
                         start_time=start, end_time=end)


def _manifest(sessions, photos, snippets=None) -> ProjectManifest:
    return ProjectManifest(
        meta=WorkshopMeta(title="Test"),
        sessions=sessions,
        photos=photos,
        text_snippets=snippets or [],
    )


def _photo_set(enriched_list) -> EnrichedPhotoSet:
    return EnrichedPhotoSet(enriched_photos=enriched_list)


# ---------------------------------------------------------------------------
# Temporal scoring
# ---------------------------------------------------------------------------

class TestTemporalScore:
    def test_no_timestamp_returns_neutral(self):
        photo = _photo(ts=None)
        photo = photo.model_copy(update={"timestamp_file": None, "timestamp_exif": None})
        session = _session(start=time(9, 0), end=time(10, 0))
        assert _temporal_score(photo, session, [session]) == 0.5

    def test_timestamp_in_window_returns_one(self):
        ts = datetime(2026, 2, 9, 9, 30, tzinfo=timezone.utc)
        photo = _photo(ts=ts)
        session = _session(start=time(9, 0), end=time(10, 0))
        assert _temporal_score(photo, session, [session]) == 1.0

    def test_timestamp_outside_window_returns_less_than_one(self):
        ts = datetime(2026, 2, 9, 11, 0, tzinfo=timezone.utc)
        photo = _photo(ts=ts)
        session = _session(start=time(9, 0), end=time(10, 0))
        score = _temporal_score(photo, session, [session])
        assert score < 1.0

    def test_no_session_times_returns_neutral(self):
        ts = datetime(2026, 2, 9, 9, 30, tzinfo=timezone.utc)
        photo = _photo(ts=ts)
        session = _session()  # no times
        assert _temporal_score(photo, session, [session]) == 0.5

    def test_prefers_matching_session_over_non_matching(self):
        ts = datetime(2026, 2, 9, 9, 30, tzinfo=timezone.utc)
        photo = _photo(ts=ts)
        s1 = _session("session_001", 1, "Morning", time(9, 0), time(10, 0))
        s2 = _session("session_002", 2, "Afternoon", time(13, 0), time(14, 0))
        score_s1 = _temporal_score(photo, s1, [s1, s2])
        score_s2 = _temporal_score(photo, s2, [s1, s2])
        assert score_s1 > score_s2

    def test_open_ended_last_session_uses_90min_window(self):
        ts = datetime(2026, 2, 9, 12, 30, tzinfo=timezone.utc)
        photo = _photo(ts=ts)
        session = _session(start=time(12, 0))  # no end time
        assert _temporal_score(photo, session, [session]) == 1.0

    def test_session_without_time_gets_low_score_when_others_have_times(self):
        ts = datetime(2026, 2, 9, 9, 30, tzinfo=timezone.utc)
        photo = _photo(ts=ts)
        s_timed = _session("session_001", 1, "Timed", time(9, 0), time(10, 0))
        s_untimed = _session("session_002", 2, "Untimed")
        score = _temporal_score(photo, s_untimed, [s_timed, s_untimed])
        assert score == 0.1


# ---------------------------------------------------------------------------
# Semantic scoring
# ---------------------------------------------------------------------------

class TestSemanticScore:
    def test_no_enriched_returns_floor(self):
        session = _session(name="Vernetzung")
        assert _semantic_score(None, session, []) == 0.1

    def test_matching_keywords_increase_score(self):
        enriched = _enriched(keywords=["vernetzung", "team", "kommunikation"])
        session = _session(name="Vernetzung und Kommunikation")
        score = _semantic_score(enriched, session, [])
        assert score > 0.1

    def test_no_overlap_returns_floor(self):
        enriched = _enriched(keywords=["sport", "musik", "kunst"])
        session = _session(name="Datenschutz Compliance Richtlinien")
        score = _semantic_score(enriched, session, [])
        assert score == 0.1

    def test_ocr_text_contributes_to_score(self):
        enriched = _enriched(keywords=[], ocr_text="Gemeinsame Verantwortung übernehmen")
        session = _session(name="Gemeinsame Verantwortung")
        score = _semantic_score(enriched, session, [])
        assert score > 0.1

    def test_text_snippets_contribute_to_score(self):
        enriched = _enriched(keywords=["onboarding", "willkommen"])
        session = _session(name="Einstieg")
        snippet = TextSnippet(
            id="text_001", filename="notes.md",
            content="Onboarding und Willkommenskultur sind wichtig",
            word_count=7,
        )
        score_with = _semantic_score(enriched, session, [snippet])
        score_without = _semantic_score(enriched, session, [])
        assert score_with > score_without


# ---------------------------------------------------------------------------
# Tokenizer
# ---------------------------------------------------------------------------

class TestTokenize:
    def test_lowercases(self):
        assert "workshop" in _tokenize("Workshop")

    def test_filters_single_chars(self):
        tokens = _tokenize("a b Workshop")
        assert "a" not in tokens
        assert "b" not in tokens
        assert "workshop" in tokens

    def test_keeps_two_char_abbreviations(self):
        # German workshop abbreviations like OGS, KL, SL are meaningful
        tokens = _tokenize("KL und OGS arbeiten zusammen")
        assert "kl" in tokens
        assert "og" not in tokens  # OGS → "ogs" (3 chars, kept), "og" not a word
        assert "ogs" in tokens

    def test_handles_empty(self):
        assert _tokenize("") == set()


# ---------------------------------------------------------------------------
# run() — single session
# ---------------------------------------------------------------------------

class TestRunSingleSession:
    def test_all_photos_assigned_to_only_session(self, tmp_path):
        s = _settings(tmp_path)
        photos = [_photo("photo_001"), _photo("photo_002", "b.jpg")]
        enriched = [_enriched("photo_001"), _enriched("photo_002")]
        manifest = _manifest([_session()], photos)
        plan = run(s, manifest, _photo_set(enriched))

        assert len(plan.items) == 1
        assert set(plan.items[0].photo_ids) == {"photo_001", "photo_002"}

    def test_heading_is_session_name(self, tmp_path):
        s = _settings(tmp_path)
        session = _session(name="Gelingensfaktoren")
        plan = run(s, _manifest([session], [_photo()]), _photo_set([_enriched()]))
        assert plan.items[0].heading == "Gelingensfaktoren"

    def test_session_ref_matches(self, tmp_path):
        s = _settings(tmp_path)
        session = _session(session_id="session_042")
        plan = run(s, _manifest([session], [_photo()]), _photo_set([_enriched()]))
        assert plan.items[0].session_ref == "session_042"

    def test_no_photos_no_error(self, tmp_path):
        s = _settings(tmp_path)
        plan = run(s, _manifest([_session()], []), _photo_set([]))
        assert len(plan.items) == 1
        assert plan.items[0].photo_ids == []

    def test_empty_sessions_returns_empty_plan(self, tmp_path):
        s = _settings(tmp_path)
        plan = run(s, _manifest([], [_photo()]), _photo_set([_enriched()]))
        assert plan.items == []


# ---------------------------------------------------------------------------
# run() — multiple sessions with time windows
# ---------------------------------------------------------------------------

class TestRunMultipleSessions:
    def _make_two_session_setup(self, tmp_path):
        s = _settings(tmp_path)
        morning = _session("session_001", 1, "Morgen", time(9, 0), time(12, 0))
        afternoon = _session("session_002", 2, "Nachmittag", time(13, 0), time(16, 0))
        p_morning = _photo("photo_001", ts=datetime(2026, 2, 9, 10, 0, tzinfo=timezone.utc))
        p_afternoon = _photo("photo_002", "b.jpg", ts=datetime(2026, 2, 9, 14, 0, tzinfo=timezone.utc))
        e_morning = _enriched("photo_001")
        e_afternoon = _enriched("photo_002")
        return s, [morning, afternoon], [p_morning, p_afternoon], [e_morning, e_afternoon]

    def test_photos_assigned_to_correct_sessions(self, tmp_path):
        s, sessions, photos, enriched = self._make_two_session_setup(tmp_path)
        plan = run(s, _manifest(sessions, photos), _photo_set(enriched))
        morning_item = next(i for i in plan.items if i.session_ref == "session_001")
        afternoon_item = next(i for i in plan.items if i.session_ref == "session_002")
        assert "photo_001" in morning_item.photo_ids
        assert "photo_002" in afternoon_item.photo_ids

    def test_in_window_has_high_confidence(self, tmp_path):
        s, sessions, photos, enriched = self._make_two_session_setup(tmp_path)
        plan = run(s, _manifest(sessions, photos), _photo_set(enriched))
        for item in plan.items:
            assert item.temporal_confidence >= 0.6


# ---------------------------------------------------------------------------
# needs_review flag
# ---------------------------------------------------------------------------

class TestNeedsReview:
    def test_low_confidence_sets_needs_review(self, tmp_path):
        s = _settings(tmp_path)
        # Photo with no timestamp and no keyword overlap → low confidence
        session = _session(name="Xylophone Quarks Zebra", start=time(9, 0), end=time(10, 0))
        # Photo timestamp far outside the session window
        ts = datetime(2026, 2, 9, 22, 0, tzinfo=timezone.utc)
        photo = _photo(ts=ts)
        enriched = _enriched(keywords=["sport", "musik"])
        plan = run(s, _manifest([session], [photo]), _photo_set([enriched]))
        assert plan.items[0].needs_review is True

    def test_high_confidence_no_review(self, tmp_path):
        s = _settings(tmp_path)
        # Timestamp in window → temporal=1.0; keywords match session name → semantic > floor
        session = _session(name="Workshop Vernetzung", start=time(9, 0), end=time(10, 0))
        ts = datetime(2026, 2, 9, 9, 30, tzinfo=timezone.utc)
        photo = _photo(ts=ts)
        enriched = _enriched(keywords=["workshop", "vernetzung", "team"])
        plan = run(s, _manifest([session], [photo]), _photo_set([enriched]))
        assert plan.items[0].needs_review is False


# ---------------------------------------------------------------------------
# Artifact
# ---------------------------------------------------------------------------

class TestArtifact:
    def test_artifact_written(self, tmp_path):
        s = _settings(tmp_path)
        plan = run(s, _manifest([_session()], [_photo()]), _photo_set([_enriched()]))
        assert (s.cache_dir / "content_plan.json").exists()

    def test_artifact_roundtrips(self, tmp_path):
        s = _settings(tmp_path)
        plan = run(s, _manifest([_session()], [_photo()]), _photo_set([_enriched()]))
        loaded = ContentPlan.model_validate_json(
            (s.cache_dir / "content_plan.json").read_text()
        )
        assert loaded.items[0].session_ref == plan.items[0].session_ref
        assert loaded.items[0].photo_ids == plan.items[0].photo_ids
