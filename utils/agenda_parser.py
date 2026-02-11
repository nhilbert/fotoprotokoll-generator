"""Agenda document parser.

Primary path: send document text to GPT (structured output) to extract
title, date, location, participants, and sessions reliably from any format.

Fallback path: regex-based extraction used when the API is unavailable,
in offline mode, or in unit tests that mock the API.
"""
import logging
import re
import random
import time as time_module
from datetime import date, time
from pathlib import Path

from openai import OpenAI, RateLimitError
from pydantic import BaseModel, ConfigDict

from models.manifest import AgendaSession, WorkshopMeta
from settings import Settings
from utils.openai_utils import strict_schema as _strict_schema

logger = logging.getLogger(__name__)


class _SessionSchema(BaseModel):
    model_config = ConfigDict(json_schema_extra=_strict_schema)

    name: str
    start_time: str | None = None   # "HH:MM" or null
    end_time: str | None = None     # "HH:MM" or null


class _AgendaSchema(BaseModel):
    model_config = ConfigDict(json_schema_extra=_strict_schema)

    title: str
    workshop_date: str | None = None   # ISO format "YYYY-MM-DD" or null
    location: str | None = None
    participants: int | None = None
    sessions: list[_SessionSchema]


_SYSTEM_PROMPT = """\
Du bist ein Assistent, der Workshop-Agenden analysiert.
Extrahiere aus dem folgenden Dokument:
- Titel des Workshops
- Datum (im Format YYYY-MM-DD, falls erkennbar)
- Ort (falls angegeben)
- Anzahl der Teilnehmer (falls angegeben)
- Tagesordnungspunkte / Programmpunkte mit Uhrzeiten (HH:MM), falls vorhanden

Sind keine Uhrzeiten angegeben, liefere eine einzelne Session mit dem Workshop-Titel als Namen.
Antworte ausschließlich im vorgegebenen JSON-Schema.
"""


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def parse_agenda(
    agenda_path: Path,
    settings: Settings,
) -> tuple[WorkshopMeta, list[AgendaSession]]:
    """Parse an agenda file into metadata and sessions.

    Uses GPT structured output as the primary extraction method.
    Falls back to regex parsing if the API call fails.
    """
    text = _read_text(agenda_path)

    try:
        extraction = _extract_via_llm(text, settings)
        logger.info("Agenda parsed via LLM (%s).", settings.text_model)
    except Exception as exc:
        logger.warning("LLM agenda extraction failed (%s); falling back to regex.", exc)
        extraction = _extract_via_regex(text, agenda_path)

    meta = WorkshopMeta(
        title=extraction.title,
        workshop_date=_parse_iso_date(extraction.workshop_date),
        location=extraction.location,
        participants=extraction.participants,
    )
    sessions = _sessions_from_schema(extraction.sessions)
    return meta, sessions


# ---------------------------------------------------------------------------
# LLM extraction
# ---------------------------------------------------------------------------

def _extract_via_llm(text: str, settings: Settings) -> _AgendaSchema:
    client = OpenAI(api_key=settings.openai_api_key)
    for attempt in range(6):
        try:
            response = client.beta.chat.completions.parse(
                model=settings.text_model,
                messages=[
                    {"role": "system", "content": _SYSTEM_PROMPT},
                    {"role": "user", "content": text[:8000]},  # stay within context budget
                ],
                response_format=_AgendaSchema,
            )
            return response.choices[0].message.parsed
        except RateLimitError:
            if attempt == 5:
                raise
            delay = 2 ** attempt + random.uniform(0, 1)
            logger.debug("Rate limited; retrying in %.1fs (attempt %d/6).", delay, attempt + 1)
            time_module.sleep(delay)

    raise RuntimeError("Unreachable")  # pragma: no cover


# ---------------------------------------------------------------------------
# Regex fallback
# ---------------------------------------------------------------------------

_TITLE_RE = re.compile(r'^(?:Titel|Title|Thema|Name)\s*:\s*(.+)$', re.MULTILINE | re.IGNORECASE)
_DATE_LABEL_RE = re.compile(r'^(?:Datum|Date)\s*:\s*(.+)$', re.MULTILINE | re.IGNORECASE)
_LOCATION_RE = re.compile(r'^(?:Ort|Location|Veranstaltungsort)\s*:\s*(.+)$', re.MULTILINE | re.IGNORECASE)
_PARTICIPANTS_RE = re.compile(r'^(?:Teilnehmer|Participants|TN)\s*:\s*(\d+)', re.MULTILINE | re.IGNORECASE)
_SESSION_RE = re.compile(r'^\s*(\d{1,2})[:\.](\d{2})\s+(.+)$', re.MULTILINE)


