# Fotoprotokoll Generator – Architecture

**Version:** 0.2
**Date:** 2026-02-11
**Status:** Revised after senior architect review

---

## 1. Guiding Principles

- **Pipeline architecture:** 6 discrete, independently runnable stages. Each stage reads its own inputs, writes exactly one output artifact, and never mutates a previous stage's artifact.
- **Single-writer rule:** Every artifact (`*.json`, `design.yaml`, processed images) is written by exactly one stage. Downstream stages read; they never write back upstream.
- **Resumable by default:** A content-hash cache manifest tracks inputs per stage. A stage is skipped if its inputs haven't changed. `--force-stage N` re-runs from stage N and invalidates all downstream caches.
- **AI as a service:** All OpenAI calls are isolated in `ai/`. Per-call results are cached individually by content hash. All calls include a retry wrapper with exponential backoff.
- **Injectable configuration:** A Pydantic `BaseSettings` object is constructed once and passed into every stage. No global config state. Fully testable.
- **UI-ready from day one:** Each stage exposes both `run()` (batch/CLI) and `stream()` (async generator for UI/WebSocket). Defining both costs nothing now and avoids a full refactor later.
- **Design is data, not code:** The visual design system lives in a human-editable `design.yaml`. The template analyzer *suggests* initial values; a human confirms them once. Stage 5 (rendering) reads only from `design.yaml` — no hardcoded coordinates or styles.
- **German language throughout:** All AI prompts, generated headings, and captions default to German (`de`).

---

## 2. Repository Structure

```
fotoprotokoll-generator/
│
├── main.py                         # CLI entry point (typer)
│
├── pipeline/                       # One module per stage
│   ├── stage1_ingest.py            # Parse agenda, template, inventory photos
│   ├── stage2_photos.py            # Crop, correct, quality-score photos
│   ├── stage3a_enrich.py           # AI photo analysis & OCR (per-photo, cached)
│   ├── stage3b_match.py            # Content matching: photos ↔ text ↔ agenda
│   ├── stage4_layout.py            # Page layout planning
│   └── stage5_render.py            # PDF generation via WeasyPrint + Jinja2
│
├── models/                         # Pydantic data models (stage contracts)
│   ├── manifest.py                 # ProjectManifest       (Stage 1 output)
│   ├── photo_results.py            # PhotoResults          (Stage 2 output)
│   ├── enriched_photos.py          # EnrichedPhotoSet      (Stage 3a output)
│   ├── content_plan.py             # ContentPlan           (Stage 3b output)
│   ├── page_plan.py                # PagePlan              (Stage 4 output)
│   └── events.py                   # PipelineEvent         (for stream() interface)
│
├── ai/
│   ├── vision.py                   # GPT-4o Vision: analyze_photo(), per-photo cache
│   ├── text_analysis.py            # GPT: analyze_text_snippet()
│   └── matching.py                 # Embedding-based semantic matching
│
├── utils/
│   ├── agenda_parser.py            # Parse .docx / .pdf agenda → AgendaSessions
│   ├── template_analyzer.py        # Suggest DesignSystem values → design.yaml
│   └── image_utils.py              # OpenCV: flipchart detect/crop, quality score
│
├── templates/                      # Jinja2 HTML templates for PDF rendering
│   ├── base.html                   # Master layout with CSS variables from design.yaml
│   ├── cover.html
│   ├── content.html                # 1-photo and 2-photo variants
│   ├── section_divider.html
│   └── closing.html
│
├── settings.py                     # Pydantic BaseSettings (injected, not global)
├── cache.py                        # Content-hash cache manifest logic
├── requirements.txt
│
└── data/                           # gitignored
    ├── agenda/                     # Per-workshop: agenda .docx or .pdf
    ├── fotos/                      # Per-workshop: raw photos
    ├── text/                       # Per-workshop: written notes, documentation (.md/.txt)
    ├── template/                   # Reusable across workshops (update occasionally)
    │   ├── reference.pdf           # Design reference PDF (read-only)
    │   └── design.yaml             # *** Confirmed design system (human-editable) ***
    ├── assets/                     # Stable brand assets: logos, decorative images
    ├── .cache/
    │   ├── cache_manifest.json     # Input hashes + artifact paths per stage
    │   ├── manifest.json           # Stage 1 output
    │   ├── photo_results.json      # Stage 2 output
    │   ├── processed/              # Stage 2 processed images
    │   ├── analyses/               # Stage 3a per-photo JSON (keyed by photo hash)
    │   ├── enriched_photos.json    # Stage 3a output
    │   ├── content_plan.json       # Stage 3b output
    │   ├── page_plan.json          # Stage 4 output
    │   └── logs/                   # Structured JSON run logs
    └── output/                     # Final PDF(s)
```

