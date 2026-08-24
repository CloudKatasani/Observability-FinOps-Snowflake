"""Secrets adapter (BUILD_PROMPT §17, §21).

Secret *material* never lives in :class:`~snowobs_common.config.Settings`, in
Postgres, in Terraform state, or in a log line. What is configured and stored
is a **reference** — ``env://SLACK_WEBHOOK``, ``file://alerts/webhook``,
``aws://snowobs/alerts/webhook`` — and this module is the only thing that turns
a reference into a value, at the moment of use.

Three providers, selected by ``SECRETS__PROVIDER``:

``env``
    Reads a process environment variable. The mapping is injected rather than
    read ambiently, so the one ``os.environ`` reference outside the settings
    module is here and is auditable (A-26).
``file``
    Reads a JSON object from a local file, for laptops and air-gapped installs.
    The file is the deployment's own secret store; the app only reads it.
``aws``
    Reads AWS Secrets Manager through an injected client. ``boto3`` is an
    optional extra, so a deployment that never configures ``aws`` never needs
    it installed.

Every resolver raises :class:`SecretNotFoundError` with the *reference* — never
the value — when a lookup fails, so a misconfiguration is diagnosable without
leaking anything into a log.
"""

from __future__ import annotations

import json
import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from snowobs_common.config import Settings
from snowobs_common.errors import AppError

#: Scheme separator in a secret reference.
SCHEME_SEPARATOR = "://"


class SecretNotFoundError(AppError):
    """A configured secret reference does not resolve to a value."""

    status_code = 500
    title = "Secret could not be resolved"
    problem_type = "https://snowobs.dev/problems/secret-not-found"


@runtime_checkable
class SecretResolver(Protocol):
    """Resolves a stored secret reference to its value (§17)."""

    def resolve(self, reference: str) -> str: ...


def split_reference(reference: str) -> tuple[str | None, str]:
    """``("env", "SLACK_WEBHOOK")`` for ``env://SLACK_WEBHOOK``.

    A reference with no scheme is returned as ``(None, reference)`` so a
    provider can accept its own bare keys without every caller having to know
    which provider is configured.
    """
    if SCHEME_SEPARATOR in reference:
        scheme, _, key = reference.partition(SCHEME_SEPARATOR)
        return scheme.lower(), key
    return None, reference


def _reject_foreign_scheme(reference: str, accepted: str) -> str:
    scheme, key = split_reference(reference)
    if scheme is not None and scheme != accepted:
        raise SecretNotFoundError(
            f"Secret reference {reference!r} names the '{scheme}' provider but the "
            f"configured secrets adapter is '{accepted}'. Either change "
            f"SECRETS__PROVIDER or rewrite the reference."
        )
    if not key:
        raise SecretNotFoundError(f"Secret reference {reference!r} names no key")
    return key


@dataclass(frozen=True)
class EnvSecretResolver:
    """Resolves ``env://NAME`` (or a bare ``NAME``) from a process environment.

    The environment is a constructor argument, not an ambient read: it keeps
    §21's "never read ``os.environ`` outside the settings module" honest to its
    intent (configuration is typed and validated in one place) while letting
    secret material stay out of :class:`Settings` entirely.
    """

    environ: Mapping[str, str] = field(default_factory=lambda: os.environ)

    def resolve(self, reference: str) -> str:
        name = _reject_foreign_scheme(reference, "env")
        value = self.environ.get(name)
        if value is None or value == "":
            raise SecretNotFoundError(
                f"Secret reference {reference!r} is not set in this process environment"
            )
        return value


@dataclass(frozen=True)
class FileSecretResolver:
    """Resolves ``file://key`` from a JSON object on disk."""

    path: Path

    def resolve(self, reference: str) -> str:
        key = _reject_foreign_scheme(reference, "file")
        if not self.path.is_file():
            raise SecretNotFoundError(
                f"Secret reference {reference!r} cannot be resolved: the secrets file "
                f"{self.path} does not exist"
            )
        try:
            document = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise SecretNotFoundError(
                f"Secrets file {self.path} could not be read as a JSON object"
            ) from exc
        if not isinstance(document, dict):
            raise SecretNotFoundError(f"Secrets file {self.path} is not a JSON object")
        value = document.get(key)
        if not isinstance(value, str) or not value:
            raise SecretNotFoundError(f"Secret reference {reference!r} is absent from {self.path}")
        return value


@runtime_checkable
class SecretsManagerClient(Protocol):
    """The one Secrets Manager call this adapter makes."""

    def get_secret_value(self, *, SecretId: str) -> Mapping[str, Any]: ...  # noqa: N803


@dataclass(frozen=True)
class AwsSecretResolver:
    """Resolves ``aws://name`` from AWS Secrets Manager.

    The client is injected so the adapter is testable and so ``boto3`` stays an
    optional extra (:func:`build_resolver` constructs a real one on demand).
    """

    client: SecretsManagerClient

    def resolve(self, reference: str) -> str:
        secret_id = _reject_foreign_scheme(reference, "aws")
        try:
            response = self.client.get_secret_value(SecretId=secret_id)
        except Exception as exc:  # botocore raises a client-specific exception tree
            raise SecretNotFoundError(
                f"Secret reference {reference!r} could not be read from AWS Secrets Manager"
            ) from exc
        value = response.get("SecretString")
        if not isinstance(value, str) or not value:
            raise SecretNotFoundError(
                f"Secret reference {reference!r} resolved to no SecretString. Binary "
                "secrets are not supported; store the value as a string."
            )
        return value


@dataclass(frozen=True)
class NullSecretResolver:
    """Resolves nothing, and says so.

    Used when a deployment configures no secrets adapter at all. It exists so
    the failure is an explicit, readable error at the point of use rather than
    an ``AttributeError`` on ``None``.
    """

    def resolve(self, reference: str) -> str:
        raise SecretNotFoundError(
            f"No secrets adapter is configured, so {reference!r} cannot be resolved. "
            "Set SECRETS__PROVIDER (env, file, or aws)."
        )


def build_resolver(
    settings: Settings,
    *,
    environ: Mapping[str, str] | None = None,
    client: SecretsManagerClient | None = None,
) -> SecretResolver:
    """The resolver this deployment's configuration asks for."""
    provider = settings.secrets.provider
    if provider == "env":
        return EnvSecretResolver(environ if environ is not None else os.environ)
    if provider == "file":
        return FileSecretResolver(Path(settings.secrets.file_path))
    if client is not None:
        return AwsSecretResolver(client)
    try:
        import boto3
    except ImportError as exc:  # pragma: no cover - exercised only without the extra
        raise SecretNotFoundError(
            "SECRETS__PROVIDER=aws requires boto3. Install the 'aws' extra, or set "
            "SECRETS__PROVIDER to env or file."
        ) from exc
    return AwsSecretResolver(boto3.client("secretsmanager", region_name=settings.storage.region))


__all__ = [
    "AwsSecretResolver",
    "EnvSecretResolver",
    "FileSecretResolver",
    "NullSecretResolver",
    "SecretNotFoundError",
    "SecretResolver",
    "SecretsManagerClient",
    "build_resolver",
    "split_reference",
]
