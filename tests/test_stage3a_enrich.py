"""Tests for Stage 3a AI Enrichment.

All OpenAI Vision API calls are mocked — no network access required.
"""
import json
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from PIL import Image

from models.enriched_photos import (
    CropBox,
    EnrichedPhoto,
    EnrichedPhotoSet,
    PhotoAnalysis,
)
from models.manifest import Photo, ProjectManifest, WorkshopMeta
from pipeline.stage3a_enrich import _crop_with_margin, _detect_mime, run
from settings import Settings

_NOW = datetime(2026, 2, 9, 12, 0, 0, tzinfo=timezone.utc)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_analysis(
    scene_type="result",
    description="Moderationskarten auf dem Boden.",
    ocr_text=None,
    topic_keywords=None,
    crop_box=None,
) -> PhotoAnalysis:
    return PhotoAnalysis(
        scene_type=scene_type,
        description=description,
        ocr_text=ocr_text,
        topic_keywords=topic_keywords or ["workshop"],
        crop_box=crop_box,
    )


def _make_flipchart_analysis() -> PhotoAnalysis:
    return _make_analysis(
        scene_type="flipchart",
        description="Ein Flipchart mit Stichpunkten.",
        ocr_text="Punkt 1\nPunkt 2",
        crop_box=CropBox(x_min=0.1, y_min=0.1, x_max=0.9, y_max=0.9),
    )


def _make_llm_response(analysis: PhotoAnalysis) -> MagicMock:
    mock_choice = MagicMock()
    mock_choice.message.parsed = analysis
    mock_response = MagicMock()
    mock_response.choices = [mock_choice]
    return mock_response


def _make_project(tmp_path, photos: list[tuple[str, tuple[int, int]]] = None) -> Settings:
    fotos = tmp_path / "fotos"
    fotos.mkdir()
    for name, size in (photos or []):
        Image.new("RGB", size).save(fotos / name)
    for d in ("agenda", "text", "template"):
        (tmp_path / d).mkdir()
    return Settings(openai_api_key="test", project_dir=tmp_path)


def _make_manifest(settings: Settings, photo_names: list[str] = None) -> ProjectManifest:
    photos = []
    for i, name in enumerate((photo_names or []), start=1):
        path = settings.fotos_dir / name
        img = Image.open(path)
        w, h = img.size
        photos.append(Photo(
            id=f"photo_{i:03d}",
            filename=name,
            path=Path(f"fotos/{name}"),
            width=w,
            height=h,
            orientation="landscape" if w > h else "portrait",
            timestamp_file=_NOW,
        ))
    return ProjectManifest(
        meta=WorkshopMeta(title="Test"),
        sessions=[],
        photos=photos,
        text_snippets=[],
    )


# ---------------------------------------------------------------------------
# _detect_mime
# ---------------------------------------------------------------------------

class TestDetectMime:
    def test_jpeg_magic(self):
        assert _detect_mime(b"\xff\xd8\xff" + b"\x00" * 10) == "image/jpeg"

    def test_png_magic(self):
        assert _detect_mime(b"\x89PNG\r\n\x1a\n" + b"\x00" * 10) == "image/png"

    def test_unknown_defaults_to_jpeg(self):
        assert _detect_mime(b"\x00\x01\x02\x03") == "image/jpeg"


# ---------------------------------------------------------------------------
# _crop_with_margin
# ---------------------------------------------------------------------------

class TestCropWithMargin:
    def test_crops_to_box(self):
        img = Image.new("RGB", (1000, 1000))
        crop_box = CropBox(x_min=0.2, y_min=0.2, x_max=0.8, y_max=0.8)
        result = _crop_with_margin(img, crop_box)
        # With 3% margin: 0.17–0.83 → 170–830 → 660x660
        assert result.size == (660, 660)

    def test_margin_does_not_exceed_bounds(self):
        img = Image.new("RGB", (1000, 500))
        # Tight crop right at the edge
        crop_box = CropBox(x_min=0.0, y_min=0.0, x_max=1.0, y_max=1.0)
        result = _crop_with_margin(img, crop_box)
        assert result.size == (1000, 500)  # clamped — same as original


# ---------------------------------------------------------------------------
# Cache behaviour
# ---------------------------------------------------------------------------

