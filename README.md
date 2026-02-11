# Fotoprotokoll-Generator

Automatically generates a polished PDF photo-protocol from workshop photos and an agenda. The pipeline ingests your raw inputs (photos, agenda, text notes), uses AI to analyse and match content to sessions, plans the page layout, and renders a print-ready PDF.

---

## What it does

- Parses a workshop agenda (`.docx`) to extract sessions and timing
- Inventories workshop photos and text snippets from a folder
- Uses GPT vision to analyse each photo (scene type, description, topic keywords)
- Matches photos to agenda sessions by timestamp and semantic similarity
- Plans page layout automatically (1- or 2-photo pages, text pages, section dividers)
- Renders a branded, print-ready PDF via WeasyPrint + Jinja2
- Design (colours, fonts, page dimensions) is configured via a human-editable `design.yaml` — no code changes needed for styling

---

## Quick Start

### Prerequisites

- Python 3.11+
- An OpenAI API key
- WeasyPrint system dependencies (GTK/Pango — see [WeasyPrint docs](https://doc.courtbouillon.org/weasyprint/stable/first_steps.html#installation))

### Install

```bash
pip install -r requirements.txt
```

### Configure

Copy `.env - Kopie.example` to `.env` and set your API key:

```dotenv
FPG_OPENAI_API_KEY=sk-...
```

### Prepare your data

Place your workshop files under `data/`:

```
data/
├── agenda/          ← workshop agenda (.docx or .pdf)
├── fotos/           ← workshop photos (.jpg / .png)
├── text/            ← optional: written notes / documentation (.md or .txt)
├── assets/          ← optional: logo file (logo.png / logo.svg)
└── template/
    └── design.yaml  ← design system (colours, fonts, margins)
```

### Run

```bash
# Full pipeline
python run_pipeline.py

# Resume from a specific stage (uses cached outputs from earlier stages)
python run_pipeline.py --from-stage 4   # re-run layout + render
python run_pipeline.py --from-stage 5   # re-render only (preserves manual page_plan edits)
```

Output PDF is written to `data/output/`.

---

## Pipeline Stages

```
Stage 1: Ingest
  Parses agenda (.docx/.pdf), inventories photos and text snippets.
  Output: data/.cache/manifest.json

Stage 3a: Enrich Photos
  Sends each photo to GPT vision for scene classification, description,
  OCR text (flipcharts), and topic keywords. Results cached per photo.
  Output: data/.cache/enriched_photos.json

Stage 3b: Match Content
  Matches photos to agenda sessions using timestamp proximity and
  semantic embedding similarity. Flags low-confidence matches for review.
  Output: data/.cache/content_plan.json

Stage 4: Layout
  Plans every page: cover, section dividers, 1- and 2-photo content pages,
  text-only pages, and an Anhang for unmatched text snippets.
  Output: data/.cache/page_plan.json

Stage 5: Render
  Renders Jinja2 HTML templates with design.yaml values, then converts
  to PDF via WeasyPrint. Validates font embedding in the output.
  Output: data/output/<title>.pdf
```

> **Note:** Stage 2 (photo processing / flipchart crop) is planned but not yet implemented. Photos are used as-is.

---

## Configuration

All settings use the `FPG_` prefix and can be set in `.env` or as environment variables:

| Variable | Default | Description |
|---|---|---|
| `FPG_OPENAI_API_KEY` | *(required)* | OpenAI API key |
| `FPG_VISION_MODEL` | `gpt-5` | Model for photo analysis |
| `FPG_TEXT_MODEL` | `gpt-5` | Model for agenda parsing and heading generation |
| `FPG_PROJECT_DIR` | `./data` | Root directory for all workshop data |
| `FPG_MATCH_CONFIDENCE_THRESHOLD` | `0.65` | Minimum confidence to auto-assign a photo to a session |
| `FPG_TEMPORAL_WEIGHT` | `0.6` | Weight of timestamp-based matching (0–1) |
| `FPG_SEMANTIC_WEIGHT` | `0.4` | Weight of embedding-based matching (0–1) |
| `FPG_MAX_PHOTOS_PER_PAGE` | `2` | Maximum photos on a single content page |
| `FPG_LANGUAGE` | `de` | Output language for AI-generated text |
| `FPG_SECTION_DIVIDERS` | `false` | Insert section divider pages between agenda sections |
| `FPG_LOG_LEVEL` | `INFO` | Logging verbosity (`DEBUG`, `INFO`, `WARNING`) |

---

## Design System

Visual styling lives entirely in `data/template/design.yaml`:

```yaml
page:
  width_mm: 210
  height_mm: 297
  margin_top_mm: 20
  margin_bottom_mm: 20
  margin_left_mm: 20
  margin_right_mm: 20

colors:
  primary: "#1A3A5C"     # headings, titles
  secondary: "#F4F7FA"   # backgrounds
  accent: "#00897B"      # cover outline, separator lines
  text: "#1A1A1A"        # body text
  caption: "#666666"     # captions and meta text

typography:
  heading:  { font: "DejaVu Sans", size_pt: 20, weight: bold }
  body:     { font: "DejaVu Sans", size_pt: 10, weight: normal }
  caption:  { font: "DejaVu Sans", size_pt: 8,  weight: normal }

assets:
  logo: null             # path relative to data/assets/, or null
  logo_position: top-right
```

Changing a colour or font only requires editing this file and re-running `--from-stage 5`.

---

## Manual Page Plan Edits

The intermediate page plan (`data/.cache/page_plan.json`) is human-editable JSON. After editing it, re-render without losing your changes:

```bash
python run_pipeline.py --from-stage 5
```

This skips all earlier stages and renders directly from your edited plan.

---

## Project Structure

```
fotoprotokoll-generator/
├── run_pipeline.py          # CLI entry point (--from-stage N)
├── settings.py              # Pydantic settings (FPG_ env vars)
│
├── pipeline/
│   ├── stage1_ingest.py     # Agenda parsing + photo inventory
│   ├── stage3a_enrich.py    # GPT vision analysis per photo
│   ├── stage3b_match.py     # Photo ↔ session matching
│   ├── stage4_layout.py     # Page layout planning
│   └── stage5_render.py     # WeasyPrint PDF rendering
│
├── models/                  # Pydantic data models (stage I/O contracts)
│   ├── manifest.py          # ProjectManifest     (Stage 1 output)
│   ├── enriched_photos.py   # EnrichedPhotoSet    (Stage 3a output)
│   ├── content_plan.py      # ContentPlan         (Stage 3b output)
│   ├── page_plan.py         # PagePlan            (Stage 4 output)
│   └── design.py            # DesignSystem        (loaded from design.yaml)
│
├── utils/
│   └── agenda_parser.py     # .docx agenda → AgendaSessions (LLM-assisted)
│
├── ai/                      # OpenAI client wrappers
│
├── templates/
│   └── report.html.j2       # Single Jinja2 template for all page types
│
├── tests/                   # pytest test suite
│
└── data/                    # gitignored — workshop data and outputs
    ├── agenda/
    ├── fotos/
    ├── text/
    ├── assets/
    ├── template/
    │   └── design.yaml
    ├── .cache/              # Intermediate stage outputs (JSON)
    └── output/              # Final PDF(s)
```

---

## Tests

```bash
pytest
```

The test suite uses mocked AI calls and a sample project fixture under `tests/fixtures/sample_project/`.

---

## Architecture

See [ARCHITECTURE.md](ARCHITECTURE.md) for a detailed description of the data flow, stage contracts, AI module design, and planned features.
