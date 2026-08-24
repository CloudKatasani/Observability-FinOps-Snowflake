"""Settings behaviour: defaults, env overrides, and readable failure."""

from decimal import Decimal

import pytest

from snowobs_common.config import Settings, load_settings
from snowobs_common.errors import ConfigurationError


def test_defaults_are_safe(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SNOWOBS_MODE", raising=False)
    settings = Settings(_env_file=None)
    assert settings.mode == "auto"
    assert settings.tenancy == "single"
    assert settings.llm.provider == "none"  # demo path needs no API key
    assert settings.guardrails.allow_adhoc_sql is False
    assert settings.guardrails.max_rows == 50_000
    assert settings.finops.reconcile_tolerance_pct == Decimal("0.5")
    assert isinstance(settings.finops.reconcile_tolerance_pct, Decimal)
    assert settings.snowflake.auth == "keypair"


def test_env_overrides_nested_and_prefixed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SNOWOBS_MODE", "offline")
    monkeypatch.setenv("SNOWOBS_TENANCY", "multi")
    monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://u:p@db:5432/x")
    monkeypatch.setenv("STORAGE__PROVIDER", "s3")
    monkeypatch.setenv("STORAGE__BUCKET", "acme-snowobs")
    monkeypatch.setenv("LLM__PROVIDER", "bedrock")
    monkeypatch.setenv("FINOPS__RECONCILE_TOLERANCE_PCT", "0.25")
    monkeypatch.setenv("GUARDRAILS__MAX_ROWS", "1000")

    settings = Settings(_env_file=None)
    assert settings.mode == "offline"
    assert settings.tenancy == "multi"
    assert settings.database_url.endswith("@db:5432/x")
    assert settings.storage.provider == "s3"
    assert settings.storage.bucket == "acme-snowobs"
    assert settings.llm.provider == "bedrock"
    assert settings.finops.reconcile_tolerance_pct == Decimal("0.25")
    assert settings.guardrails.max_rows == 1000


def test_invalid_value_fails_with_readable_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SNOWOBS_MODE", "hybrid")
    with pytest.raises(ConfigurationError) as excinfo:
        load_settings()
    message = str(excinfo.value)
    assert "SNOWOBS_MODE" in message  # names the offending variable
    assert "offline" in message  # and the accepted values


def test_credit_amounts_are_decimal_not_float(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FINOPS__CREDIT_PRICE_USD", "3.10")
    settings = Settings(_env_file=None)
    assert settings.finops.credit_price_usd == Decimal("3.10")
    assert not isinstance(settings.finops.credit_price_usd, float)
