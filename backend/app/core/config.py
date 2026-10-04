"""Application configuration.

Every setting comes from the environment. No literal anywhere else in the codebase
configures behaviour — that is the 12-factor rule, and the reason is simple: the same
image must run in dev, staging and production without being rebuilt.

CP2 expands this with database, Redis, storage and model settings.
"""

from enum import StrEnum
from functools import lru_cache

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Environment(StrEnum):
    LOCAL = "local"
    STAGING = "staging"
    PRODUCTION = "production"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        env_prefix="MEDREPORT_",
        extra="ignore",
        frozen=True,  # settings are read-only once loaded
    )

    app_name: str = "MedReport"
    environment: Environment = Environment.LOCAL
    debug: bool = False

    api_v1_prefix: str = "/api/v1"

    # Comma-separated in the env file: MEDREPORT_CORS_ORIGINS=http://localhost:3000
    cors_origins: list[str] = Field(default_factory=list)

    database_url: str = "postgresql+asyncpg://medreport:medreport@localhost:5433/medreport"
    database_echo: bool = False
    # Sizing note: pool_size + max_overflow, multiplied by every API container and
    # Celery worker, must stay below Postgres max_connections (100 by default).
    database_pool_size: int = 5
    database_max_overflow: int = 10

    google_client_id: str = ""
    jwt_secret: str = "dev-only-insecure-secret-change-me-32chars"  # noqa: S105
    jwt_ttl_minutes: int = 60 * 24 * 14  # two weeks; a health app is not a bank

    log_level: str = "INFO"
    # None means "decide from the environment": human-readable locally, JSON in
    # anything deployed. Explicit true/false overrides that, which is occasionally
    # useful when debugging a container.
    log_json: bool | None = None

    @model_validator(mode="after")
    def _refuse_dev_secrets_outside_local(self) -> "Settings":
        """Fail at startup rather than issuing forgeable tokens in production.

        A default secret in source is public. Anyone who reads the repository can
        mint a valid session token for any user - and in this product that is
        access to other people's medical records. Crashing on boot is the correct
        behaviour: a service that will not start gets fixed in minutes, a service
        signing tokens with a known key does not get noticed at all.
        """
        if self.environment is not Environment.LOCAL and "dev-only" in self.jwt_secret:
            raise ValueError(
                "MEDREPORT_JWT_SECRET must be set outside local. "
                'Generate one: python -c "import secrets;print(secrets.token_urlsafe(48))"'
            )
        return self

    @property
    def is_production(self) -> bool:
        return self.environment is Environment.PRODUCTION

    @property
    def use_json_logs(self) -> bool:
        if self.log_json is not None:
            return self.log_json
        return self.environment is not Environment.LOCAL


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Cached accessor.

    Settings are read once per process. The cache also makes this trivially
    overridable in tests via ``get_settings.cache_clear()``, and it is the seam
    FastAPI's dependency system overrides for test clients.
    """
    return Settings()
