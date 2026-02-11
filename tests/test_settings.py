from pathlib import Path

import pytest
from pydantic import ValidationError

from settings import Settings


def test_settings_loads_with_required_fields():
    s = Settings(openai_api_key="sk-test")
    assert s.openai_api_key == "sk-test"
    assert s.project_dir == Path("./data")
    assert s.language == "de"
    assert s.max_photos_per_page == 2
    assert s.vision_model == "gpt-5"
    assert s.embedding_model == "text-embedding-3-small"


def test_settings_derived_paths():
    s = Settings(openai_api_key="sk-test", project_dir=Path("/tmp/project"))
    assert s.agenda_dir == Path("/tmp/project/agenda")
    assert s.fotos_dir == Path("/tmp/project/fotos")
    assert s.cache_dir == Path("/tmp/project/.cache")
    assert s.analyses_dir == Path("/tmp/project/.cache/analyses")
    assert s.output_dir == Path("/tmp/project/output")
    assert s.design_yaml_path == Path("/tmp/project/template/design.yaml")


def test_settings_missing_api_key_raises():
    with pytest.raises(ValidationError) as exc_info:
        Settings()
    assert "openai_api_key" in str(exc_info.value)


def test_confidence_threshold_must_be_fraction():
    with pytest.raises(ValidationError):
        Settings(openai_api_key="sk-test", match_confidence_threshold=1.5)
    with pytest.raises(ValidationError):
        Settings(openai_api_key="sk-test", match_confidence_threshold=-0.1)


def test_max_photos_per_page_must_be_positive():
    with pytest.raises(ValidationError):
        Settings(openai_api_key="sk-test", max_photos_per_page=0)


def test_settings_custom_project_dir():
    s = Settings(openai_api_key="sk-test", project_dir=Path("/custom/path"))
    assert s.project_dir == Path("/custom/path")
    assert s.fotos_dir == Path("/custom/path/fotos")


def test_settings_env_prefix(monkeypatch):
    monkeypatch.setenv("FPG_OPENAI_API_KEY", "sk-from-env")
    monkeypatch.setenv("FPG_MAX_PHOTOS_PER_PAGE", "1")
    s = Settings()
    assert s.openai_api_key == "sk-from-env"
    assert s.max_photos_per_page == 1
