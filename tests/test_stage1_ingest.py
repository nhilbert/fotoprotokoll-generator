"""Tests for Stage 1 Ingest and agenda_parser utilities.

All OpenAI API calls are mocked — no network access required.
"""
import json
from datetime import date, datetime, time, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from PIL import Image

from models.manifest import ProjectManifest
from pipeline.stage1_ingest import run
from settings import Settings
from utils.agenda_parser import (
    _AgendaSchema,
    _SessionSchema,
    _clean_filename,
    _parse_date_string,
    _regex_title,
    _regex_date,
    parse_agenda,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _make_llm_response(schema: _AgendaSchema) -> MagicMock:
    """Build a mock openai response that returns the given _AgendaSchema."""
    mock_choice = MagicMock()
    mock_choice.message.parsed = schema
    mock_response = MagicMock()
    mock_response.choices = [mock_choice]
    return mock_response


def _mock_llm(schema: _AgendaSchema):
    """Context manager: patch the OpenAI client to return the given schema."""
    mock_client = MagicMock()
    mock_client.beta.chat.completions.parse.return_value = _make_llm_response(schema)
    return patch("utils.agenda_parser.OpenAI", return_value=mock_client)


# ---------------------------------------------------------------------------
# agenda_parser — LLM path
# ---------------------------------------------------------------------------

class TestAgendaParserLLM:
    def test_extracts_full_metadata(self, sample_project_dir, settings):
        agenda_path = sample_project_dir / "agenda" / "agenda.txt"
        expected = _AgendaSchema(
            title="Test-Workshop Gelingensfaktoren",
            workshop_date="2026-02-09",
            location="Berlin",
            participants=12,
            sessions=[
                _SessionSchema(name="Begrüßung und Einstieg", start_time="09:00", end_time="10:00"),
                _SessionSchema(name="Gruppenarbeit: Problemanalyse", start_time="10:00", end_time="11:00"),
                _SessionSchema(name="Präsentation der Ergebnisse", start_time="11:00", end_time="12:00"),
                _SessionSchema(name="Abschluss", start_time="12:00", end_time=None),
            ],
        )
        with _mock_llm(expected):
            meta, sessions = parse_agenda(agenda_path, settings)

        assert meta.title == "Test-Workshop Gelingensfaktoren"
        assert meta.workshop_date == date(2026, 2, 9)
        assert meta.location == "Berlin"
        assert meta.participants == 12
        assert len(sessions) == 4
        assert sessions[0].start_time == time(9, 0)
        assert sessions[0].end_time == time(10, 0)
        assert sessions[-1].end_time is None

    def test_session_ids_and_order(self, sample_project_dir, settings):
        agenda_path = sample_project_dir / "agenda" / "agenda.txt"
        schema = _AgendaSchema(
            title="Workshop",
            workshop_date=None,
            location=None,
            participants=None,
            sessions=[
                _SessionSchema(name="Block A", start_time="09:00"),
                _SessionSchema(name="Block B", start_time="10:30"),
            ],
        )
        with _mock_llm(schema):
            _, sessions = parse_agenda(agenda_path, settings)

        assert sessions[0].id == "session_001"
        assert sessions[1].id == "session_002"
        assert sessions[0].order == 1
        assert sessions[1].order == 2

    def test_null_date_returns_none(self, sample_project_dir, settings):
        agenda_path = sample_project_dir / "agenda" / "agenda.txt"
        schema = _AgendaSchema(
            title="Workshop", workshop_date=None,
            location=None, participants=None,
            sessions=[_SessionSchema(name="Session")],
        )
        with _mock_llm(schema):
            meta, _ = parse_agenda(agenda_path, settings)
        assert meta.workshop_date is None

    def test_falls_back_to_regex_on_llm_failure(self, sample_project_dir, settings):
        agenda_path = sample_project_dir / "agenda" / "agenda.txt"
        with patch("utils.agenda_parser.OpenAI", side_effect=Exception("API down")):
            meta, sessions = parse_agenda(agenda_path, settings)

        # Regex fallback should extract something sensible from the fixture txt
        assert meta.title  # not empty
        assert len(sessions) >= 1

    def test_rate_limit_retries(self, sample_project_dir, settings):
        from openai import RateLimitError as _RateLimitError

        agenda_path = sample_project_dir / "agenda" / "agenda.txt"
        expected = _AgendaSchema(
            title="Workshop", workshop_date=None, location=None,
            participants=None, sessions=[_SessionSchema(name="Session")],
        )
        mock_client = MagicMock()
        # Fail twice, succeed on third attempt
        mock_client.beta.chat.completions.parse.side_effect = [
            _RateLimitError("rate limit", response=MagicMock(status_code=429), body={}),
            _RateLimitError("rate limit", response=MagicMock(status_code=429), body={}),
            _make_llm_response(expected),
        ]
        with patch("utils.agenda_parser.OpenAI", return_value=mock_client), \
             patch("utils.agenda_parser.time_module.sleep"):  # don't actually sleep
            meta, _ = parse_agenda(agenda_path, settings)

        assert meta.title == "Workshop"
        assert mock_client.beta.chat.completions.parse.call_count == 3


# ---------------------------------------------------------------------------
# agenda_parser — regex fallback internals
# ---------------------------------------------------------------------------

class TestAgendaParserRegex:
    def test_parse_date_ddmmyyyy(self):
        assert _parse_date_string("Datum: 09.02.2026") == date(2026, 2, 9)

    def test_parse_date_ddmmyy(self):
        assert _parse_date_string("09.02.26") == date(2026, 2, 9)

    def test_parse_date_iso(self):
        assert _parse_date_string("2026-02-09") == date(2026, 2, 9)

    def test_parse_date_invalid_returns_none(self):
        assert _parse_date_string("no date here") is None

    def test_clean_filename_removes_date_and_suffixes(self):
        assert _clean_filename("Ablaufidee Workshop 09.02.26_final") == "Ablaufidee Workshop"

    def test_clean_filename_handles_underscores(self):
        assert _clean_filename("My_Workshop_v2") == "My Workshop"

    def test_regex_title_uses_label(self, sample_project_dir):
        path = sample_project_dir / "agenda" / "agenda.txt"
        text = path.read_text()
        assert "Gelingensfaktoren" in _regex_title(text, path)

    def test_regex_date_finds_labelled_date(self, sample_project_dir):
        path = sample_project_dir / "agenda" / "agenda.txt"
        text = path.read_text()
        assert _regex_date(text, path) == date(2026, 2, 9)


# ---------------------------------------------------------------------------
# Stage 1 run() — photo inventory
# ---------------------------------------------------------------------------

class TestStage1Photos:
    def _make_project(self, tmp_path, photos: list[tuple[str, tuple[int, int]]] = None):
        """Helper: create a minimal project directory structure."""
        fotos = tmp_path / "fotos"
        fotos.mkdir()
        for name, size in (photos or []):
            Image.new("RGB", size).save(fotos / name)
        for d in ("agenda", "text", "template"):
            (tmp_path / d).mkdir()
        return Settings(openai_api_key="test", project_dir=tmp_path)

    def test_inventories_all_photos(self, tmp_path):
        s = self._make_project(tmp_path, [("a.jpg", (800, 600)), ("b.jpg", (800, 600))])
        manifest = run(s)
        assert len(manifest.photos) == 2

    def test_photos_have_relative_paths(self, tmp_path):
        s = self._make_project(tmp_path, [("img.jpg", (800, 600))])
        manifest = run(s)
        assert not manifest.photos[0].path.is_absolute()
        assert manifest.photos[0].path == Path("fotos/img.jpg")

    def test_landscape_orientation(self, tmp_path):
        s = self._make_project(tmp_path, [("img.jpg", (800, 600))])
        manifest = run(s)
        assert manifest.photos[0].orientation == "landscape"

    def test_portrait_orientation(self, tmp_path):
        s = self._make_project(tmp_path, [("img.jpg", (600, 800))])
        manifest = run(s)
        assert manifest.photos[0].orientation == "portrait"

    def test_file_mtime_used_as_timestamp_fallback(self, tmp_path):
        s = self._make_project(tmp_path, [("img.jpg", (800, 600))])
        manifest = run(s)
        photo = manifest.photos[0]
        assert photo.timestamp_exif is None
        assert photo.timestamp_file is not None
        assert photo.timestamp_file.tzinfo == timezone.utc
        assert photo.best_timestamp == photo.timestamp_file

    def test_photos_sorted_by_filename(self, tmp_path):
        s = self._make_project(tmp_path, [("c.jpg", (800, 600)), ("a.jpg", (800, 600)), ("b.jpg", (800, 600))])
        manifest = run(s)
        assert [p.filename for p in manifest.photos] == ["a.jpg", "b.jpg", "c.jpg"]

    def test_missing_photos_dir_returns_empty(self, tmp_path):
        for d in ("agenda", "text", "template"):
            (tmp_path / d).mkdir()
        s = Settings(openai_api_key="test", project_dir=tmp_path)
        manifest = run(s)
        assert manifest.photos == []


# ---------------------------------------------------------------------------
# Stage 1 run() — text snippets
# ---------------------------------------------------------------------------

class TestStage1TextSnippets:
    def test_reads_text_snippets(self, settings, tmp_path):
        text_dir = tmp_path / "text"
        text_dir.mkdir()
        (text_dir / "notes.md").write_text("Ergebnis Eins Zwei Drei", encoding="utf-8")
        for d in ("agenda", "fotos", "template"):
            (tmp_path / d).mkdir()

        s = Settings(openai_api_key="test", project_dir=tmp_path)
        with patch("utils.agenda_parser.OpenAI", side_effect=Exception("no api")):
            manifest = run(s)

        assert len(manifest.text_snippets) == 1
        assert manifest.text_snippets[0].word_count == 4
        assert manifest.text_snippets[0].filename == "notes.md"

    def test_missing_text_dir_returns_empty(self, tmp_path):
        for d in ("agenda", "fotos", "template"):
            (tmp_path / d).mkdir()

        s = Settings(openai_api_key="test", project_dir=tmp_path)
        with patch("utils.agenda_parser.OpenAI", side_effect=Exception("no api")):
            manifest = run(s)

        assert manifest.text_snippets == []


# ---------------------------------------------------------------------------
# Stage 1 run() — missing agenda fallback
# ---------------------------------------------------------------------------

class TestStage1AgendaFallback:
    def test_missing_agenda_produces_default_session(self, tmp_path):
        for d in ("fotos", "text", "template"):
            (tmp_path / d).mkdir()
        # no agenda/ dir

        s = Settings(openai_api_key="test", project_dir=tmp_path)
        manifest = run(s)

        assert len(manifest.sessions) == 1
        assert manifest.sessions[0].name == "Workshop"
        assert manifest.meta.title == "Workshop"

    def test_empty_agenda_dir_produces_default_session(self, tmp_path):
        for d in ("agenda", "fotos", "text", "template"):
            (tmp_path / d).mkdir()

        s = Settings(openai_api_key="test", project_dir=tmp_path)
        manifest = run(s)

        assert manifest.sessions[0].name == "Workshop"


# ---------------------------------------------------------------------------
# Stage 1 run() — artifact written to cache
# ---------------------------------------------------------------------------

class TestStage1Artifact:
    def test_manifest_written_to_cache(self, tmp_path):
        for d in ("agenda", "fotos", "text", "template"):
            (tmp_path / d).mkdir()

        s = Settings(openai_api_key="test", project_dir=tmp_path)
        run(s)

        artifact = tmp_path / ".cache" / "manifest.json"
        assert artifact.exists()
        data = json.loads(artifact.read_text())
        assert "meta" in data
        assert "photos" in data

    def test_manifest_roundtrips_from_json(self, tmp_path):
        for d in ("agenda", "fotos", "text", "template"):
            (tmp_path / d).mkdir()

        s = Settings(openai_api_key="test", project_dir=tmp_path)
        manifest = run(s)

        artifact = tmp_path / ".cache" / "manifest.json"
        loaded = ProjectManifest.model_validate_json(artifact.read_text())
        assert loaded == manifest
