# Implementation Plan – Fotoprotokoll Generator

**Approach:** Agile, MVP-first. Build the thinnest possible end-to-end slice first, run it on real data, evaluate together, then refine sprint by sprint.

---

## Guiding Principle: Vertical Slices, Not Layers

Each sprint delivers something runnable and evaluable. We build the full pipeline end-to-end at low fidelity first, then increase fidelity per stage in subsequent sprints.

```
Fidelity
  │
  │                                          Sprint 5 ●──────
  │                              Sprint 4 ●──────
  │                  Sprint 3 ●──────
  │      Sprint 2 ●──────
  │  MVP ●──────────────────────────────────────────────────
  │
  └──────────────────────────────────────────────────────── Stages
     S1   S2   S3a  S3b  S4   S5
```

---

## MVP Sprint – "It Produces a PDF"

**Goal:** A working end-to-end run on real workshop data. Output a real PDF we can look at and critique together.

**What we deliberately skip:**
- Flipchart perspective correction (photos used as-is)
- Cache invalidation (re-run = re-run everything)
- `stream()` interface (only `run()`)
- Template analyzer (we write `design.yaml` manually)
- Embedding-based semantic matching (timestamp-only ordering)
- Quality scoring and deduplication
- Interactive review step
- Section divider pages

**Deliverables:**

### MVP-1: Project Scaffolding
- Full directory structure created
- `settings.py` — Pydantic `BaseSettings` with env file support
- `requirements.txt` — all dependencies pinned
- `tests/` directory with `conftest.py` and shared fixtures
- `.env.example`

**Tests:** Settings loads correctly from env; missing required fields raise a clear error.

---

### MVP-2: Pydantic Models
All stage contract models defined (even if not all fields are populated in the MVP):
- `models/manifest.py` — `WorkshopMeta`, `AgendaSession`, `Photo`, `TextSnippet`, `ProjectManifest`
- `models/photo_results.py` — `ProcessedPhoto`, `PhotoResults`
- `models/enriched_photos.py` — `EnrichedPhoto`, `EnrichedPhotoSet`
- `models/content_plan.py` — `ContentItem`, `ContentPlan`
- `models/page_plan.py` — `PagePlan`, `Page`, `PhotoSlot`, `TextBlock`
- `models/events.py` — `PipelineEvent`

**Tests:** Each model round-trips correctly through JSON (serialize → deserialize → equal).

---

### MVP-3: Stage 1 — Ingest
Parse the project directory and produce `manifest.json`.

- Detect agenda file (`.docx` or `.pdf`) in `data/agenda/`
- **Agenda parsing (minimal):** extract title and date from filename or first lines; session list is optional in MVP (if not found, use a single "Workshop" session)
- Inventory all photos in `data/fotos/` — filename, path, exif timestamp (fallback: file mtime), resolution, orientation
- Read all text snippets from `data/text/` (workshop-specific notes — not under `data/template/`)
- Write `manifest.json`

**Tests:**
- Given a fixture directory with known files, manifest contains correct photo count and metadata.
- Missing agenda → warning logged, single fallback session created.
- EXIF timestamp extracted correctly; file mtime used as fallback.

---

### MVP-4: Stage 3a — AI Enrichment
Analyze each photo with GPT-4o Vision and write `enriched_photos.json`.

**API standards applied (source: `developers.openai.com/api/docs/guides/images-vision`):**
- Model: `gpt-4o-2024-11-20` (pinned snapshot — no floating alias)
- Structured output: `client.beta.chat.completions.parse()` with a Pydantic `PhotoAnalysis` model → 100% schema conformance, no JSON parsing code
- Image delivery: base64-encoded JPEG (local files, no public URL)
- `detail` parameter: `"high"` if `is_flipchart=True` (tile-level resolution for text reading), `"auto"` otherwise
- **Order dependency:** Stage 3a must run after Stage 2 — send perspective-corrected images; model struggles with rotated/skewed content
- Retry: exponential backoff + jitter, max 6 attempts (official OpenAI recommendation for 429s)
- Per-photo result saved to `.cache/analyses/<content_hash>.json` (cache keyed by content hash — re-runs never re-call the API for unchanged photos)
- Write `enriched_photos.json`