def _extract_via_regex(text: str, path: Path) -> _AgendaSchema:
    """Best-effort regex extraction — used as LLM fallback."""
    title = _regex_title(text, path)
    raw_date = _regex_date(text, path)
    workshop_date = raw_date.isoformat() if raw_date else None
    location_match = _LOCATION_RE.search(text)
    participants_match = _PARTICIPANTS_RE.search(text)

    raw_sessions: list[_SessionSchema] = []
    for m in _SESSION_RE.finditer(text):
        name = m.group(3).strip()
        if name and len(name) > 2:
            raw_sessions.append(_SessionSchema(
                name=name,
                start_time=f"{int(m.group(1)):02d}:{m.group(2)}",
            ))
    # Patch end times from next session's start
    for i in range(len(raw_sessions) - 1):
        raw_sessions[i] = raw_sessions[i].model_copy(
            update={"end_time": raw_sessions[i + 1].start_time}
        )

    if not raw_sessions:
        raw_sessions = [_SessionSchema(name="Workshop")]

    return _AgendaSchema(
        title=title,
        workshop_date=workshop_date,
        location=location_match.group(1).strip() if location_match else None,
        participants=int(participants_match.group(1)) if participants_match else None,
        sessions=raw_sessions,
    )


def _regex_title(text: str, path: Path) -> str:
    m = _TITLE_RE.search(text)
    if m:
        return m.group(1).strip()
    for line in text.splitlines():
        line = line.strip()
        if line and not re.match(r'^\d{1,2}[:.]\d{2}', line) and len(line) > 3:
            return line
    return _clean_filename(path.stem)


def _regex_date(text: str, path: Path) -> date | None:
    m = _DATE_LABEL_RE.search(text)
    if m:
        result = _parse_date_string(m.group(1).strip())
        if result:
            return result
    return _parse_date_string(text) or _parse_date_string(path.stem)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _sessions_from_schema(raw: list[_SessionSchema]) -> list[AgendaSession]:
    sessions = []
    for i, s in enumerate(raw, start=1):
        sessions.append(AgendaSession(
            id=f"session_{i:03d}",
            order=i,
            name=s.name,
            start_time=_parse_time_string(s.start_time),
            end_time=_parse_time_string(s.end_time),
        ))
    return sessions or [AgendaSession(id="session_001", order=1, name="Workshop")]


def _parse_time_string(value: str | None) -> time | None:
    if not value:
        return None
    m = re.match(r'^(\d{1,2}):(\d{2})$', value.strip())
    if m:
        try:
            return time(int(m.group(1)), int(m.group(2)))
        except ValueError:
            pass
    return None


def _parse_iso_date(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return _parse_date_string(value)


def _parse_date_string(text: str) -> date | None:
    for pattern, groups in [
        (r'\b(\d{2})\.(\d{2})\.(\d{4})\b', lambda m: date(int(m.group(3)), int(m.group(2)), int(m.group(1)))),
        (r'\b(\d{2})\.(\d{2})\.(\d{2})\b',  lambda m: date(2000 + int(m.group(3)), int(m.group(2)), int(m.group(1)))),
        (r'\b(\d{4})-(\d{2})-(\d{2})\b',    lambda m: date(int(m.group(1)), int(m.group(2)), int(m.group(3)))),
    ]:
        m = re.search(pattern, text)
        if m:
            try:
                return groups(m)
            except ValueError:
                pass
    return None


def _clean_filename(stem: str) -> str:
    cleaned = re.sub(r'\d{2}[.\-_]\d{2}[.\-_]\d{2,4}', '', stem)
    cleaned = re.sub(r'_final|_v\d+|_draft', '', cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r'[_\-]+', ' ', cleaned).strip()
    return cleaned if cleaned else stem


# ---------------------------------------------------------------------------
# Text reading
# ---------------------------------------------------------------------------

def _read_text(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix == ".docx":
        return _read_docx(path)
    if suffix == ".pdf":
        return _read_pdf(path)
    return path.read_text(encoding="utf-8", errors="replace")


def _read_docx(path: Path) -> str:
    from docx import Document
    doc = Document(path)
    return "\n".join(p.text for p in doc.paragraphs if p.text.strip())


def _read_pdf(path: Path) -> str:
    import pdfplumber
    with pdfplumber.open(path) as pdf:
        return "\n".join(page.extract_text() or "" for page in pdf.pages)
