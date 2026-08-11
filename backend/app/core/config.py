"""Application configuration.

Every setting is validated at import time. The process fails fast on a missing
or malformed variable rather than surfacing it later as a confusing runtime
error in a request handler.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Annotated, Literal

from pydantic import Field, PostgresDsn, RedisDsn, SecretStr, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

Environment = Literal["local", "ci", "production"]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # -- environment -----------------------------------------------------
    environment: Environment = "local"
    debug: bool = False
    app_name: str = "Frugal"
    api_v1_prefix: str = "/api/v1"

    # -- database --------------------------------------------------------
    database_url: PostgresDsn = Field(
        description="Application connection. On Neon this is the *pooled* endpoint."
    )
    database_direct_url: PostgresDsn | None = Field(
        default=None,
        description=(
            "Direct (non-pooled) endpoint used by Alembic. DDL through a "
            "connection pooler is unreliable. Falls back to database_url locally."
        ),
    )
    # 10 + 10. A single advisor request holds **three** connections at its peak:
    # its own session, plus one each for the health and forecast engines it
    # gathers concurrently. At the previous 5 + 5 that was three concurrent
    # advisor users before the pool blocked, and SQLAlchemy's 30-second
    # `pool_timeout` then surfaced as a request that simply hung — which is how
    # this was found, as intermittent end-to-end timeouts that looked like load.
    #
    # Twenty is still conservative against Postgres's default 100 and Neon's
    # free-tier allowance, and leaves room for the worker's own pool.
    db_pool_size: int = Field(default=10, ge=1)
    db_max_overflow: int = Field(default=10, ge=0)
    db_echo: bool = False

    # -- redis -----------------------------------------------------------
    redis_url: RedisDsn

    # -- security --------------------------------------------------------
    jwt_secret: SecretStr
    jwt_algorithm: str = "HS256"
    access_token_ttl_seconds: int = Field(default=900, ge=60)
    refresh_token_ttl_days: int = Field(default=30, ge=1)
    # NoDecode: pydantic-settings JSON-decodes complex types from env before
    # validators run, so a plain comma-separated string would fail to parse.
    cors_origins: Annotated[list[str], NoDecode] = Field(
        default_factory=lambda: ["http://localhost:3000"]
    )
    frontend_url: str = "http://localhost:3000"

    # --- oauth (FR-1.5) --------------------------------------------------
    # Optional: the OAuth routes are registered only when a client id is set,
    # so a deployment without credentials has no OAuth endpoints rather than
    # endpoints that fail at runtime.
    google_client_id: str | None = None
    google_client_secret: SecretStr | None = None
    google_redirect_uri: str = "http://localhost:8000/api/v1/auth/oauth/google/callback"
    # Signs the short-lived session used to carry OAuth state; falls back to
    # the JWT secret when unset.
    session_secret: SecretStr | None = None

    # -- adapters (ADR-004) ----------------------------------------------
    storage_backend: Literal["s3", "minio", "memory"] = "minio"
    ocr_engine: Literal["tesseract", "fake"] = "fake"
    #: Which `PriceProvider` the advisor and the market module both use.
    #:
    #: They must agree. `seed_catalog` prices are static, `simulated_market`
    #: prices move — and running one of each meant the advisor quoted ₹89,900
    #: for a laptop the wishlist was tracking at ₹70,283 on the same day. The
    #: setting exists so there is one answer to "what does this cost".
    price_provider: Literal["seed_catalog", "simulated_market", "manual"] = "simulated_market"

    # -- object storage --------------------------------------------------
    s3_bucket: str = "frugal-receipts"
    s3_region: str = "ap-south-1"
    s3_endpoint_url: str | None = Field(
        default=None, description="Set for MinIO; leave unset for real S3."
    )
    s3_access_key: SecretStr | None = None
    s3_secret_key: SecretStr | None = None
    presigned_url_ttl_seconds: int = Field(default=300, ge=60)

    # -- rate limits (FR-1.7) ---------------------------------------------
    # Defaults are the production policy. They are configurable so local and
    # E2E environments can raise the registration ceiling -- an end-to-end
    # suite legitimately creates many accounts from one IP. The limiting
    # *behaviour* is pinned by tests that set their own limits explicitly, so
    # relaxing these here cannot silently disable the protection.
    login_attempts_per_ip: int = Field(default=10, ge=1)
    login_attempts_per_account: int = Field(default=5, ge=1)
    registrations_per_ip_per_hour: int = Field(default=5, ge=1)
    refreshes_per_hour: int = Field(default=60, ge=1)

    # -- engine thresholds -----------------------------------------------
    ocr_confidence_threshold: float = Field(default=0.75, ge=0, le=1)
    #: Calibrated, not guessed. `tests/eval/test_categorization_accuracy.py`
    #: sweeps this against hand-labelled unseen merchants; 0.60 was the original
    #: value and it accepted only 8% of predictions -- correct on all of them,
    #: and useless. 0.30 accepts 28% at 91% precision. The trade is favourable
    #: because a model suggestion lands unreviewed in the review queue: a wrong
    #: one costs a click to fix, a missing one costs picking from 23 categories.
    categorization_confidence_threshold: float = Field(default=0.30, ge=0, le=1)
    forecast_min_observation_days: int = Field(default=14, ge=1)
    forecast_ewma_min_days: int = Field(default=60, ge=1)
    forecast_prophet_min_days: int = Field(default=180, ge=1)

    # -- observability ---------------------------------------------------
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    log_format: Literal["json", "console"] = "json"

    @field_validator("jwt_secret")
    @classmethod
    def _secret_is_long_enough(cls, value: SecretStr) -> SecretStr:
        """HS256 keys shorter than the 256-bit hash output weaken the MAC
        (RFC 7518 §3.2). Enforced at boot so a weak secret cannot reach
        production quietly -- generate with `openssl rand -hex 32`.
        """
        if len(value.get_secret_value()) < 32:
            raise ValueError("JWT_SECRET must be at least 32 characters")
        return value

    @field_validator("cors_origins", mode="before")
    @classmethod
    def _split_origins(cls, value: object) -> object:
        """Accept a comma-separated string, since env vars cannot hold lists."""
        if isinstance(value, str):
            return [origin.strip() for origin in value.split(",") if origin.strip()]
        return value

    @property
    def is_production(self) -> bool:
        return self.environment == "production"

    @property
    def oauth_enabled(self) -> bool:
        return bool(self.google_client_id and self.google_client_secret)

    @property
    def session_signing_key(self) -> str:
        key = self.session_secret or self.jwt_secret
        return key.get_secret_value()

    @property
    def alembic_url(self) -> str:
        """Direct endpoint for DDL, falling back to the pooled one locally."""
        return str(self.database_direct_url or self.database_url)

    @property
    def sync_database_url(self) -> str:
        """psycopg URL for Celery workers (ADR-006)."""
        return str(self.database_url).replace("postgresql+asyncpg://", "postgresql+psycopg://")

    @property
    def async_database_url(self) -> str:
        """asyncpg URL for the API."""
        url = str(self.database_url)
        if "+asyncpg" not in url:
            url = url.replace("postgresql://", "postgresql+asyncpg://")
        return url


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Cached accessor. Import this rather than instantiating Settings directly."""
    return Settings()
