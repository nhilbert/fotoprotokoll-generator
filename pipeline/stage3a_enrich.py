"""Stage 3a: AI Enrichment — GPT Vision analysis and document cropping.

For each photo the Vision API returns:
  - scene classification (flipchart / group / activity / result / unknown)
  - description and OCR text
  - crop_box (only for rectangular document photos)

Document photos (flipchart) are cropped and saved to .cache/processed/.
All other photos reference the original file via processed_path.

Reads:  data/.cache/manifest.json  (ProjectManifest)
Writes: data/.cache/enriched_photos.json  (EnrichedPhotoSet)
        data/.cache/analyses/<sha256>.json  (per-photo cache, never re-computed)
        data/.cache/processed/<sha256>.jpg  (cropped document photos)
"""
import base64
import hashlib
import logging
import random
import time as time_module
from pathlib import Path

from openai import OpenAI, RateLimitError
from PIL import Image, ImageOps

from models.enriched_photos import CropBox, EnrichedPhoto, EnrichedPhotoSet, PhotoAnalysis
from models.manifest import Photo, ProjectManifest
from settings import Settings

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT = """\
Du bist ein Assistent, der Fotos von Workshops analysiert.
Analysiere das Foto und extrahiere:
- scene_type: Art der Szene:
    "flipchart"  = ein rechteckiges Dokument füllt den Bildinhalt (Flipchart, Whiteboard,
                   Plakat, Poster, Pinnwand — klar abgegrenztes rechteckiges Objekt)
    "group"      = Menschen bei Gruppenarbeit oder Diskussion
    "activity"   = Aktivität oder Übung (z.B. Moderationskarten auf dem Boden)
    "result"     = Ergebnis oder Produkt (z.B. Karten, Zettel, Poster als Gesamtbild)
    "unknown"    = Sonstiges
- description: Kurze prägnante Beschreibung des Bildinhalts auf Deutsch (1-2 Sätze)
- ocr_text: Lesbarer Text auf dem Bild — vollständig extrahieren, falls vorhanden; sonst null
- topic_keywords: 2-5 thematische Schlagwörter aus dem Bildinhalt
- crop_box: Nur wenn scene_type="flipchart" — enge Crop-Koordinaten (normalisiert 0.0–1.0)
    die das rechteckige Dokument im Bild umschließen. Bei allen anderen scene_types: null.

Antworte ausschließlich im vorgegebenen JSON-Schema.
"""

# Padding added around the model's tight crop box (in normalized units)
# "better take more than less" — keeps a comfortable border around the document
_CROP_MARGIN = 0.03

_MAX_RETRIES = 6

# Document scene types that trigger cropping
_DOCUMENT_SCENE_TYPES = frozenset({"flipchart"})


def run(settings: Settings, manifest: ProjectManifest) -> EnrichedPhotoSet:
    """Analyse all photos via GPT Vision. Results cached per content-hash.

    Returns the completed EnrichedPhotoSet and writes two artifacts:
    - .cache/analyses/<sha256>.json  for each photo (persistent cache)
    - .cache/enriched_photos.json    combined set for downstream stages
    """
    settings.analyses_dir.mkdir(parents=True, exist_ok=True)
    settings.processed_dir.mkdir(parents=True, exist_ok=True)

    client = OpenAI(api_key=settings.openai_api_key)
    enriched: list[EnrichedPhoto] = []

    for photo in manifest.photos:
        photo_path = settings.project_dir / photo.path
        try:
            result = _analyse_photo(photo, photo_path, client, settings)
        except Exception as exc:
            logger.warning("  [%s] %s — SKIPPED: %s", photo.id, photo.filename, exc)
            continue
        enriched.append(result)
        crop_note = " [cropped]" if result.crop_box else ""
        logger.info(
            "  [%s] %s — %s%s",
            photo.id,
            photo.filename,
            result.scene_type,
            crop_note,
        )

    photo_set = EnrichedPhotoSet(enriched_photos=enriched)

    artifact_path = settings.cache_dir / "enriched_photos.json"
    artifact_path.write_text(photo_set.model_dump_json(indent=2), encoding="utf-8")

    logger.info("Stage 3a complete → %s", artifact_path)
    logger.info("  Photos analysed: %d", len(enriched))
    _log_scene_summary(enriched)

    return photo_set


# ---------------------------------------------------------------------------
# Per-photo analysis
# ---------------------------------------------------------------------------

def _analyse_photo(
    photo: Photo,
    photo_path: Path,
    client: OpenAI,
    settings: Settings,
) -> EnrichedPhoto:
    """Return cached analysis if available, otherwise call the Vision API."""
    # Hash original bytes for a stable cache key independent of orientation correction
    original_bytes = photo_path.read_bytes()
    content_hash = hashlib.sha256(original_bytes).hexdigest()

    # Normalised image (EXIF rotation applied) used for API and cropping
    corrected_img, corrected_bytes = _load_corrected(photo_path)

    cache_file = settings.analyses_dir / f"{content_hash}.json"
    if cache_file.exists():
        logger.debug("Cache hit for %s (%s)", photo.filename, content_hash[:12])
        cached = EnrichedPhoto.model_validate_json(cache_file.read_text(encoding="utf-8"))
        # Ensure processed file still exists (may have been deleted)
        if cached.processed_path and not (settings.project_dir / cached.processed_path).exists():
            cached = _apply_crop_to_photo(cached, corrected_img, content_hash, settings)
            cache_file.write_text(cached.model_dump_json(indent=2), encoding="utf-8")
        return cached

    logger.debug("Cache miss for %s — calling Vision API", photo.filename)
    analysis = _call_vision_api(corrected_bytes, client, settings)

    processed_path = _save_processed(
        photo, corrected_img, analysis.crop_box, content_hash, settings
    )
    result = EnrichedPhoto.from_analysis(
        photo.id, analysis, settings.vision_model, processed_path=processed_path
    )

    cache_file.write_text(result.model_dump_json(indent=2), encoding="utf-8")
    return result