In MVP, `is_flipchart` defaults to `False` for all photos (Stage 2 not yet built); `detail="auto"` for all.

**Tests:**
- Cache hit: second call with same photo does not call the API (mock verifies zero API calls).
- Structured response parsed directly into `PhotoAnalysis` Pydantic model — no manual JSON handling.
- Retry fires on `RateLimitError` with correct backoff (mock: fail twice, succeed on third attempt).
- `detail="high"` is passed when `is_flipchart=True`; `detail="auto"` otherwise.

---

### MVP-5: Stage 3b — Matching (Timestamp-Only)
Produce `content_plan.json` using only timestamp ordering. No embeddings yet.

- Sort photos by exif timestamp
- Assign photos to agenda sessions by time window (if sessions have times); otherwise divide photos evenly across sessions
- Match text snippets to sessions by order (first snippet → first session, etc.)
- Generate a German heading per content item from the session name
- Set `temporal_confidence` from timestamp; `semantic_confidence = 0.0` for MVP; flag everything with semantic_confidence = 0 as `needs_review = False` (review step not yet active)
- Write `content_plan.json`

**Tests:**
- Photos with timestamps within a session window are assigned correctly.
- Photos with no timestamp are distributed evenly.
- Text snippets with more sessions than snippets → some sessions have `text_snippet_ref = null`.

---

### MVP-6: Stage 4 — Layout Planning (Simple)
Produce `page_plan.json`.

- Always 1 photo per page (2-photo layout deferred to Sprint 3)
- Page sequence: cover → content pages (one per content item) → closing
- Cover: title, date from `WorkshopMeta`
- Content page: heading + photo + caption (photo description truncated to 1 sentence)
- Closing: static German closing text ("Dokumentation erstellt mit Fotoprotokoll Generator.")
- Write `page_plan.json`

**Tests:**
- N content items → N+2 pages (cover + N content + closing).
- Each content page has exactly one photo slot.
- Cover page contains correct title and date.

---

### MVP-7: Manual `design.yaml`
Write a minimal but real `design.yaml` by hand (DIN A4, clean sans-serif defaults). Template analyzer is deferred. This file lives in the repo under `data/template/` as a starting point.

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
  secondary: "#F4F7FA"
  text: "#1A1A1A"
  caption: "#666666"

typography:
  heading:  { font: "Helvetica", size_pt: 20, weight: bold }
  body:     { font: "Helvetica", size_pt: 10, weight: normal }
  caption:  { font: "Helvetica", size_pt: 8,  weight: normal }

assets:
  logo: null
  logo_position: top-right
