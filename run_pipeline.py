#!/usr/bin/env python3
"""Run the full Fotoprotokoll-Generator pipeline end-to-end.

Usage:
    python run_pipeline.py                  # run all stages
    python run_pipeline.py --from-stage 4   # start from stage 4 (load earlier caches)
    python run_pipeline.py --from-stage 5   # start from stage 5 (load earlier caches)
"""
import argparse
import json
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from settings import Settings
from models.manifest import ProjectManifest
from models.enriched_photos import EnrichedPhotoSet
from models.content_plan import ContentPlan
from models.page_plan import PagePlan
from pipeline import stage1_ingest, stage3a_enrich, stage3b_match, stage4_layout, stage5_render

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("run_pipeline")


def _load_json(path: Path, model):
    data = json.loads(path.read_text(encoding="utf-8"))
    return model.model_validate(data)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--from-stage", type=int, default=1, dest="from_stage",
                        help="Start from this stage number (1-5); earlier stages load from cache")
    args = parser.parse_args()

    settings = Settings()
    cache = settings.cache_dir  # data/.cache

    if args.from_stage <= 1:
        logger.info("=== Stage 1: Ingest ===")
        manifest = stage1_ingest.run(settings)
    else:
        logger.info("=== Stage 1: loading from cache ===")
        manifest = _load_json(cache / "manifest.json", ProjectManifest)

    if args.from_stage <= 3:
        logger.info("=== Stage 3a: Enrich photos ===")
        photo_set = stage3a_enrich.run(settings, manifest)

        logger.info("=== Stage 3b: Match content ===")
        content_plan = stage3b_match.run(settings, manifest, photo_set)
    else:
        logger.info("=== Stage 3a/3b: loading from cache ===")
        photo_set = _load_json(cache / "enriched_photos.json", EnrichedPhotoSet)
        content_plan = _load_json(cache / "content_plan.json", ContentPlan)

    if args.from_stage <= 4:
        logger.info("=== Stage 4: Layout ===")
        page_plan = stage4_layout.run(settings, manifest, content_plan, photo_set)
    else:
        logger.info("=== Stage 4: loading from cache ===")
        page_plan = _load_json(cache / "page_plan.json", PagePlan)

    logger.info("=== Stage 5: Render PDF ===")
    output_path = stage5_render.run(settings, page_plan, photo_set, manifest)

    logger.info("=== Done → %s ===", output_path)


if __name__ == "__main__":
    main()
