from pathlib import Path

import pytest

from settings import Settings

FIXTURES_DIR = Path(__file__).parent / "fixtures"
SAMPLE_PROJECT_DIR = FIXTURES_DIR / "sample_project"


@pytest.fixture
def sample_project_dir() -> Path:
    """Path to the minimal sample project used across all stage tests."""
    return SAMPLE_PROJECT_DIR


@pytest.fixture
def settings(sample_project_dir: Path) -> Settings:
    """Settings instance pointing at the sample project. No real API key needed for unit tests."""
    return Settings(
        openai_api_key="test-key-not-used-in-unit-tests",
        project_dir=sample_project_dir,
    )


@pytest.fixture
def tmp_settings(tmp_path: Path) -> Settings:
    """Settings instance pointing at a fresh temp directory for tests that write output.

    Directory layout mirrors the real project:
        data/agenda/    per-workshop agenda
        data/fotos/     per-workshop photos
        data/text/      per-workshop notes
        data/template/  reusable design system (design.yaml, reference.pdf)
        data/assets/    stable brand assets (logos, images)
    """
    for subdir in ("fotos", "agenda", "text", "template", "assets"):
        (tmp_path / subdir).mkdir()
    return Settings(
        openai_api_key="test-key-not-used-in-unit-tests",
        project_dir=tmp_path,
    )
