"""Stage 3b: Matching — assign photos to agenda sessions.

Each photo is scored against every session on two dimensions:

  Temporal score  — how well the photo timestamp aligns with the session time window.
  Semantic score  — keyword overlap between photo content and session name/text snippets.

Each photo is then assigned to the highest-scoring session. The resulting
ContentPlan carries one ContentItem per session listing the assigned photo IDs
and aggregate confidence scores.

Reads:  data/.cache/manifest.json        (ProjectManifest)
        data/.cache/enriched_photos.json  (EnrichedPhotoSet)
Writes: data/.cache/content_plan.json    (ContentPlan)
"""
import logging
import re
from datetime import time
from statistics import mean

from models.content_plan import ContentItem, ContentPlan
from models.enriched_photos import EnrichedPhoto, EnrichedPhotoSet
from models.manifest import AgendaSession, Photo, ProjectManifest, TextSnippet
from settings import Settings

logger = logging.getLogger(__name__)

# Photos whose combined_confidence falls below this get needs_review=True.
# Overridden by settings.match_confidence_threshold at runtime.
_DEFAULT_THRESHOLD = 0.65

# Minimum semantic score even when there is zero keyword overlap — a photo
# always belongs to the workshop regardless of topic match.
_SEMANTIC_FLOOR = 0.1


def run(
    settings: Settings,
    manifest: ProjectManifest,
    photo_set: EnrichedPhotoSet,
) -> ContentPlan:
    """Match photos to sessions and write content_plan.json.

    Returns the completed ContentPlan.
    """
    sessions = manifest.sessions
    photos = manifest.photos
    enriched_map = {e.photo_id: e for e in photo_set.enriched_photos}

    if not sessions:
        logger.warning("No sessions in manifest — producing empty ContentPlan.")
        plan = ContentPlan()
        _write_artifact(plan, settings)
        return plan

    # Build per-photo scores: {photo_id: {session_id: (temporal, semantic)}}
    photo_scores: dict[str, dict[str, tuple[float, float]]] = {}
    for photo in photos:
        enriched = enriched_map.get(photo.id)
        scores: dict[str, tuple[float, float]] = {}
        for session in sessions:
            t = _temporal_score(photo, session, sessions)
            s = _semantic_score(enriched, session, manifest.text_snippets)
            scores[session.id] = (t, s)
        photo_scores[photo.id] = scores

    # Assign each photo to its best session
    assignments: dict[str, list[str]] = {s.id: [] for s in sessions}
    photo_combined: dict[str, float] = {}
    threshold = settings.match_confidence_threshold

    for photo in photos:
        scores = photo_scores[photo.id]
        best_session_id = max(
            scores,
            key=lambda sid: _combined(scores[sid], settings),
        )
        assignments[best_session_id].append(photo.id)
        photo_combined[photo.id] = _combined(scores[best_session_id], settings)

    # Build ContentItems — one per session
    items: list[ContentItem] = []
    for i, session in enumerate(sessions, start=1):
        assigned = assignments[session.id]
        t_scores = [photo_scores[pid][session.id][0] for pid in assigned]
        s_scores = [photo_scores[pid][session.id][1] for pid in assigned]
        agg_temporal = mean(t_scores) if t_scores else 0.5
        agg_semantic = mean(s_scores) if s_scores else 0.5

        item = ContentItem(
            id=f"item_{i:03d}",
            session_ref=session.id,
            heading=session.name,
            photo_ids=assigned,
            text_snippet_ref=_find_text_snippet(session, manifest.text_snippets),
            temporal_confidence=round(agg_temporal, 4),
            semantic_confidence=round(agg_semantic, 4),
            needs_review=_combined((agg_temporal, agg_semantic), settings) < threshold,
        )
        items.append(item)
        logger.info(
            "  [%s] %s — %d photos, confidence %.2f%s",
            session.id,
            session.name,
            len(assigned),
            item.combined_confidence,
            " ⚠ review" if item.needs_review else "",
        )

    plan = ContentPlan(items=items)
    _write_artifact(plan, settings)

    logger.info("Stage 3b complete → %s", settings.cache_dir / "content_plan.json")
    logger.info("  Sessions: %d, Photos matched: %d", len(items), len(photos))

    return plan


# ---------------------------------------------------------------------------
# Temporal scoring
# ---------------------------------------------------------------------------