```

---

### MVP-8: Stage 5 — Rendering (WeasyPrint + Jinja2)
Produce the final PDF.

- Jinja2 templates: `base.html`, `cover.html`, `content.html`, `closing.html`
- `base.html` injects `design.yaml` values as CSS custom properties
- `content.html`: heading (H1), photo (full-width), caption below
- Write `output/<title>_<date>.pdf`

**Tests:**
- Rendering a minimal `PagePlan` fixture produces a non-empty PDF file.
- PDF page count matches `PagePlan` page count (checked via `pdfplumber`).

---

### MVP-9: `main.py` — Wiring
Connect all stages into a single CLI command.

```bash
python main.py           # runs all stages in order
python main.py --stage 3a  # run through stage 3a only
```

- Progress printed to stdout: `[Stage 1/5] Ingesting project...`
- Errors exit with a clear message and non-zero exit code

**Tests:**
- Integration test: run full pipeline on a fixture project directory; assert PDF is created.

---

### MVP Evaluation Checkpoint

After MVP is complete, we run it on real workshop data and review the PDF together.

**Questions we'll answer:**
1. Is the photo-to-session matching good enough with timestamps alone, or do we urgently need embeddings?
2. Does the 1-photo-per-page layout feel too sparse, or is it a good baseline?
3. Is the design system (colors, fonts, spacing) close to what's needed, or far off?
4. Are flipchart photos readable without perspective correction, or is that a blocker?
5. What's missing that we didn't anticipate?

---

## Sprint 2 — Photo Quality & Flipchart Processing

**Goal:** Images look clean and professional in the PDF.

- `utils/image_utils.py` — flipchart detection (OpenCV contour), perspective warp, quality score (Laplacian)
- Stage 2 becomes a real stage: produces `photo_results.json`
- Duplicate detection: flag near-identical photos (perceptual hash)
- Low-quality photos flagged in `content_plan.json`

**New tests:** Flipchart crop on fixture images; quality score thresholds; duplicate detection.

---

## Sprint 3 — Smart Matching & Interactive Review

**Goal:** Photos are matched to content accurately; user can correct mistakes.

- Embedding-based semantic matching (`text-embedding-3-small`, batch multiple texts per API call)
- `temporal_confidence` + `semantic_confidence` both populated
- Items below threshold → `needs_review = true`
- `--review` flag: CLI presents flagged items and asks for confirmation
- 2-photo-per-page layout added to Stage 4

**New tests:** Matching algorithm with known fixtures; confidence thresholds; review prompt flow.

---

## Sprint 4 — Design Fidelity & Template Analyzer

**Goal:** Output matches the actual design template.

- `utils/template_analyzer.py` — extract design values from `reference.pdf`
- `--setup-template` command writes suggested `design.yaml`
- Section divider pages
- Cover page uses real logo from assets
- CSS/HTML templates refined to match reference layout closely

**New tests:** Template analyzer extracts expected values from a known reference PDF; cover page renders logo.

---

## Sprint 5 — Robustness & Polish

**Goal:** Tool is reliable on any real workshop dataset.

- Content-hash cache invalidation (`cache.py`)
- `stream()` async interface on all stages
- Structured JSON logging (`.cache/logs/`)
- Optional Batch API mode for Stage 3a (`--batch` flag): submit all photos as a single batch job via `/v1/chat/completions`, 50% cost reduction, 24h completion window
- Edge cases: missing agenda, no text snippets, single photo, 0 photos
- Full test coverage audit; fill gaps
- `requirements.txt` locked with `pip-compile`

---

## Test Strategy (All Sprints)

| Type | Tool | What |
|---|---|---|
| Unit | `pytest` | Each function in isolation, with fixtures |
| Integration | `pytest` | Full stage run on fixture project directory |
| Snapshot | `pytest` + file comparison | Stage output JSON matches expected fixture |
| PDF smoke test | `pdfplumber` | PDF opens, has correct page count, contains expected text |

Fixture project lives at `tests/fixtures/sample_project/` — a minimal but realistic workshop dataset (3 photos, 1 agenda, 1 text snippet).

OpenAI API calls are always mocked in tests using `unittest.mock` or `pytest-mock`. No real API calls in the test suite.

---

## File Delivery Sequence (MVP)

```
Sprint MVP
├── settings.py
├── requirements.txt
├── .env.example
├── tests/
│   └── conftest.py
│   └── fixtures/sample_project/...
├── models/
│   ├── manifest.py
│   ├── photo_results.py
│   ├── enriched_photos.py
│   ├── content_plan.py
│   ├── page_plan.py
│   └── events.py
├── pipeline/
│   ├── stage1_ingest.py
│   ├── stage3a_enrich.py
│   ├── stage3b_match.py
│   ├── stage4_layout.py
│   └── stage5_render.py
├── ai/
│   └── vision.py
├── utils/
│   └── agenda_parser.py
├── templates/
│   ├── base.html
│   ├── cover.html
│   ├── content.html
│   └── closing.html
├── data/template/design.yaml
└── main.py
```
