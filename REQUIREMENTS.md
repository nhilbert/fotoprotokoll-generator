# Fotoprotokoll Generator – Requirements Document

**Version:** 0.1
**Date:** 2026-02-11
**Status:** Draft

---

## 1. Overview

The **Fotoprotokoll Generator** is a CLI tool that turns raw workshop materials (photos, agenda, text, design template) into a professional, ready-to-print PDF documentation. It uses AI-powered analysis to understand photo content, match images to written text, and distribute content across pages following a design template.

---

## 2. Goals

- Automate the tedious manual work of assembling workshop photo protocols.
- Produce a polished, consistently designed PDF with minimal human intervention.
- Allow interactive clarification steps where automated matching is ambiguous.
- Build on a solid foundation that can later be extended with a UI.

---

## 3. Input Data Structure

All inputs live under a single **project directory** (default: `data/`).

```
data/
├── agenda/             # One file: .docx or .pdf with the workshop agenda
├── fotos/              # All workshop photos (.jpg, .png, .heic, …)
├── template/           # Setup folder (used once; defines design)
│   ├── template.pdf    # Reference PDF showing target layout and design
│   ├── assets/         # Logos, decorative images, fonts, color swatches
│   └── text/           # Pre-written text snippets / section descriptions
```

### 3.1 Agenda
- Provides: workshop title, date, location, participants (if present), session/topic structure.
- Used for: document metadata, ordering of content, section headings.
- Format: `.docx` or `.pdf`.

### 3.2 Photos
- Any quantity; filenames may be arbitrary.
- Content types expected: flipchart photos, group photos, working sessions, results.
- May include exif metadata (timestamp, device) useful for ordering.

### 3.3 Template
- A reference PDF that communicates: page layout, fonts, colors, logo placement, heading style, caption style, footer/header.
- **Read-only reference** – never modified by the tool.
- Assets folder: logos, background images, decorative elements referenced in the template.
- Text folder: reusable text blocks (e.g., introductory paragraph, closing remarks, standard section headings).

---

## 4. Processing Workflow

The tool runs as a sequential pipeline with well-defined stages. Each stage produces an artifact that feeds the next.

### Stage 1 – Project Ingestion & Analysis
**Goal:** Understand all input materials.

- Parse the agenda document → extract: title, date, participant count, session list (name, order, duration hints).
- Analyze the template PDF → extract: page dimensions, margin layout, color palette, font names/sizes, logo positions, grid/columns.
- Inventory all photos → record: filename, timestamp (exif or file mtime), resolution, orientation.
- Read text snippets from `template/text/`.
- Output: a structured **project manifest** (JSON/YAML) summarizing all findings.

### Stage 2 – Photo Processing
**Goal:** Prepare photos for use in the document.

- **Flipchart detection:** Identify photos that contain a flipchart/whiteboard (rectangular region with handwriting).
- **Perspective correction & cropping:** For flipchart photos, detect the board boundary and apply a perspective transform to produce a clean, rectangular crop.
- **Quality assessment:** Flag blurry or dark photos; score each photo by usability.
- **Deduplication:** Detect near-identical shots; keep the highest-quality one (optionally confirm with user).
- Output: processed photo set in a working directory (`data/.cache/processed/`).

### Stage 3 – Content Understanding & Matching
**Goal:** Assign meaning to each photo and link it to text and agenda items.

- **Photo content analysis (AI/Vision):** For each photo, generate a content description (what is shown, any readable text on flipcharts).
- **Text analysis:** Parse text snippets; extract topic keywords and semantic intent.
- **Agenda-driven ordering:** Use the session list as the primary ordering skeleton.
- **Matching algorithm:**
  1. Match photos to agenda sessions based on timestamp proximity and content similarity.
  2. Match photos to text snippets based on semantic similarity (keywords, OCR text on flipcharts vs. written text).
  3. Produce a confidence score per match.
- **Interactive clarification step:** For matches below a confidence threshold, pause and ask the user to confirm or reassign (CLI prompt with suggested options).
- Output: **content plan** (ordered list of `{session, heading, photo(s), text_snippet, page_hint}`).

### Stage 4 – Page Layout Planning
**Goal:** Distribute content across pages following the template design.

- Map the content plan onto pages respecting:
  - Target: **1–2 photos per page**.
  - Each page: optional heading, 1–2 photos with captions, optional body text.
  - Cover page: workshop title, date, logo (from assets).
  - Section divider pages (optional): one per major agenda session.
  - Closing page: standard closing text (from `template/text/`).