---

## 3. Data Flow

```
data/agenda/     ──────────────────────────────────────────────┐
data/fotos/      ───────────────────────────────────────────┐  │
data/text/       ────────────────────────────────────────┐  │  │
data/assets/     ──────────────────────────────────┐  │  │  │  │
                                                  │  │  │  │  │
                                         ┌────────▼──▼──▼──▼──▼──────────┐
                                                 │   Stage 1: Ingest       │
                                                 │   manifest.json         │
                                                 └───────────┬─────────────┘
                                                             │
                                                 ┌───────────▼─────────────┐
                                                 │   Stage 2: Photo        │
                                                 │   Processing            │
                                                 │   photo_results.json    │
                                                 └───────────┬─────────────┘
                                                             │
                                                 ┌───────────▼─────────────┐
                                                 │   Stage 3a: AI          │◄── OpenAI GPT-4o Vision
                                                 │   Enrichment            │    (per-photo, cached)
                                                 │   enriched_photos.json  │
                                                 └───────────┬─────────────┘
                                                             │
                                                 ┌───────────▼─────────────┐
                                                 │   Stage 3b: Matching    │◄── OpenAI Embeddings
                                                 │   content_plan.json     │
                                                 └───────────┬─────────────┘
                                                             │
                                              (optional interactive review)
                                                             │
                                                 ┌───────────▼─────────────┐
                                                 │   Stage 4: Layout       │
                                                 │   page_plan.json        │
                                                 └───────────┬─────────────┘
                                                             │
                                                 ┌───────────▼─────────────┐
                                                 │   Stage 5: Render       │◄── design.yaml
                                                 │   output/<title>.pdf    │◄── templates/*.html
                                                 └─────────────────────────┘
```

**Special: Template Setup Flow (once per template, not per workshop)**
```
data/template/reference.pdf
        │
        ▼
template_analyzer.py  ──suggests──►  data/template/design.yaml  (human confirms)
        │                                        │
        └── runs once, result persisted ─────────┘
```

---

## 4. Stage Contracts (Data Models)

### Stage 1 → `ProjectManifest`
```
WorkshopMeta        title, date, location, participants
AgendaSessions[]    id, order, name, start_time, end_time
Photos[]            id, filename, path, timestamp_exif, timestamp_file,
                    resolution, orientation
TextSnippets[]      id, filename, content, word_count
```
> `TextSnippets` are read from `data/text/` — workshop-specific notes, not from `data/template/`.
> Design is loaded from `data/template/design.yaml` at render time only (not part of this manifest).

### Stage 2 → `PhotoResults`
```
ProcessedPhotos[]
  ├── photo_id          (ref to ProjectManifest.Photos[].id)
  ├── processed_path    (path to processed image in .cache/processed/)
  ├── is_flipchart      (bool)
  ├── crop_applied      (bool)
  ├── quality_score     (0.0–1.0, Laplacian + exposure)
  ├── duplicate_of      (photo_id or null)
  └── content_hash      (SHA256 of processed image, used for cache keying)
```

### Stage 3a → `EnrichedPhotoSet`
```
EnrichedPhotos[]
  ├── photo_id
  ├── scene_type        (flipchart | group | activity | result | unknown)
  ├── description       (German, 2–4 sentences)
  ├── ocr_text          (extracted text if flipchart, else null)
  ├── topic_keywords[]  (German keywords extracted by AI)
  └── analysis_model    (model version used, for auditability)
```
> Each analysis is also persisted individually as `.cache/analyses/<content_hash>.json`
> so re-runs don't re-call the API for unchanged photos.