class TestCacheBehaviour:
    def test_cache_miss_calls_api(self, tmp_path):
        s = _make_project(tmp_path, [("img.jpg", (800, 600))])
        manifest = _make_manifest(s, ["img.jpg"])
        mock_client = MagicMock()
        mock_client.beta.chat.completions.parse.return_value = _make_llm_response(_make_analysis())

        with patch("pipeline.stage3a_enrich.OpenAI", return_value=mock_client):
            run(s, manifest)

        assert mock_client.beta.chat.completions.parse.call_count == 1

    def test_cache_hit_skips_api(self, tmp_path):
        s = _make_project(tmp_path, [("img.jpg", (800, 600))])
        manifest = _make_manifest(s, ["img.jpg"])
        mock_client = MagicMock()
        mock_client.beta.chat.completions.parse.return_value = _make_llm_response(_make_analysis())

        with patch("pipeline.stage3a_enrich.OpenAI", return_value=mock_client):
            run(s, manifest)
            run(s, manifest)

        assert mock_client.beta.chat.completions.parse.call_count == 1

    def test_cache_file_written_per_photo(self, tmp_path):
        s = _make_project(tmp_path, [("a.jpg", (800, 600)), ("b.jpg", (801, 600))])
        manifest = _make_manifest(s, ["a.jpg", "b.jpg"])
        mock_client = MagicMock()
        mock_client.beta.chat.completions.parse.return_value = _make_llm_response(_make_analysis())

        with patch("pipeline.stage3a_enrich.OpenAI", return_value=mock_client):
            run(s, manifest)

        assert len(list(s.analyses_dir.glob("*.json"))) == 2

    def test_cached_result_roundtrips(self, tmp_path):
        s = _make_project(tmp_path, [("img.jpg", (800, 600))])
        manifest = _make_manifest(s, ["img.jpg"])
        analysis = _make_analysis(ocr_text="Ergebnis A", topic_keywords=["vernetzung"])
        mock_client = MagicMock()
        mock_client.beta.chat.completions.parse.return_value = _make_llm_response(analysis)

        with patch("pipeline.stage3a_enrich.OpenAI", return_value=mock_client):
            first = run(s, manifest)
        second = run(s, manifest)  # no mock — must read from cache

        assert first.enriched_photos[0].ocr_text == second.enriched_photos[0].ocr_text
        assert first.enriched_photos[0].topic_keywords == second.enriched_photos[0].topic_keywords


# ---------------------------------------------------------------------------
# Cropping behaviour
# ---------------------------------------------------------------------------

class TestCropping:
    def test_flipchart_with_crop_box_saves_processed_file(self, tmp_path):
        s = _make_project(tmp_path, [("flip.jpg", (1000, 800))])
        manifest = _make_manifest(s, ["flip.jpg"])
        mock_client = MagicMock()
        mock_client.beta.chat.completions.parse.return_value = _make_llm_response(
            _make_flipchart_analysis()
        )

        with patch("pipeline.stage3a_enrich.OpenAI", return_value=mock_client):
            result = run(s, manifest)

        enriched = result.enriched_photos[0]
        assert enriched.processed_path is not None
        assert (s.project_dir / enriched.processed_path).exists()
        assert ".cache/processed" in str(enriched.processed_path)

    def test_flipchart_processed_image_is_smaller_than_original(self, tmp_path):
        s = _make_project(tmp_path, [("flip.jpg", (1000, 800))])
        manifest = _make_manifest(s, ["flip.jpg"])
        mock_client = MagicMock()
        mock_client.beta.chat.completions.parse.return_value = _make_llm_response(
            _make_flipchart_analysis()
        )

        with patch("pipeline.stage3a_enrich.OpenAI", return_value=mock_client):
            result = run(s, manifest)

        processed_path = s.project_dir / result.enriched_photos[0].processed_path
        with Image.open(processed_path) as img:
            w, h = img.size
        assert w < 1000 and h < 800  # crop_box=0.1–0.9 with 3% margin → ~84% of original

    def test_non_document_photo_saved_to_processed(self, tmp_path):
        s = _make_project(tmp_path, [("group.jpg", (800, 600))])
        manifest = _make_manifest(s, ["group.jpg"])
        mock_client = MagicMock()
        mock_client.beta.chat.completions.parse.return_value = _make_llm_response(
            _make_analysis(scene_type="group")
        )

        with patch("pipeline.stage3a_enrich.OpenAI", return_value=mock_client):
            result = run(s, manifest)

        enriched = result.enriched_photos[0]
        # Non-document photos are also saved to processed/ (orientation-corrected)
        assert enriched.processed_path is not None
        assert (s.project_dir / enriched.processed_path).exists()
        assert ".cache/processed" in str(enriched.processed_path)
        # No crop_box on non-document photos
        assert enriched.crop_box is None

    def test_crop_box_stored_in_enriched_photo(self, tmp_path):
        s = _make_project(tmp_path, [("flip.jpg", (1000, 800))])
        manifest = _make_manifest(s, ["flip.jpg"])
        mock_client = MagicMock()
        mock_client.beta.chat.completions.parse.return_value = _make_llm_response(
            _make_flipchart_analysis()
        )

        with patch("pipeline.stage3a_enrich.OpenAI", return_value=mock_client):
            result = run(s, manifest)

        cb = result.enriched_photos[0].crop_box
        assert cb is not None
        assert cb.x_min == 0.1
        assert cb.x_max == 0.9


# ---------------------------------------------------------------------------
# EnrichedPhotoSet content
# ---------------------------------------------------------------------------

