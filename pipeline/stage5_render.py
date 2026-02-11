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
import zlib
from pathlib import Path

import markdown as _markdown_lib
from jinja2 import Environment, FileSystemLoader, select_autoescape
from markupsafe import Markup

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

    photo_srcs = _build_photo_srcs(photo_set, manifest, settings)
    logo_src = _resolve_logo(design, settings)
    footer_logo_src = _find_assets_logo(settings) or logo_src

    html = _render_html(page_plan, design, photo_srcs, logo_src, footer_logo_src, manifest)

    output_path = _output_path(settings, manifest)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    if _weasyprint is None:  # pragma: no cover
        raise RuntimeError(
            "WeasyPrint native libraries (GTK/Pango) are not available. "
            "Follow https://doc.courtbouillon.org/weasyprint/stable/first_steps.html"
        )
    font_config = _weasyprint.text.fonts.FontConfiguration()
    font_css = _weasyprint.CSS(
        string=_build_font_face_css(design.typography.body.font),
        font_config=font_config,
    )
    _weasyprint.HTML(
        string=html,
        base_url=str(settings.project_dir.resolve()),
    ).write_pdf(str(output_path), stylesheets=[font_css], font_config=font_config)

    _validate_pdf_fonts(output_path, design.typography.body.font)
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
    footer_logo_src: str | None = None,
    manifest: ProjectManifest | None = None,
) -> str:
    """Render the Jinja2 template to an HTML string."""
    env = Environment(
        loader=FileSystemLoader(str(_TEMPLATE_DIR)),
        autoescape=select_autoescape(["html"]),
    )
    env.filters["markdown"] = lambda text: Markup(
        _markdown_lib.markdown(text, extensions=["extra"])
    )
    template = env.get_template("report.html.j2")
    return template.render(
        pages=page_plan.pages,
        ds=design,
        photo_srcs=photo_srcs,
        logo_src=logo_src,
        footer_logo_src=footer_logo_src,
        workshop_title=manifest.meta.title if manifest else "",
    )


# ---------------------------------------------------------------------------
# Font embedding
# ---------------------------------------------------------------------------

# Standard directories where TTF/OTF fonts live on Linux/macOS
_FONT_SEARCH_DIRS = [
    Path("/usr/share/fonts"),
    Path("/usr/local/share/fonts"),
    Path.home() / ".local/share/fonts",
    Path.home() / ".fonts",
]

# Filename fragment → (CSS font-weight, CSS font-style)
_FONT_STYLE_MAP = {
    "bolditalic": ("bold", "italic"),
    "bold":       ("bold", "normal"),
    "italic":     ("normal", "italic"),
    "oblique":    ("normal", "oblique"),
    "regular":    ("normal", "normal"),
    "book":       ("normal", "normal"),
}


def _find_font_files(font_name: str) -> list[tuple[Path, str, str]]:
    """Scan font directories for TTF/OTF files matching ``font_name``.

    Returns a list of (path, css_weight, css_style) tuples.
    """
    slug = font_name.replace(" ", "").lower()  # e.g. "DejaVuSans"
    results: list[tuple[Path, str, str]] = []
    for base in _FONT_SEARCH_DIRS:
        if not base.exists():
            continue
        for ext in ("*.ttf", "*.TTF", "*.otf", "*.OTF"):
            for path in base.rglob(ext):
                # Normalise stem for matching but keep original for suffix extraction
                stem_normalised = path.stem.lower()
                slug_hyphenated = font_name.lower().replace(" ", "-")  # "dejavu-sans"
                slug_nohyphen = font_name.lower().replace(" ", "")     # "dejavusans"
                # Accept "DejaVu-Sans-Bold" and "DejaVuSans-Bold" but not "DejaVuSansMono"
                if stem_normalised.startswith(slug_hyphenated + "-") or stem_normalised == slug_hyphenated:
                    suffix = stem_normalised[len(slug_hyphenated):].lstrip("-")
                elif stem_normalised.startswith(slug_nohyphen + "-") or stem_normalised == slug_nohyphen:
                    suffix = stem_normalised[len(slug_nohyphen):].lstrip("-")
                else:
                    continue
                weight, style = "normal", "normal"
                for fragment, (w, s) in _FONT_STYLE_MAP.items():
                    if fragment in suffix:
                        weight, style = w, s
                        break
                results.append((path, weight, style))
    return results


def _build_font_face_css(font_name: str) -> str:
    """Return ``@font-face`` CSS for all variants of ``font_name`` found on disk.

    Uses explicit ``file://`` URIs so WeasyPrint embeds the font directly
    rather than relying on Pango's font lookup (which may skip embedding).
    Falls back to an empty string if no font files are found.
    """
    font_files = _find_font_files(font_name)
    if not font_files:
        logger.warning("No font files found for '%s' — text may not embed correctly", font_name)
        return ""
    rules = []
    for path, weight, style in font_files:
        rules.append(
            f'@font-face {{\n'
            f'  font-family: "{font_name}";\n'
            f'  src: url({path.as_uri()}) format("truetype");\n'
            f'  font-weight: {weight};\n'
            f'  font-style: {style};\n'
            f'}}'
        )
    logger.debug("Font-face rules for '%s': %d variants", font_name, len(rules))
    return "\n".join(rules)


