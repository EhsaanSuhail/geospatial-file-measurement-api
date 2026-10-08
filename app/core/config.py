from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

_MB = 1024 * 1024


class Settings(BaseSettings):
    """Application configuration, read from environment variables (and `.env` if present)."""

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    database_url: str = Field(
        description="SQLAlchemy URL, e.g. postgresql+psycopg://user:password@host:5432/dbname",
    )
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"

    max_upload_size_mb: int = Field(default=50, gt=0)
    max_uncompressed_size_mb: int = Field(default=250, gt=0)
    max_archive_members: int = Field(default=100, gt=0)
    max_feature_count: int = Field(default=50_000, gt=0)

    @property
    def max_upload_size_bytes(self) -> int:
        return self.max_upload_size_mb * _MB

    @property
    def max_uncompressed_size_bytes(self) -> int:
        return self.max_uncompressed_size_mb * _MB