### Stage 3b → `ContentPlan`
```
ContentItems[]
  ├── id
  ├── session_ref           (AgendaSession.id)
  ├── heading               (German, AI-generated from session + photo content)
  ├── photos[]              (ordered list of photo_ids)
  ├── text_snippet_ref      (TextSnippet.id or null)
  ├── temporal_confidence   (0.0–1.0: how well timestamps fit the session window)
  ├── semantic_confidence   (0.0–1.0: embedding similarity of photo ↔ text/session)
  ├── combined_confidence   (weighted average)
  └── needs_review          (bool: true if combined_confidence < threshold)
```

### Stage 4 → `PagePlan`
```
Pages[]
  ├── page_number
  ├── page_type             (cover | section_divider | content | closing)
  ├── layout_variant        (1-photo | 2-photo | text-only | photo-left | photo-right)
  ├── content_item_ref      (ContentItem.id or null)
  ├── photo_slots[]
  │     ├── photo_id
  │     ├── caption         (German)
  │     └── display_size    (full-width | half-width | portrait-pair)
  └── text_blocks[]
        ├── content
        ├── role            (heading | body | caption | footer)
        └── style_ref       (key into design.yaml typography section)
```

---

## 5. Cache Invalidation (`cache.py`)

```
cache_manifest.json structure:
{
  "stage1": { "input_hash": "abc123", "artifact": ".cache/manifest.json" },
  "stage2": { "input_hash": "def456", "artifact": ".cache/photo_results.json" },
  ...
}
```

- Each stage computes a **stage input hash**: SHA256 of all input file contents + relevant settings values.
- At stage entry: if `input_hash` matches, load cached artifact and skip.
- If `input_hash` differs: re-run stage, write new artifact, **and invalidate all downstream stages** by removing their cache entries.
- `--force-stage N` removes cache entries for stages N through 5.

---

## 6. Configuration (`settings.py`)

```python
from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    openai_api_key: str
    project_dir: Path = Path("./data")
    match_confidence_threshold: float = 0.65
    max_photos_per_page: int = 2
    language: str = "de"
    section_dividers: bool = False
    log_level: str = "INFO"

    model_config = SettingsConfigDict(env_file=".env", env_prefix="FPG_")
```

- Instantiated once in `main.py`, passed into every stage `run(settings, ...)` call.
- Tests inject a `Settings(openai_api_key="test", ...)` directly — no monkeypatching needed.
- CLI flags override settings via typer's callback, which updates the `Settings` instance before the pipeline runs.

---

## 7. Stage Interface Pattern

Every stage module exposes two functions:

```python
# Batch / CLI use — returns completed artifact
def run(settings: Settings, *inputs) -> Artifact: ...

# UI / streaming use — yields progress events, then returns artifact
async def stream(settings: Settings, *inputs) -> AsyncGenerator[PipelineEvent, None]: ...
```

`PipelineEvent` model:
```python
class PipelineEvent(BaseModel):
    stage: str           # e.g. "stage3a"
    step: str            # e.g. "analyzing_photo"
    progress: float      # 0.0–1.0
    message: str         # Human-readable status (German)
    payload: dict | None # Optional data (e.g. partial PhotoAnalysis)
```

The CLI `run()` simply drives `stream()` internally and prints progress to stdout.
A future FastAPI backend forwards events to a WebSocket connection.

---

## 8. AI Module Design

### API Standards (as of 2026-02)

Based on the official OpenAI docs at `developers.openai.com/api/docs/guides/images-vision` and `developers.openai.com/api/reference/resources/responses`.

