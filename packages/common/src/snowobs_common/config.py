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

from pydantic import AliasChoices, BaseModel, Field, ValidationError, model_validator
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
    #: Where the ``file`` provider reads its JSON object from. A path, never a
    #: secret: the values behind it are resolved at the moment of use (§17).
    file_path: str = "/run/secrets/snowobs.json"


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
    #: A reference the secrets adapter resolves at the moment of use — never the
    #: key itself (§27.13). The `env` resolver reads it from the environment, so
    #: `LLM__API_KEY_REF=ANTHROPIC_API_KEY` is the usual local setup; on AWS it
    #: is a Secrets Manager reference. `bedrock` needs none of this: it
    #: authenticates with the task role.
    api_key_ref: str | None = None


class SnowflakeAccountSettings(BaseModel):
    """One Snowflake account the platform reads.

    An enterprise runs many accounts under one organization, and the platform
    needs a connection per account to see anything below the billing line:
    `ORGANIZATION_USAGE` rolls every account up but carries no queries, users,
    or tables, so query-level observability exists only where an account has
    been connected.
    """

    #: How this account is named in `ORGANIZATION_USAGE` — the join key between
    #: the org roll-up and this account's own detail. Getting it wrong shows up
    #: as an account that appears twice in the organization view, so it is
    #: matched rather than assumed.
    name: str
    account: str
    user: str
    auth: Literal["keypair", "oauth", "pat", "externalbrowser"] = "keypair"
    private_key_ref: str | None = None
    role: str | None = None
    warehouse: str | None = None
    host: str | None = None
    #: True for the one account whose connection reads `ORGANIZATION_USAGE`.
    #: Only an account with ORGADMIN-granted access can, so this is a property
    #: of one connection, not of all of them.
    organization_reader: bool = False
    enabled: bool = True


class SnowflakeSettings(BaseModel):
    """LIVE-mode connection defaults. Secrets are referenced, never stored inline."""

    #: The organization these accounts belong to, as `ORGANIZATION_USAGE` names
    #: it. Used to check that a roll-up is not silently mixing organizations.
    organization: str | None = None
    #: Every account the platform reads. When empty, the single-account fields
    #: below are used instead, so an existing single-account deployment keeps
    #: working untouched.
    accounts: list[SnowflakeAccountSettings] = Field(default_factory=list)

    account: str | None = None
    user: str | None = None
    auth: Literal["keypair", "oauth", "pat", "externalbrowser"] = "keypair"
    private_key_ref: str | None = None
    role: str | None = None
    warehouse: str | None = None
    query_tag_prefix: str = "SNOWOBS"
    statement_timeout_s: int = Field(default=300, ge=1)

    def configured_accounts(self) -> list[SnowflakeAccountSettings]:
        """Every account to read, however the deployment was configured.

        A single-account deployment sets the flat fields and never mentions
        `accounts`; an organization sets `accounts` and leaves the flat fields
        alone. Both arrive here as a list, so no caller has to know which style
        was used — and the single-account case stays exactly one account rather
        than becoming a special path through every consumer.
        """
        if self.accounts:
            return [account for account in self.accounts if account.enabled]
        if not (self.account and self.user):
            return []
        return [
            SnowflakeAccountSettings(
                # With nothing else to go on, the account identifier is also
                # its name. An organization deployment names them explicitly.
                name=self.account,
                account=self.account,
                user=self.user,
                auth=self.auth,
                private_key_ref=self.private_key_ref,
                role=self.role,
                warehouse=self.warehouse,
                # A lone account is the only candidate for reading the
                # organization views, and it may or may not be granted them —
                # the probe reports which, rather than this guessing.
                organization_reader=True,
            )
        ]

    def account_named(self, name: str) -> SnowflakeAccountSettings | None:
        return next((a for a in self.configured_accounts() if a.name == name), None)

    def organization_reader(self) -> SnowflakeAccountSettings | None:
        """The connection that reads `ORGANIZATION_USAGE`, if one is designated."""
        return next(
            (a for a in self.configured_accounts() if a.organization_reader),
            None,
        )

    @model_validator(mode="after")
    def _accounts_are_coherent(self) -> SnowflakeSettings:
        names = [account.name for account in self.accounts]
        duplicates = sorted({name for name in names if names.count(name) > 1})
        if duplicates:
            raise ValueError(
                f"snowflake.accounts has duplicate names: {duplicates}. Names are "
                "the join key to ORGANIZATION_USAGE, so a duplicate would double-count "
                "that account in every organization roll-up."
            )
        readers = [a.name for a in self.accounts if a.organization_reader and a.enabled]
        if len(readers) > 1:
            raise ValueError(
                f"More than one account is marked organization_reader: {readers}. "
                "ORGANIZATION_USAGE returns the whole organization from any account "
                "granted it, so reading it twice would double every org-level figure."
            )
        return self


class FinOpsSettings(BaseModel):
    """Chargeback behaviour. Money and tolerances are Decimal — never float."""

    credit_price_usd: Decimal | None = None
    reconcile_tolerance_pct: Decimal = Decimal("0.5")
    mode: Literal["showback", "chargeback"] = "showback"
    close_business_day: int = Field(default=3, ge=1, le=20)


class AlertingSettings(BaseModel):
    """Alert rule evaluation and notification dispatch (§14).

    ``enabled`` gates *outbound* traffic only. Rules are always loaded, listed,
    and backtestable — an operator can see and validate the rule set before any
    of it is allowed to page anybody, which is the order those two things
    should happen in.
    """

    enabled: bool = False
    #: Path to the declared rule set. ``None`` uses the shipped
    #: ``config/alert_rules.yaml``.
    rules_file: str | None = None
    #: Days of history fetched per rule, so a delta or anomaly condition has a
    #: baseline. The floor is the anomaly detector's minimum baseline (14 days)
    #: plus a fortnight of margin.
    lookback_days: int = Field(default=45, ge=28, le=400)
    #: How often the worker's scheduled evaluation runs.
    evaluation_interval_minutes: int = Field(default=60, ge=5, le=1440)
    #: Warehouse named by the OFFLINE ``CREATE ALERT`` DDL export (§14).
    ddl_warehouse: str = "WH_SNOWOBS_APP"


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
        # Without this a field carrying a validation alias can *only* be set by
        # that alias, so `Settings(mode="offline")` silently yields the default
        # instead — the constructor accepts the argument and discards it. Tests
        # and fixtures build settings by field name, and a silently ignored
        # mode is precisely the kind of wrong that still passes.
        populate_by_name=True,
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
    alerting: AlertingSettings = AlertingSettings()
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