# ---------------------------------------------------------------------------
# PDF font validation
# ---------------------------------------------------------------------------

def _validate_pdf_fonts(pdf_path: Path, expected_font: str) -> None:
    """Check that fonts are embedded in the generated PDF and log the result.

    Parses the raw PDF bytes looking for:
    - ``/FontFile2`` (TrueType font programs) in object-stream dictionaries
    - Subset font names with the standard ``ABCDEF+FontName`` prefix

    A warning is logged if no embedded font programs are found so the caller
    can catch misconfiguration early without aborting the run.
    """
    data = pdf_path.read_bytes()

    # Decompress all FlateDecode streams so we can inspect object dictionaries
    # that WeasyPrint packs into cross-reference object streams (ObjStm).
    all_decoded: list[bytes] = [data]  # also search raw (uncompressed objects)
    for m in re.finditer(rb"stream[\r\n]+(.*?)[\r\n]+endstream", data, re.DOTALL):
        try:
            all_decoded.append(zlib.decompress(m.group(1)))
        except Exception:
            pass

    combined = b"\n".join(all_decoded)

    # Count /FontFile2 or /FontFile3 references (TrueType / OpenType programs)
    font_file_refs = len(re.findall(rb"/FontFile[23]?\b", combined))

    # Collect subset font names (standard PDF format: ABCDEF+FontName)
    subset_names = [
        n.decode("latin-1")
        for n in re.findall(rb"/FontName\s+/([A-Z]{6}\+[^\s/\]>]+)", combined)
    ]

    if font_file_refs == 0 and not subset_names:
        logger.warning(
            "PDF font validation FAILED for %s — "
            "no embedded font programs found. "
            "Text may be unreadable on systems without '%s' installed. "
            "Check that font files for '%s' are present in a standard font directory.",
            pdf_path.name,
            expected_font,
            expected_font,
        )
    else:
        logger.info(
            "PDF font validation OK for %s — "
            "%d font program(s) embedded, subsets: %s",
            pdf_path.name,
            font_file_refs,
            subset_names if subset_names else ["(none detected — may be in raw stream)"],
        )


# ---------------------------------------------------------------------------
# Photo URI resolution
# ---------------------------------------------------------------------------

def _build_photo_srcs(
    photo_set: EnrichedPhotoSet,
    manifest: ProjectManifest,
    settings: Settings,
) -> dict[str, str]:
    """Map photo_id → ``file://`` URI for the processed (or original) image."""
    # Build a fallback map: photo_id → original path from the manifest
    manifest_paths: dict[str, Path] = {
        p.id: settings.project_dir / p.path
        for p in manifest.photos
    }
    result: dict[str, str] = {}
    for ep in photo_set.enriched_photos:
        path = _resolve_photo_path(
            ep.processed_path, ep.photo_id, manifest_paths, settings
        )
        if path and path.exists():
            result[ep.photo_id] = path.resolve().as_uri()
    return result


def _resolve_photo_path(
    processed_path: Path | None,
    photo_id: str,
    manifest_paths: dict[str, Path],
    settings: Settings,
) -> Path | None:
    """Return the absolute Path for a photo.

    Priority:
    1. ``processed_path`` (relative to project_dir) — cropped/corrected image
    2. Manifest original path — exact filename from Stage 1 ingest
    """
    if processed_path:
        candidate = settings.project_dir / processed_path
        if candidate.exists():
            return candidate

    # Fallback: manifest original path (has the real filename)
    if photo_id in manifest_paths:
        candidate = manifest_paths[photo_id]
        if candidate.exists():
            return candidate

    return None


def _find_assets_logo(settings: Settings) -> str | None:
    """Return a ``file://`` URI for the first logo found in the assets directory.

    Prefers SVG over raster formats for crisp scaling.
    """
    for ext in ("*.svg", "*.SVG", "*.png", "*.PNG", "*.jpg", "*.JPG"):
        matches = [
            p for p in settings.assets_dir.glob(ext)
            if not p.name.endswith(":Zone.Identifier")
        ]
        if matches:
            return matches[0].resolve().as_uri()
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
    text = text.lower()
    for umlaut, replacement in (("ä", "ae"), ("ö", "oe"), ("ü", "ue"), ("ß", "ss")):
        text = text.replace(umlaut, replacement)
    text = re.sub(r"[^a-z0-9]+", "_", text)
    text = text.strip("_")
    return text[:50] or "protokoll"