| Concern | Decision | Rationale |
|---|---|---|
| Model | `gpt-4o-2024-11-20` (pinned snapshot) | Avoid unexpected behavior from floating aliases like `gpt-4o` |
| API | Chat Completions (`/v1/chat/completions`) | Sufficient for our stateless request/response pattern; Responses API adds statefulness we don't need |
| Structured output | `client.beta.chat.completions.parse()` with Pydantic models | 100% schema conformance, no fragile JSON parsing |
| `detail` parameter | `high` for flipchart photos, `auto` for all others | Flipcharts need tile-level resolution to read text; `auto` is sufficient for scene classification |
| Image delivery | Base64-encoded (local files) | No public URL available; stays within 50 MB payload limit for our photo sizes |
| Batch mode | Optional via Batch API (`/v1/chat/completions`) | 50% cost reduction; viable since Stage 3a is not latency-sensitive |
| Embeddings model | `text-embedding-3-small` | `text-embedding-ada-002` deprecated Jan 2025; `3-small` is cost-effective for our matching use case |
| Retry strategy | Exponential backoff + jitter, max 6 attempts | Official OpenAI recommendation for 429 handling |

**Critical ordering constraint:** Stage 2 (perspective correction of flipcharts) **must** run before Stage 3a. The model struggles with rotated/skewed content — send corrected images only.

### `ai/vision.py`

```python
from pydantic import BaseModel
from openai import OpenAI

class PhotoAnalysis(BaseModel):
    scene_type: Literal["flipchart", "group", "activity", "result", "unknown"]
    description: str          # German, 2–4 sentences
    ocr_text: str | None      # Extracted flipchart text, else null
    topic_keywords: list[str] # German keywords

def analyze_photo(
    image_path: Path,
    content_hash: str,
    is_flipchart: bool,
    cache_dir: Path,
    settings: Settings,
) -> PhotoAnalysis:
    cache_file = cache_dir / "analyses" / f"{content_hash}.json"
    if cache_file.exists():
        return PhotoAnalysis.model_validate_json(cache_file.read_text())

    detail = "high" if is_flipchart else "auto"
    result = _call_with_retry(image_path, detail, settings)
    cache_file.write_text(result.model_dump_json())
    return result

def _call_with_retry(image_path: Path, detail: str, settings: Settings) -> PhotoAnalysis:
    client = OpenAI(api_key=settings.openai_api_key)
    base64_image = _encode_image(image_path)

    for attempt in range(6):
        try:
            response = client.beta.chat.completions.parse(
                model="gpt-4o-2024-11-20",
                messages=[{
                    "role": "user",
                    "content": [
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": f"data:image/jpeg;base64,{base64_image}",
                                "detail": detail,
                            },
                        },
                        {"type": "text", "text": ANALYSIS_PROMPT_DE},
                    ],
                }],
                response_format=PhotoAnalysis,
            )
            return response.choices[0].message.parsed
        except RateLimitError:
            if attempt == 5:
                raise
            time.sleep(2 ** attempt + random.uniform(0, 1))
```

### `ai/matching.py`
- Embeds photo `topic_keywords` + `description` and `TextSnippet.content` using `text-embedding-3-small`.
- Batch multiple texts in a single embeddings call (not one call per text).
- Cosine similarity matrix: photos × text snippets (vectors are unit-length — dot product equals cosine similarity).
- Temporal matching: assign photo to session whose `[start_time, end_time]` window contains the photo's exif timestamp. Confidence = 1.0 if within window, decays linearly outside.
- Final `combined_confidence = 0.6 * temporal + 0.4 * semantic` (weights configurable in `settings.py`).

---

## 9. Design System (`design.yaml`)

Produced once by `template_analyzer.py`, confirmed by human, stored in `data/template/design.yaml`:

```yaml
page:
  width_mm: 210
  height_mm: 297
  margin_top_mm: 20
  margin_bottom_mm: 20
  margin_left_mm: 20
  margin_right_mm: 20

colors:
  primary: "#1A3A5C"
  secondary: "#E8EEF4"
  text: "#222222"
  caption: "#666666"

typography:
  heading:  { font: "Helvetica Neue", size_pt: 18, weight: bold, color: primary }
  body:     { font: "Helvetica Neue", size_pt: 10, weight: normal, color: text }
  caption:  { font: "Helvetica Neue", size_pt: 8,  weight: normal, color: caption }
  footer:   { font: "Helvetica Neue", size_pt: 7,  weight: normal, color: caption }

assets:
  logo: "assets/logo.png"
  logo_position: top-right   # top-left | top-right | bottom-left | bottom-right
```

