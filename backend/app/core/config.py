"""Application configuration.

Every setting comes from the environment. No literal anywhere else in the codebase
configures behaviour — that is the 12-factor rule, and the reason is simple: the same
image must run in dev, staging and production without being rebuilt.

CP2 expands this with database, Redis, storage and model settings.
"""

from enum import StrEnum
from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Environment(StrEnum):
    LOCAL = "local"
    STAGING = "staging"
    PRODUCTION = "production"

class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        env_prefix="BASELINE_",
        extra="ignore",
        frozen=True,  # settings are read-only once loaded
    )

    app_name: str = "Baseline"
    environment: Environment = Environment.LOCAL
    debug: bool = False

    api_v1_prefix: str = "/api/v1"

    # Comma-separated in the env file: BASELINE_CORS_ORIGINS=http://localhost:3000
    cors_origins: list[str] = Field(default_factory=list)

    @property
    def is_production(self) -> bool:
        return self.environment is Environment.PRODUCTION


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Cached accessor.

    Settings are read once per process. The cache also makes this trivially
    overridable in tests via ``get_settings.cache_clear()``, and it is the seam
    FastAPI's dependency system overrides for test clients.
    """
    return Settings()
