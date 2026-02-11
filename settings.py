from pathlib import Path

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    openai_api_key: str

    project_dir: Path = Path("./data")
    match_confidence_threshold: float = 0.65
    temporal_weight: float = 0.6
    semantic_weight: float = 0.4
    max_photos_per_page: int = 2
    language: str = "de"
    section_dividers: bool = False
    log_level: str = "INFO"

    model_config = SettingsConfigDict(
        env_file=".env",
        env_prefix="FPG_",
        env_file_encoding="utf-8",
    )

    @field_validator("match_confidence_threshold")
    @classmethod
    def confidence_must_be_fraction(cls, v: float) -> float:
        if not 0.0 <= v <= 1.0:
            raise ValueError("match_confidence_threshold must be between 0.0 and 1.0")
        return v

    @field_validator("max_photos_per_page")
    @classmethod
    def photos_per_page_must_be_positive(cls, v: int) -> int:
        if v < 1:
            raise ValueError("max_photos_per_page must be at least 1")
        return v

    @property
    def agenda_dir(self) -> Path:
        return self.project_dir / "agenda"

    @property
    def fotos_dir(self) -> Path:
        return self.project_dir / "fotos"

    @property
    def template_dir(self) -> Path:
        return self.project_dir / "template"

    @property
    def assets_dir(self) -> Path:
        return self.project_dir / "assets"

    @property
    def text_dir(self) -> Path:
        return self.project_dir / "text"

    @property
    def cache_dir(self) -> Path:
        return self.project_dir / ".cache"

    @property
    def analyses_dir(self) -> Path:
        return self.cache_dir / "analyses"

    @property
    def processed_dir(self) -> Path:
        return self.cache_dir / "processed"

    @property
    def output_dir(self) -> Path:
        return self.project_dir / "output"

    @property
    def design_yaml_path(self) -> Path:
        return self.template_dir / "design.yaml"
