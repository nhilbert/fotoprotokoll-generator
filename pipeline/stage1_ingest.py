"""Stage 1: Ingest — parse project directory and produce manifest.json.

Reads:  data/agenda/, data/fotos/, data/text/, data/template/design.yaml
Writes: data/.cache/manifest.json
"""
import logging
from datetime import datetime, timezone
from pathlib import Path

from PIL import Image

from models.manifest import Photo, ProjectManifest, TextSnippet, WorkshopMeta, AgendaSession
from settings import Settings
from utils.agenda_parser import parse_agenda

logger = logging.getLogger(__name__)

_PHOTO_EXTENSIONS = frozenset({".jpg", ".jpeg", ".png", ".webp", ".gif", ".heic"})
_TEXT_EXTENSIONS = frozenset({".md", ".txt"})

# EXIF tag IDs for timestamps (checked in priority order)
_EXIF_DATETIME_TAGS = (36867, 36868, 306)  # DateTimeOriginal, DateTimeDigitized, DateTime
_EXIF_DATETIME_FORMAT = "%Y:%m:%d %H:%M:%S"

# EXIF orientation values that swap width/height for display
_TRANSPOSING_ORIENTATIONS = frozenset({6, 8})


def run(settings: Settings) -> ProjectManifest:
    """Parse the project directory and write manifest.json to the cache.

    Returns the completed ProjectManifest.
    """
    _ensure_dirs(settings)

    meta, sessions = _load_agenda(settings)
    photos = _inventory_photos(settings)
    text_snippets = _read_text_snippets(settings)

    manifest = ProjectManifest(
        meta=meta,
        sessions=sessions,
        photos=photos,
        text_snippets=text_snippets,
    )

    artifact_path = settings.cache_dir / "manifest.json"
    artifact_path.write_text(manifest.model_dump_json(indent=2), encoding="utf-8")

    logger.info("Stage 1 complete → %s", artifact_path)
    logger.info("  Title:         %s", meta.title)
    logger.info("  Date:          %s", meta.workshop_date)
    logger.info("  Sessions:      %d", len(sessions))
    logger.info("  Photos:        %d", len(photos))
    logger.info("  Text snippets: %d", len(text_snippets))

    return manifest


# ---------------------------------------------------------------------------
# Agenda
# ---------------------------------------------------------------------------

def _load_agenda(settings: Settings) -> tuple[WorkshopMeta, list[AgendaSession]]:
    _AGENDA_EXTENSIONS = frozenset({".docx", ".pdf", ".txt", ".md"})

    if not settings.agenda_dir.exists():
        logger.warning("Agenda directory not found: %s. Using defaults.", settings.agenda_dir)
        return WorkshopMeta(title="Workshop"), _default_sessions()

    agenda_files = [
        f for f in sorted(settings.agenda_dir.iterdir())
        if f.is_file() and f.suffix.lower() in _AGENDA_EXTENSIONS
    ]

    if not agenda_files:
        logger.warning("No agenda file found in %s. Using defaults.", settings.agenda_dir)
        return WorkshopMeta(title="Workshop"), _default_sessions()

    if len(agenda_files) > 1:
        logger.warning(
            "Multiple agenda files found; using: %s", agenda_files[0].name
        )

    return parse_agenda(agenda_files[0], settings)


def _default_sessions() -> list[AgendaSession]:
    return [AgendaSession(id="session_001", order=1, name="Workshop")]


# ---------------------------------------------------------------------------
# Photos
# ---------------------------------------------------------------------------

def _inventory_photos(settings: Settings) -> list[Photo]:
    if not settings.fotos_dir.exists():
        logger.warning("Photos directory not found: %s", settings.fotos_dir)
        return []

    photo_files = sorted(
        f for f in settings.fotos_dir.iterdir()
        if f.is_file() and f.suffix.lower() in _PHOTO_EXTENSIONS
    )

    photos: list[Photo] = []
    for index, path in enumerate(photo_files, start=1):
        photo = _read_photo_metadata(path, index, settings.project_dir)
        if photo is not None:
            photos.append(photo)

    return photos


def _read_photo_metadata(path: Path, index: int, project_dir: Path) -> Photo | None:
    try:
        with Image.open(path) as img:
            width, height = img.size
            exif = img.getexif()
            orientation_tag = exif.get(274, 1)  # 274 = Orientation
            timestamp_exif = _read_exif_timestamp(exif)

        # Swap dimensions for rotationally transposed images so
        # orientation reflects how the image is actually displayed.
        if orientation_tag in _TRANSPOSING_ORIENTATIONS:
            width, height = height, width

        relative_path = path.relative_to(project_dir)
        timestamp_file = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)

        return Photo(
            id=f"photo_{index:03d}",
            filename=path.name,
            path=relative_path,
            timestamp_exif=timestamp_exif,
            timestamp_file=timestamp_file,
            width=width,
            height=height,
            orientation=_detect_orientation(width, height),
        )
    except Exception as exc:
        logger.warning("Could not read photo metadata for %s: %s", path.name, exc)
        return None


def _read_exif_timestamp(exif) -> datetime | None:
    for tag_id in _EXIF_DATETIME_TAGS:
        value = exif.get(tag_id)
        if value:
            try:
                return datetime.strptime(value, _EXIF_DATETIME_FORMAT).replace(
                    tzinfo=timezone.utc
                )
            except ValueError:
                continue
    return None


def _detect_orientation(width: int, height: int) -> str:
    if width > height:
        return "landscape"
    if height > width:
        return "portrait"
    return "square"


# ---------------------------------------------------------------------------
# Text snippets
# ---------------------------------------------------------------------------

def _read_text_snippets(settings: Settings) -> list[TextSnippet]:
    if not settings.text_dir.exists():
        logger.warning("Text directory not found: %s", settings.text_dir)
        return []

    text_files = sorted(
        f for f in settings.text_dir.iterdir()
        if f.is_file() and f.suffix.lower() in _TEXT_EXTENSIONS
    )

    snippets: list[TextSnippet] = []
    for index, path in enumerate(text_files, start=1):
        try:
            content = path.read_text(encoding="utf-8", errors="replace")
            snippets.append(TextSnippet(
                id=f"text_{index:03d}",
                filename=path.name,
                content=content,
                word_count=len(content.split()),
            ))
        except Exception as exc:
            logger.warning("Could not read text file %s: %s", path.name, exc)

    return snippets


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _ensure_dirs(settings: Settings) -> None:
    settings.cache_dir.mkdir(parents=True, exist_ok=True)
    settings.output_dir.mkdir(parents=True, exist_ok=True)
