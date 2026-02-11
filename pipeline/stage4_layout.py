"""Stage 4: Layout Planning — arrange photos and text into pages.

Consumes the ContentPlan and EnrichedPhotoSet to produce a PagePlan describing
every page in the final document: cover, optional section dividers, content
pages (1–2 photos each), and a closing page.

Layout decisions:
  - Landscape photos:  1 per page → "1-photo" / full-width
                       2 per page → "2-photo" / half-width each
  - Portrait photos:   1 per page → "1-photo" / portrait-pair (centered)
                       2 per page → "2-photo" / portrait-pair each
  - Mixed orientations: treated as landscape (half-width)

The heading of each ContentItem appears as a TextBlock on the first content
page of that item. Photo captions are taken from the enriched description.

Reads:  data/.cache/manifest.json        (ProjectManifest — for cover metadata)
        data/.cache/content_plan.json    (ContentPlan)
        data/.cache/enriched_photos.json  (EnrichedPhotoSet — for orientations)
Writes: data/.cache/page_plan.json       (PagePlan)
"""
import logging

from models.content_plan import ContentItem, ContentPlan
from models.enriched_photos import EnrichedPhoto, EnrichedPhotoSet
from models.manifest import ProjectManifest
from models.page_plan import Page, PagePlan, PhotoSlot, TextBlock
from settings import Settings

logger = logging.getLogger(__name__)


def run(
    settings: Settings,
    manifest: ProjectManifest,
    content_plan: ContentPlan,
    photo_set: EnrichedPhotoSet,
) -> PagePlan:
    """Build the PagePlan and write page_plan.json.

    Returns the completed PagePlan.
    """
    enriched_map = {e.photo_id: e for e in photo_set.enriched_photos}
    pages: list[Page] = []
    page_number = 1

    # --- Cover page ---
    cover = _make_cover(page_number, manifest)
    pages.append(cover)
    page_number += 1

    # --- Content pages (one block per ContentItem) ---
    for item in content_plan.items:
        if settings.section_dividers:
            pages.append(_make_section_divider(page_number, item))
            page_number += 1

        content_pages = _make_content_pages(
            start_page=page_number,
            item=item,
            enriched_map=enriched_map,
            max_per_page=settings.max_photos_per_page,
        )
        pages.extend(content_pages)
        page_number += len(content_pages)

    plan = PagePlan(pages=pages)

    artifact_path = settings.cache_dir / "page_plan.json"
    settings.cache_dir.mkdir(parents=True, exist_ok=True)
    artifact_path.write_text(plan.model_dump_json(indent=2), encoding="utf-8")

    logger.info("Stage 4 complete → %s", artifact_path)
    logger.info("  Total pages: %d", len(pages))
    _log_page_summary(pages)

    return plan


# ---------------------------------------------------------------------------
# Cover page
# ---------------------------------------------------------------------------

def _make_cover(page_number: int, manifest: ProjectManifest) -> Page:
    meta = manifest.meta
    blocks: list[TextBlock] = [
        TextBlock(content=meta.title, role="heading", style_ref="heading"),
    ]
    if meta.workshop_date:
        blocks.append(TextBlock(
            content=meta.workshop_date.strftime("%-d. %B %Y"),
            role="body",
            style_ref="body",
        ))
    if meta.location:
        blocks.append(TextBlock(content=meta.location, role="body", style_ref="body"))

    return Page(
        page_number=page_number,
        page_type="cover",
        layout_variant="text-only",
        text_blocks=blocks,
    )


# ---------------------------------------------------------------------------
# Section divider
# ---------------------------------------------------------------------------

def _make_section_divider(page_number: int, item: ContentItem) -> Page:
    return Page(
        page_number=page_number,
        page_type="section_divider",
        layout_variant="text-only",
        content_item_ref=item.id,
        text_blocks=[
            TextBlock(content=item.heading, role="heading", style_ref="heading"),
        ],
    )


# ---------------------------------------------------------------------------
# Content pages
# ---------------------------------------------------------------------------

def _make_content_pages(
    start_page: int,
    item: ContentItem,
    enriched_map: dict[str, EnrichedPhoto],
    max_per_page: int,
) -> list[Page]:
    """Distribute the item's photos across content pages.

    The item heading appears as a TextBlock on the first content page.
    Each page gets at most `max_per_page` photos.
    """
    pages: list[Page] = []
    photo_ids = item.photo_ids
    page_number = start_page

    if not photo_ids:
        # No photos — emit a text-only page with the heading
        pages.append(Page(
            page_number=page_number,
            page_type="content",
            layout_variant="text-only",
            content_item_ref=item.id,
            text_blocks=[
                TextBlock(content=item.heading, role="heading", style_ref="heading"),
            ],
        ))
        return pages

    # Batch photos into groups of max_per_page
    batches = [
        photo_ids[i:i + max_per_page]
        for i in range(0, len(photo_ids), max_per_page)
    ]

    for batch_index, batch in enumerate(batches):
        is_first = batch_index == 0
        text_blocks: list[TextBlock] = []
        if is_first:
            text_blocks.append(
                TextBlock(content=item.heading, role="heading", style_ref="heading")
            )

        slots = [_make_photo_slot(pid, len(batch), enriched_map) for pid in batch]
        variant = _pick_layout_variant(slots)

        pages.append(Page(
            page_number=page_number,
            page_type="content",
            layout_variant=variant,
            content_item_ref=item.id,
            photo_slots=slots,
            text_blocks=text_blocks,
        ))
        page_number += 1

    return pages


def _make_photo_slot(
    photo_id: str,
    batch_size: int,
    enriched_map: dict[str, EnrichedPhoto],
) -> PhotoSlot:
    enriched = enriched_map.get(photo_id)
    caption = enriched.description if enriched else ""
    orientation = _photo_orientation(photo_id, enriched_map)

    if batch_size == 1:
        display_size = "full-width" if orientation == "landscape" else "portrait-pair"
    else:
        # Two photos per page — portrait pair stays portrait-pair, rest half-width
        display_size = "portrait-pair" if orientation == "portrait" else "half-width"

    return PhotoSlot(photo_id=photo_id, caption=caption, display_size=display_size)


def _photo_orientation(photo_id: str, enriched_map: dict[str, EnrichedPhoto]) -> str:
    """Return 'portrait' or 'landscape'. Defaults to landscape if unknown."""
    # Orientation is not stored in EnrichedPhoto — we use the crop_box aspect ratio
    # as a proxy: if crop_box height > width → portrait, else landscape.
    enriched = enriched_map.get(photo_id)
    if enriched and enriched.crop_box:
        cb = enriched.crop_box
        if (cb.y_max - cb.y_min) > (cb.x_max - cb.x_min):
            return "portrait"
    return "landscape"


def _pick_layout_variant(slots: list[PhotoSlot]) -> str:
    if len(slots) == 1:
        return "1-photo"
    # Two photos — use photo-left / photo-right only for text-heavy layouts (future)
    return "2-photo"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _log_page_summary(pages: list[Page]) -> None:
    counts: dict[str, int] = {}
    for p in pages:
        counts[p.page_type] = counts.get(p.page_type, 0) + 1
    for page_type, count in sorted(counts.items()):
        logger.info("  %-18s %d", page_type, count)