Stage 5 reads this file at startup. Design changes = YAML edits, not code changes.

---

## 10. PDF Rendering (Stage 5)

**Technology:** `WeasyPrint` + `Jinja2` HTML templates.

Rationale over ReportLab:
- Design changes are CSS/HTML, not Python coordinate math.
- Jinja2 templates are readable and editable by non-developers.
- WeasyPrint handles print layout, pagination, and bleed correctly.
- CSS variables map directly to `design.yaml` values.

Rendering flow:
1. Load `design.yaml` → inject as CSS custom properties into `base.html`.
2. For each page in `PagePlan`, select the appropriate Jinja2 template.
3. Render template with page data → HTML string.
4. Concatenate all pages → single HTML document.
5. `weasyprint.HTML(string=html).write_pdf("output/<title>.pdf")`.

---

## 11. Logging

Each pipeline run writes a structured log to `.cache/logs/<timestamp>_run.jsonl`:

```json
{"ts": "2026-02-11T10:23:01Z", "stage": "stage3a", "event": "photo_analyzed",
 "photo_id": "img_012", "model": "gpt-4o", "duration_ms": 1240, "cached": false}
{"ts": "2026-02-11T10:23:02Z", "stage": "stage3b", "event": "match_low_confidence",
 "photo_id": "img_007", "session": "session_2", "combined_confidence": 0.51}
```

Log level controlled by `settings.log_level`. `--verbose` sets `DEBUG`.

---

## 12. Technology Stack

| Concern | Library | Notes |
|---|---|---|
| CLI | `typer` | |
| Data models | `pydantic` v2 | |
| Settings | `pydantic-settings` | Replaces plain config.py |
| Agenda parsing (.docx) | `python-docx` | |
| Agenda/template parsing (.pdf) | `pdfplumber` | |
| Image processing | `opencv-python`, `Pillow` | |
| AI vision & OCR | `openai` (GPT-4o) | Per-photo cache + retry |
| Semantic matching | `openai` (text-embedding-3-small) | |
| PDF generation | `weasyprint` | Replaces ReportLab |
| HTML templates | `Jinja2` | |
| Config / env | `python-dotenv` | |

---

## 13. CLI Design

```bash
# Full run (default)
python main.py

# Full run with interactive review after Stage 3b
python main.py --review

# Run through Stage 2 only (inspect crops before committing to AI calls)
python main.py --stage 2

# Force re-run from Stage 3a onwards (e.g. after adding photos)
python main.py --force-stage 3a

# Custom project directory
python main.py ./my-workshop-2026

# Override output path
python main.py --output ./reports/workshop_final.pdf
```

---

## 14. Template Setup (One-Time, Per Template)

```bash
# Analyze reference PDF and write suggested design.yaml
python main.py --setup-template

# → writes data/template/design.yaml with suggested values
# → user reviews and edits design.yaml
# → subsequent runs read from design.yaml directly (template_analyzer not re-run)
```

---

## 15. Implementation Order

| Phase | Target | Goal |
|---|---|---|
| 1 | Scaffolding + settings + cache | Project structure, `Settings`, `cache.py`, data models |
| 2 | Stage 1 | Parse agenda + inventory photos; confirm `ProjectManifest` |
| 3 | Template setup + `design.yaml` | `template_analyzer.py` → suggest → human-confirm design |
| 4 | Stage 2 | Flipchart crop + quality scoring → `PhotoResults` |
| 5 | Stage 3a | GPT-4o photo analysis with per-photo cache → `EnrichedPhotoSet` |
| 6 | Stage 3b + review | Matching + confidence scores + interactive review → `ContentPlan` |
| 7 | Stage 4 | Layout planning → `PagePlan` |
| 8 | Stage 5 | WeasyPrint rendering → first complete PDF |
| 9 | Polish | Design fidelity, edge cases, stream() interface, logging |
