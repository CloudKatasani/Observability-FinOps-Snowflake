"""White-label branding loaded from configuration, never hardcoded.

The display name and palette live in ``config/branding.yaml`` so a client
deployment can rebrand without a code change (BUILD_PROMPT header note, §16.3).
"""

from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import BaseModel, Field, ValidationError

from snowobs_common.errors import ConfigurationError


class BrandingPalette(BaseModel):
    navy: str = "#12446E"
    primary: str = "#0070AD"
    sky: str = "#12ABDB"
    coral: str = "#E94B89"


class Branding(BaseModel):
    """Client-facing identity. Defaults are the shipped neutral branding."""

    display_name: str = "Observability & FinOps Platform for Snowflake"
    short_name: str = "snowobs"
    palette: BrandingPalette = Field(default_factory=BrandingPalette)


def load_branding(path: str | Path) -> Branding:
    """Read branding YAML; a missing file yields the shipped defaults."""
    file = Path(path)
    if not file.exists():
        return Branding()
    try:
        raw = yaml.safe_load(file.read_text(encoding="utf-8")) or {}
        return Branding.model_validate(raw)
    except (yaml.YAMLError, ValidationError) as exc:
        raise ConfigurationError(f"Invalid branding file {file}: {exc}") from exc
