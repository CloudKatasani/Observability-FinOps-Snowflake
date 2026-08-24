"""Typed application settings (BUILD_PROMPT §21).

This module is the only place environment variables are read. Everything else
receives a validated :class:`Settings` instance. Sources, in precedence order:
environment variables, then an optional ``.env`` file, then defaults.

Naming follows §21 exactly: top-level operating switches carry the ``SNOWOBS_``
prefix (``SNOWOBS_MODE``, ``SNOWOBS_TENANCY``); infrastructure URLs are bare
(``DATABASE_URL``, ``REDIS_URL``); grouped settings use a double-underscore
nested path (``STORAGE__PROVIDER``, ``LLM__MODEL_STRONG`` …).
"""

from __future__ import annotations

from decimal import Decimal
from typing import Literal

from pydantic import AliasChoices, BaseModel, Field, ValidationError
from pydantic_settings import BaseSettings, SettingsConfigDict

from snowobs_common.errors import ConfigurationError


class StorageSettings(BaseModel):
    """Object storage adapter configuration (uploads, parquet, exports, caches)."""

    provider: Literal["s3", "minio", "local"] = "minio"
    bucket: str = "snowobs"
    endpoint_url: str | None = None
    region: str | None = None


class SecretsSettings(BaseModel):
    """Secrets adapter. Only secret *references* are ever stored in the database."""

    provider: Literal["aws", "file", "env"] = "env"


class AuthSettings(BaseModel):
    """Authentication provider. ``local`` is a development fallback only."""

    provider: Literal["oidc", "local"] = "local"
    issuer: str | None = None
    client_id: str | None = None


class LLMSettings(BaseModel):
    """LLM adapter configuration (R11). ``none`` enables the deterministic path."""

    provider: Literal["anthropic", "bedrock", "cortex", "none"] = "none"
    model_strong: str | None = None
    model_fast: str | None = None
    daily_usd_cap: Decimal = Decimal("25")


class SnowflakeSettings(BaseModel):
    """LIVE-mode connection defaults. Secrets are referenced, never stored inline."""

    account: str | None = None
    user: str | None = None
    auth: Literal["keypair", "oauth", "pat", "externalbrowser"] = "keypair"
    private_key_ref: str | None = None
    role: str | None = None
    warehouse: str | None = None
    query_tag_prefix: str = "SNOWOBS"
    statement_timeout_s: int = Field(default=300, ge=1)


class FinOpsSettings(BaseModel):
    """Chargeback behaviour. Money and tolerances are Decimal — never float."""

    credit_price_usd: Decimal | None = None
    reconcile_tolerance_pct: Decimal = Decimal("0.5")
    mode: Literal["showback", "chargeback"] = "showback"
    close_business_day: int = Field(default=3, ge=1, le=20)


class GuardrailsSettings(BaseModel):
    """Hard limits applied to every engine query (R9)."""

    max_rows: int = Field(default=50_000, ge=1)
    allow_adhoc_sql: bool = False


class Settings(BaseSettings):
    """Root settings object, validated at startup."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        env_nested_delimiter="__",
        extra="ignore",
    )

    mode: Literal["live", "offline", "auto"] = Field(
        default="auto", validation_alias=AliasChoices("SNOWOBS_MODE")
    )
    tenancy: Literal["single", "multi"] = Field(
        default="single", validation_alias=AliasChoices("SNOWOBS_TENANCY")
    )
    log_json: bool = Field(
        default=True,
        validation_alias=AliasChoices("SNOWOBS_LOG_JSON"),
        description="JSON logs for production; set false for human-readable dev output.",
    )
    branding_file: str = Field(
        default="config/branding.yaml", validation_alias=AliasChoices("SNOWOBS_BRANDING_FILE")
    )

    database_url: str = Field(
        default="postgresql+asyncpg://snowobs:snowobs@localhost:5432/snowobs",
        validation_alias=AliasChoices("DATABASE_URL"),
    )
    redis_url: str = Field(
        default="redis://localhost:6379/0", validation_alias=AliasChoices("REDIS_URL")
    )

    storage: StorageSettings = StorageSettings()
    secrets: SecretsSettings = SecretsSettings()
    auth: AuthSettings = AuthSettings()
    llm: LLMSettings = LLMSettings()
    snowflake: SnowflakeSettings = SnowflakeSettings()
    finops: FinOpsSettings = FinOpsSettings()
    guardrails: GuardrailsSettings = GuardrailsSettings()


def load_settings() -> Settings:
    """Build settings from the environment, failing fast with a readable error."""
    try:
        return Settings()
    except ValidationError as exc:
        problems = "; ".join(
            f"{'.'.join(str(loc) for loc in err['loc'])}: {err['msg']}" for err in exc.errors()
        )
        raise ConfigurationError(f"Invalid configuration — {problems}") from exc