def _save_processed(
    photo: Photo,
    corrected_img: Image.Image,
    crop_box: CropBox | None,
    content_hash: str,
    settings: Settings,
) -> Path:
    """Crop and save processed image; return path relative to project_dir.

    `corrected_img` has already had EXIF orientation applied.
    For document photos with a crop_box: crop + save to .cache/processed/.
    For all other photos: processed_path points to the original fotos/ file.
    """
    if crop_box is None:
        # No cropping — save orientation-corrected version so it's always right-side-up
        out_path = settings.processed_dir / f"{content_hash}.jpg"
        corrected_img.save(out_path, format="JPEG", quality=92)
        return out_path.relative_to(settings.project_dir)

    cropped = _crop_with_margin(corrected_img, crop_box)
    out_path = settings.processed_dir / f"{content_hash}.jpg"
    cropped.save(out_path, format="JPEG", quality=92)
    return out_path.relative_to(settings.project_dir)


def _apply_crop_to_photo(
    cached: EnrichedPhoto,
    corrected_img: Image.Image,
    content_hash: str,
    settings: Settings,
) -> EnrichedPhoto:
    """Re-apply processing when the processed file is missing (cache rebuild)."""
    img = _crop_with_margin(corrected_img, cached.crop_box) if cached.crop_box else corrected_img
    out_path = settings.processed_dir / f"{content_hash}.jpg"
    img.save(out_path, format="JPEG", quality=92)
    return cached.model_copy(
        update={"processed_path": out_path.relative_to(settings.project_dir)}
    )


def _crop_with_margin(img: Image.Image, crop_box: CropBox) -> Image.Image:
    """Apply crop box with margin padding. Never exceeds image bounds."""
    w, h = img.size
    x_min = max(0.0, crop_box.x_min - _CROP_MARGIN)
    y_min = max(0.0, crop_box.y_min - _CROP_MARGIN)
    x_max = min(1.0, crop_box.x_max + _CROP_MARGIN)
    y_max = min(1.0, crop_box.y_max + _CROP_MARGIN)
    return img.crop((int(x_min * w), int(y_min * h), int(x_max * w), int(y_max * h)))


# ---------------------------------------------------------------------------
# Vision API call
# ---------------------------------------------------------------------------

def _call_vision_api(
    image_bytes: bytes,
    client: OpenAI,
    settings: Settings,
) -> PhotoAnalysis:
    """Call GPT Vision with exponential-backoff retry on rate limits."""
    b64 = base64.standard_b64encode(image_bytes).decode()
    mime = _detect_mime(image_bytes)

    for attempt in range(_MAX_RETRIES):
        try:
            response = client.beta.chat.completions.parse(
                model=settings.vision_model,
                messages=[
                    {"role": "system", "content": _SYSTEM_PROMPT},
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "image_url",
                                "image_url": {
                                    "url": f"data:{mime};base64,{b64}",
                                    "detail": "high",
                                },
                            }
                        ],
                    },
                ],
                response_format=PhotoAnalysis,
            )
            return response.choices[0].message.parsed
        except RateLimitError:
            if attempt == _MAX_RETRIES - 1:
                raise
            delay = 2 ** attempt + random.uniform(0, 1)
            logger.debug(
                "Rate limited; retrying in %.1fs (attempt %d/%d).",
                delay, attempt + 1, _MAX_RETRIES,
            )
            time_module.sleep(delay)

    raise RuntimeError("Unreachable")  # pragma: no cover


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _load_corrected(path: Path) -> tuple[Image.Image, bytes]:
    """Open image, apply EXIF orientation, return (PIL Image, JPEG bytes).

    ImageOps.exif_transpose() ensures the image is right-side-up before it
    is sent to the Vision API or used for cropping. The returned Image is a
    fully-loaded copy (not backed by a file handle) so the caller need not
    manage a context manager.
    """
    import io
    with Image.open(path) as img:
        corrected = ImageOps.exif_transpose(img)
        corrected = corrected.copy()  # detach from file handle before closing
    buf = io.BytesIO()
    corrected.save(buf, format="JPEG", quality=92)
    return corrected, buf.getvalue()


def _detect_mime(image_bytes: bytes) -> str:
    """Detect MIME type from magic bytes."""
    if image_bytes[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if image_bytes[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if image_bytes[:4] in (b"RIFF", b"WEBP"):
        return "image/webp"
    return "image/jpeg"  # safe default for camera photos


def _log_scene_summary(enriched: list[EnrichedPhoto]) -> None:
    counts: dict[str, int] = {}
    for e in enriched:
        counts[e.scene_type] = counts.get(e.scene_type, 0) + 1
    for scene_type, count in sorted(counts.items()):
        logger.info("  %-12s %d", scene_type, count)