def _temporal_score(
    photo: Photo,
    session: AgendaSession,
    all_sessions: list[AgendaSession],
) -> float:
    """Score how well a photo's timestamp aligns with a session's time window.

    Returns a value in [0.0, 1.0]:
    - 1.0  timestamp falls inside the session window
    - 0.5  no timestamp or no session times — neutral / equal distribution
    - 0.0  timestamp clearly belongs to a different session's window
    """
    ts = photo.best_timestamp
    if ts is None:
        return 0.5

    # Strip tzinfo: datetime.time objects are always naive (no tz support),
    # and session start/end times are also naive — comparison requires both be naive.
    photo_time = ts.time().replace(tzinfo=None)

    # Check if any session has time data at all
    sessions_with_times = [s for s in all_sessions if s.start_time is not None]
    if not sessions_with_times:
        # No temporal info anywhere — distribute evenly
        return 0.5

    if session.start_time is None:
        # This session has no times but others do — lowest priority for time-stamped photos
        return 0.1

    start = session.start_time

    # Determine effective end: next session's start, or +90 min if last session
    end = session.end_time
    if end is None:
        next_sessions = [
            s for s in all_sessions
            if s.order > session.order and s.start_time is not None
        ]
        if next_sessions:
            end = min(next_sessions, key=lambda s: s.order).start_time
        else:
            # Last session — open-ended, grant 90 minutes
            end = _add_minutes(start, 90)

    if _time_in_window(photo_time, start, end):
        return 1.0

    # Outside window — score by proximity (decay over 30 minutes)
    dist_minutes = _minutes_distance(photo_time, start, end)
    return max(0.0, 1.0 - dist_minutes / 30.0)


def _time_in_window(t: time, start: time, end: time) -> bool:
    return start <= t <= end


def _minutes_distance(t: time, start: time, end: time) -> float:
    """Minutes outside the window [start, end]. 0 if inside."""
    t_mins = t.hour * 60 + t.minute
    s_mins = start.hour * 60 + start.minute
    e_mins = end.hour * 60 + end.minute
    if s_mins <= t_mins <= e_mins:
        return 0.0
    return min(abs(t_mins - s_mins), abs(t_mins - e_mins))


def _add_minutes(t: time, minutes: int) -> time:
    total = t.hour * 60 + t.minute + minutes
    total = min(total, 23 * 60 + 59)  # clamp to end of day
    return time(total // 60, total % 60)


# ---------------------------------------------------------------------------
# Semantic scoring
# ---------------------------------------------------------------------------

def _semantic_score(
    enriched: EnrichedPhoto | None,
    session: AgendaSession,
    text_snippets: list[TextSnippet],
) -> float:
    """Keyword overlap between photo content and session context.

    Uses Jaccard similarity on lowercased word tokens.
    Returns a value in [_SEMANTIC_FLOOR, 1.0].
    """
    if enriched is None:
        return _SEMANTIC_FLOOR

    # Photo word set: keywords + OCR text + description
    photo_words = _tokenize(
        " ".join(enriched.topic_keywords)
        + " " + (enriched.ocr_text or "")
        + " " + enriched.description
    )

    # Session word set: name + related text snippets
    snippet_text = " ".join(
        snip.content for snip in text_snippets
    )
    session_words = _tokenize(session.name + " " + snippet_text)

    if not photo_words or not session_words:
        return _SEMANTIC_FLOOR

    intersection = photo_words & session_words
    union = photo_words | session_words
    jaccard = len(intersection) / len(union)

    return max(_SEMANTIC_FLOOR, round(jaccard, 4))


def _tokenize(text: str) -> set[str]:
    """Lowercase word tokens, length >= 2, stripped of punctuation.

    Minimum length 2 preserves meaningful German abbreviations common in
    workshop documentation (OGS, KL, SL, TS, etc.).
    """
    words = re.findall(r'\b[a-zäöüßA-ZÄÖÜ]{2,}\b', text.lower())
    return set(words)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _combined(scores: tuple[float, float], settings: Settings) -> float:
    t, s = scores
    return round(
        settings.temporal_weight * t + settings.semantic_weight * s, 4
    )


def _find_text_snippet(
    session: AgendaSession,
    text_snippets: list[TextSnippet],
) -> str | None:
    """Assign a text snippet to a session.

    MVP stub: assigns the first snippet to the first session only.
    Next sprint: match snippets to sessions by keyword overlap.
    """
    if session.order == 1 and text_snippets:
        return text_snippets[0].id
    return None


def _write_artifact(plan: ContentPlan, settings: Settings) -> None:
    settings.cache_dir.mkdir(parents=True, exist_ok=True)
    artifact_path = settings.cache_dir / "content_plan.json"
    artifact_path.write_text(plan.model_dump_json(indent=2), encoding="utf-8")
