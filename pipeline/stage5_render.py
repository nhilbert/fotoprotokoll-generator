"""Stage 5: PDF Rendering — convert PagePlan to a PDF via WeasyPrint + Jinja2.

Reads:  data/.cache/page_plan.json         (PagePlan)
        data/.cache/enriched_photos.json    (EnrichedPhotoSet — for processed_path)
        data/template/design.yaml           (DesignSystem — for colours, fonts, layout)
Writes: data/output/<title>_<date>.pdf      (final PDF)

Photo images are embedded as ``file://`` URIs resolved from
``processed_path`` (relative to ``project_dir``).  When ``processed_path``
is absent the original photo in ``fotos/`` is used as a fallback.
"""
import logging
import re
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

try:
    import weasyprint as _weasyprint  # requires native GTK/Pango libs at runtime
except OSError:  # pragma: no cover — native libs absent in test env
    _weasyprint = None  # type: ignore[assignment]

from models.design import DesignSystem
from models.enriched_photos import EnrichedPhotoSet
from models.manifest import ProjectManifest
from models.page_plan import PagePlan
from settings import Settings

logger = logging.getLogger(__name__)

# Path (relative to the package root) where Jinja2 looks for templates
_TEMPLATE_DIR = Path(__file__).resolve().parent.parent / "templates"


def run(
    settings: Settings,
    page_plan: PagePlan,
    photo_set: EnrichedPhotoSet,
    manifest: ProjectManifest,
    design: DesignSystem | None = None,
) -> Path:
    """Render the PagePlan to a PDF and write it to the output directory.

    Returns the absolute path to the written PDF.
    """
    if design is None:
        design = DesignSystem.load_or_default(settings.design_yaml_path)

    photo_srcs = _build_photo_srcs(photo_set, settings)
    logo_src = _resolve_logo(design, settings)

    html = _render_html(page_plan, design, photo_srcs, logo_src)

    output_path = _output_path(settings, manifest)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    if _weasyprint is None:  # pragma: no cover
        raise RuntimeError(
            "WeasyPrint native libraries (GTK/Pango) are not available. "
            "Follow https://doc.courtbouillon.org/weasyprint/stable/first_steps.html"
        )
    _weasyprint.HTML(
        string=html,
        base_url=str(settings.project_dir.resolve()),
    ).write_pdf(str(output_path))

    logger.info("Stage 5 complete → %s", output_path)
    return output_path


# ---------------------------------------------------------------------------
# HTML rendering
# ---------------------------------------------------------------------------

def _render_html(
    page_plan: PagePlan,
    design: DesignSystem,
    photo_srcs: dict[str, str],
    logo_src: str | None,
) -> str:
    """Render the Jinja2 template to an HTML string."""
    env = Environment(
        loader=FileSystemLoader(str(_TEMPLATE_DIR)),
        autoescape=select_autoescape(["html"]),
    )
    template = env.get_template("report.html.j2")
    return template.render(
        pages=page_plan.pages,
        ds=design,
        photo_srcs=photo_srcs,
        logo_src=logo_src,
    )


# ---------------------------------------------------------------------------
# Photo URI resolution
# ---------------------------------------------------------------------------

def _build_photo_srcs(
    photo_set: EnrichedPhotoSet,
    settings: Settings,
) -> dict[str, str]:
    """Map photo_id → ``file://`` URI for the processed (or original) image."""
    result: dict[str, str] = {}
    for ep in photo_set.enriched_photos:
        path = _resolve_photo_path(ep.processed_path, ep.photo_id, settings)
        if path and path.exists():
            result[ep.photo_id] = path.resolve().as_uri()
    return result


def _resolve_photo_path(
    processed_path: Path | None,
    photo_id: str,
    settings: Settings,
) -> Path | None:
    """Return the absolute Path for a photo.

    Priority:
    1. ``processed_path`` (relative to project_dir) if it exists
    2. ``fotos/<photo_id>.jpg`` fallback
    """
    if processed_path:
        candidate = settings.project_dir / processed_path
        if candidate.exists():
            return candidate

    # Fallback: look for original in fotos/ (any common extension)
    for ext in (".jpg", ".jpeg", ".JPG", ".JPEG", ".png", ".PNG"):
        candidate = settings.fotos_dir / f"{photo_id}{ext}"
        if candidate.exists():
            return candidate

    return None


def _resolve_logo(design: DesignSystem, settings: Settings) -> str | None:
    """Return a ``file://`` URI for the logo, or None if absent."""
    if design.assets.logo is None:
        return None
    path = settings.project_dir / design.assets.logo
    if path.exists():
        return path.resolve().as_uri()
    logger.warning("Logo not found: %s", path)
    return None


# ---------------------------------------------------------------------------
# Output path
# ---------------------------------------------------------------------------

def _output_path(settings: Settings, manifest: ProjectManifest) -> Path:
    """Build the output PDF path from the manifest title and date."""
    title_slug = _slugify(manifest.meta.title)
    if manifest.meta.workshop_date:
        date_str = manifest.meta.workshop_date.strftime("%Y%m%d")
        filename = f"fotoprotokoll_{title_slug}_{date_str}.pdf"
    else:
        filename = f"fotoprotokoll_{title_slug}.pdf"
    return settings.output_dir / filename


def _slugify(text: str) -> str:
    """Convert a title to a safe ASCII filename slug."""
    # Replace German umlauts
    for umlaut, replacement in (("ä", "ae"), ("ö", "oe"), ("ü", "ue"),
                                 ("Ä", "Ae"), ("Ö", "Oe"), ("Ü", "Ue"),
                                 ("ß", "ss")):
        text = text.replace(umlaut, replacement)
    text = text.lower()
    text = re.sub(r"[^a-z0-9]+", "_", text)
    text = text.strip("_")
    return text[:50] or "protokoll"