- Respect template layout: margins, column widths, header/footer zones.
- Handle portrait vs. landscape photos differently (landscape may take full width; portrait may pair side-by-side).
- Output: **page plan** (structured layout spec per page).

### Stage 5 – PDF Generation
**Goal:** Render the final document.

- Render each page according to the page plan using the design system extracted from the template (colors, fonts, logo, assets).
- Apply consistent typography: headings, captions, body text.
- Embed photos at print resolution.
- Add page numbers and header/footer if present in template.
- Output: `output/<project-title>_<date>.pdf`.

---

## 5. Interactive Clarification Step

The tool MAY pause at the end of Stage 3 and present the user with:

- A summary of the content plan (sessions, matched photos, matched texts).
- A list of low-confidence matches with suggested alternatives.
- Yes/No/Manual prompts to confirm, swap, or skip items.

The user can also force a full review by passing `--review` flag.

---

## 6. Outputs

| Artifact | Location | Description |
|---|---|---|
| Project manifest | `data/.cache/manifest.json` | Parsed input summary |
| Processed photos | `data/.cache/processed/` | Cropped/corrected images |
| Content plan | `data/.cache/content_plan.json` | Ordered content with match scores |
| Page plan | `data/.cache/page_plan.json` | Per-page layout specification |
| **Final PDF** | `output/<title>_<date>.pdf` | The deliverable |

---

## 7. CLI Interface

```
python main.py [OPTIONS] [PROJECT_DIR]
```

| Option | Description |
|---|---|
| `PROJECT_DIR` | Path to project directory (default: `./data`) |
| `--review` | Force interactive review of content plan before rendering |
| `--skip-crop` | Skip flipchart detection and cropping |
| `--stage STAGE` | Run only up to a specific stage (1–5) |
| `--resume` | Resume from last completed stage (uses cached artifacts) |
| `--output FILE` | Override output PDF path |
| `--verbose` | Detailed logging |

---

## 8. Technology Stack (Proposed)

| Concern | Library / Tool |
|---|---|
| Document parsing (docx/pdf) | `python-docx`, `pypdf` / `pdfplumber` |
| Image processing & cropping | `OpenCV`, `Pillow` |
| AI vision / content analysis | OpenAI GPT-4o Vision or Anthropic Claude (via API) |
| Text similarity / matching | `sentence-transformers` or LLM embeddings |
| PDF generation | `reportlab` or `WeasyPrint` |
| Config & manifest | `pyyaml` / `json` |
| CLI | `typer` or `argparse` |

---

## 9. Non-Functional Requirements

- **Reproducibility:** Re-running with `--resume` must produce identical output if inputs have not changed.
- **Offline-first option:** Core pipeline must work without AI API calls if `--skip-ai` is passed (fallback: timestamp-based ordering only).
- **Extensibility:** Stages are independently testable modules; a future UI can call them via a Python API.
- **Data privacy:** Photos and text are never sent to external services unless the user explicitly configures an API key.
- **Language:** German and English content must both be handled correctly.

---

## 10. Out of Scope (v1)

- Graphical user interface.
- Cloud storage integration.
- Real-time collaboration.
- Video or audio documentation.
- Automatic participant recognition (face detection/labeling).

---

## 11. Open Questions

1. Should the cover page design be fully auto-generated from the template, or should a separate cover template be provided?
The cover page can be auto-generated from the template.
2. Is the number of expected photos per workshop typically known in advance, or highly variable?
its not known in advance, it is usually 15-30 images.
3. Should section divider pages be optional or always generated?
its optional.
4. Are text snippets in `template/text/` reused across workshops (generic), or are they workshop-specific?
the text is workshop-specific, it is usually written notes or possibly already extracted text from workshop images. in this case it should go on the right pages with the matching photos.
5. Which AI provider/API key should be used by default – Anthropic or OpenAI?
OpenAI
6. Should the tool support multiple language outputs for the same content?
No, we will work in German for now

---

## 12. Success Criteria

- Given a typical workshop with 20–50 photos, the tool produces a complete PDF requiring fewer than 5 manual corrections.
- Flipchart photos are cropped correctly in ≥ 90% of cases.
- Photos are matched to correct agenda sessions in ≥ 85% of cases (by timestamp + content).
- Total processing time (excluding API calls) < 2 minutes on a modern laptop.