class TestEnrichedPhotoSet:
    def test_photo_count_matches_manifest(self, tmp_path):
        s = _make_project(tmp_path, [("a.jpg", (800, 600)), ("b.jpg", (801, 600)), ("c.jpg", (802, 600))])
        manifest = _make_manifest(s, ["a.jpg", "b.jpg", "c.jpg"])
        mock_client = MagicMock()
        mock_client.beta.chat.completions.parse.return_value = _make_llm_response(_make_analysis())

        with patch("pipeline.stage3a_enrich.OpenAI", return_value=mock_client):
            result = run(s, manifest)

        assert len(result.enriched_photos) == 3

    def test_photo_ids_match_manifest(self, tmp_path):
        s = _make_project(tmp_path, [("a.jpg", (800, 600)), ("b.jpg", (801, 600))])
        manifest = _make_manifest(s, ["a.jpg", "b.jpg"])
        mock_client = MagicMock()
        mock_client.beta.chat.completions.parse.return_value = _make_llm_response(_make_analysis())

        with patch("pipeline.stage3a_enrich.OpenAI", return_value=mock_client):
            result = run(s, manifest)

        assert [e.photo_id for e in result.enriched_photos] == ["photo_001", "photo_002"]

    def test_analysis_model_recorded(self, tmp_path):
        s = _make_project(tmp_path, [("img.jpg", (800, 600))])
        manifest = _make_manifest(s, ["img.jpg"])
        mock_client = MagicMock()
        mock_client.beta.chat.completions.parse.return_value = _make_llm_response(_make_analysis())

        with patch("pipeline.stage3a_enrich.OpenAI", return_value=mock_client):
            result = run(s, manifest)

        assert result.enriched_photos[0].analysis_model == s.vision_model

    def test_empty_manifest_produces_empty_set(self, tmp_path):
        s = _make_project(tmp_path)
        manifest = _make_manifest(s, [])

        with patch("pipeline.stage3a_enrich.OpenAI"):
            result = run(s, manifest)

        assert result.enriched_photos == []

    def test_artifact_written_to_cache(self, tmp_path):
        s = _make_project(tmp_path, [("img.jpg", (800, 600))])
        manifest = _make_manifest(s, ["img.jpg"])
        mock_client = MagicMock()
        mock_client.beta.chat.completions.parse.return_value = _make_llm_response(_make_analysis())

        with patch("pipeline.stage3a_enrich.OpenAI", return_value=mock_client):
            run(s, manifest)

        artifact = s.cache_dir / "enriched_photos.json"
        assert artifact.exists()
        assert "enriched_photos" in json.loads(artifact.read_text())

    def test_artifact_roundtrips_from_json(self, tmp_path):
        s = _make_project(tmp_path, [("img.jpg", (800, 600))])
        manifest = _make_manifest(s, ["img.jpg"])
        mock_client = MagicMock()
        mock_client.beta.chat.completions.parse.return_value = _make_llm_response(_make_analysis())

        with patch("pipeline.stage3a_enrich.OpenAI", return_value=mock_client):
            result = run(s, manifest)

        loaded = EnrichedPhotoSet.model_validate_json(
            (s.cache_dir / "enriched_photos.json").read_text()
        )
        assert loaded == result


# ---------------------------------------------------------------------------
# Per-photo error handling
# ---------------------------------------------------------------------------

class TestErrorHandling:
    def test_missing_photo_file_is_skipped(self, tmp_path):
        s = _make_project(tmp_path, [("good.jpg", (800, 600))])
        manifest = _make_manifest(s, ["good.jpg"])
        # Add a phantom photo that doesn't exist on disk
        from models.manifest import Photo
        phantom = Photo(
            id="photo_002",
            filename="missing.jpg",
            path=Path("fotos/missing.jpg"),
            width=800,
            height=600,
            orientation="landscape",
            timestamp_file=_NOW,
        )
        manifest = manifest.model_copy(update={"photos": [phantom, manifest.photos[0]]})

        mock_client = MagicMock()
        mock_client.beta.chat.completions.parse.return_value = _make_llm_response(_make_analysis())

        with patch("pipeline.stage3a_enrich.OpenAI", return_value=mock_client):
            result = run(s, manifest)

        # Only the valid photo is in the result; missing one is skipped
        assert len(result.enriched_photos) == 1
        assert result.enriched_photos[0].photo_id == "photo_001"


# ---------------------------------------------------------------------------
# Rate limit retry
# ---------------------------------------------------------------------------

class TestRateLimitRetry:
    def test_retries_on_rate_limit(self, tmp_path):
        from openai import RateLimitError as _RateLimitError

        s = _make_project(tmp_path, [("img.jpg", (800, 600))])
        manifest = _make_manifest(s, ["img.jpg"])
        analysis = _make_analysis(scene_type="result")
        mock_client = MagicMock()
        mock_client.beta.chat.completions.parse.side_effect = [
            _RateLimitError("rate limit", response=MagicMock(status_code=429), body={}),
            _RateLimitError("rate limit", response=MagicMock(status_code=429), body={}),
            _make_llm_response(analysis),
        ]

        with patch("pipeline.stage3a_enrich.OpenAI", return_value=mock_client), \
             patch("pipeline.stage3a_enrich.time_module.sleep"):
            result = run(s, manifest)

        assert mock_client.beta.chat.completions.parse.call_count == 3
        assert result.enriched_photos[0].scene_type == "result"
