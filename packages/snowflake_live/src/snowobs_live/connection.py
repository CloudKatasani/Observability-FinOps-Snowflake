"""Snowflake connection management (BUILD_PROMPT §7.2).

Key-pair is the default and the recommendation: Snowflake's MFA rollout
completes in October 2026, after which service users may only use key-pair,
OAuth, PAT, or WIF (verified — docs/ASSUMPTIONS.md §7). Password auth exists
but is marked discouraged and warns in the UI.

Secrets never touch Postgres in plaintext: the database stores a *reference*
that the secrets adapter resolves at connect time (§17).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import TYPE_CHECKING, Any, Protocol

from snowobs_common.errors import AppError
from snowobs_common.logging import get_logger

if TYPE_CHECKING:  # pragma: no cover - typing only
    from snowflake.connector import SnowflakeConnection

logger = get_logger(__name__)

DEFAULT_STATEMENT_TIMEOUT_S = 300
DEFAULT_LOGIN_TIMEOUT_S = 30
DEFAULT_NETWORK_TIMEOUT_S = 60


class AuthMethod(StrEnum):
    KEYPAIR = "keypair"
    OAUTH = "oauth"
    PAT = "pat"
    EXTERNALBROWSER = "externalbrowser"
    #: Exists for legacy accounts. The UI marks it discouraged and warns that
    #: Snowflake blocks single-factor password sign-in for service users from
    #: October 2026.
    PASSWORD = "password"  # noqa: S105 — an auth-method name, not a credential

    @property
    def discouraged(self) -> bool:
        return self is AuthMethod.PASSWORD

    @property
    def warning(self) -> str | None:
        if self is AuthMethod.PASSWORD:
            return (
                "Password authentication is discouraged. Snowflake's MFA rollout "
                "blocks single-factor password sign-in for service users from "
                "October 2026 — use key-pair, OAuth, or a programmatic access token."
            )
        if self is AuthMethod.EXTERNALBROWSER:
            return "Browser-based SSO is for local development only; it cannot run headless."
        return None


class SnowflakeConnectionError(AppError):
    status_code = 502
    title = "Snowflake connection failed"
    problem_type = "https://snowobs.dev/problems/snowflake-connection"


class SecretResolver(Protocol):
    """Resolves a stored secret reference to its value (§17)."""

    def resolve(self, reference: str) -> str: ...


@dataclass(frozen=True)
class ConnectionProfile:
    """Everything needed to open a session, with secrets held by reference."""

    account: str
    user: str
    auth: AuthMethod = AuthMethod.KEYPAIR
    #: A reference the secrets adapter resolves — never the key material itself.
    secret_ref: str | None = None
    role: str | None = None
    warehouse: str | None = None
    database: str | None = None
    schema: str | None = None
    host: str | None = None  # PrivateLink hostnames
    proxy: str | None = None
    query_tag_prefix: str = "SNOWOBS"
    statement_timeout_s: int = DEFAULT_STATEMENT_TIMEOUT_S
    session_parameters: dict[str, str | int] = field(default_factory=dict)

    def redacted(self) -> dict[str, Any]:
        """A log- and API-safe view. Secrets are never included, even by reference."""
        return {
            "account": self.account,
            "user": self.user,
            "auth": self.auth.value,
            "role": self.role,
            "warehouse": self.warehouse,
            "host": self.host,
            "has_secret_ref": self.secret_ref is not None,
        }


def query_tag(profile: ConnectionProfile, *, tenant: str, surface: str, trace_id: str) -> str:
    """The tag stamped on every session so the tool is attributable in the
    customer's own telemetry (§7.2) — and so its self-cost can be measured."""
    return f"{profile.query_tag_prefix}:{tenant}:{surface}:{trace_id}"


def build_connect_kwargs(
    profile: ConnectionProfile,
    resolver: SecretResolver | None = None,
    *,
    tenant: str = "default",
    surface: str = "app",
    trace_id: str = "-",
) -> dict[str, Any]:
    """Build the connector arguments. Never logs or returns secret material."""
    kwargs: dict[str, Any] = {
        "account": profile.account,
        "user": profile.user,
        "login_timeout": DEFAULT_LOGIN_TIMEOUT_S,
        "network_timeout": DEFAULT_NETWORK_TIMEOUT_S,
        "client_session_keep_alive": False,
        "application": "SnowObs_Observability_FinOps",
        "session_parameters": {
            "QUERY_TAG": query_tag(profile, tenant=tenant, surface=surface, trace_id=trace_id),
            "STATEMENT_TIMEOUT_IN_SECONDS": profile.statement_timeout_s,
            # Read-only sessions have nothing to commit.
            "AUTOCOMMIT": True,
            **profile.session_parameters,
        },
    }
    if profile.role:
        kwargs["role"] = profile.role
    if profile.warehouse:
        kwargs["warehouse"] = profile.warehouse
    if profile.database:
        kwargs["database"] = profile.database
    if profile.schema:
        kwargs["schema"] = profile.schema
    if profile.host:
        kwargs["host"] = profile.host  # PrivateLink
    if profile.proxy:
        kwargs["proxy_host"] = profile.proxy

    match profile.auth:
        case AuthMethod.KEYPAIR:
            kwargs["private_key"] = _load_private_key(profile, resolver)
        case AuthMethod.OAUTH:
            kwargs["authenticator"] = "oauth"
            kwargs["token"] = _resolve(profile, resolver)
        case AuthMethod.PAT:
            kwargs["authenticator"] = "programmatic_access_token"
            kwargs["password"] = _resolve(profile, resolver)
        case AuthMethod.EXTERNALBROWSER:
            kwargs["authenticator"] = "externalbrowser"
        case AuthMethod.PASSWORD:
            kwargs["password"] = _resolve(profile, resolver)
    return kwargs


def _resolve(profile: ConnectionProfile, resolver: SecretResolver | None) -> str:
    if profile.secret_ref is None:
        raise SnowflakeConnectionError(
            f"{profile.auth.value} authentication requires a configured secret reference"
        )
    if resolver is None:
        raise SnowflakeConnectionError("No secrets adapter configured to resolve the credential")
    return resolver.resolve(profile.secret_ref)


def _load_private_key(profile: ConnectionProfile, resolver: SecretResolver | None) -> bytes:
    """Load and normalise an RSA private key for key-pair authentication."""
    from cryptography.hazmat.primitives import serialization

    material = _resolve(profile, resolver)
    passphrase = None
    if "\n" not in material and material.count(":") == 1:
        # "reference:passphrase" form for an encrypted key.
        material, _, passphrase_text = material.partition(":")
        passphrase = passphrase_text.encode()

    try:
        key = serialization.load_pem_private_key(material.encode(), password=passphrase)
    except Exception as exc:
        raise SnowflakeConnectionError(
            "Private key could not be loaded. Check that the secret contains a "
            "PEM-encoded RSA key and, if encrypted, its passphrase."
        ) from exc

    return key.private_bytes(
        encoding=serialization.Encoding.DER,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )


class SnowflakeConnector:
    """Opens sessions for a profile. One instance per tenant connection."""

    def __init__(
        self,
        profile: ConnectionProfile,
        resolver: SecretResolver | None = None,
        *,
        tenant: str = "default",
    ) -> None:
        self.profile = profile
        self.resolver = resolver
        self.tenant = tenant

    def connect(self, *, surface: str = "app", trace_id: str = "-") -> SnowflakeConnection:
        """Open a session. The caller owns closing it."""
        import snowflake.connector

        kwargs = build_connect_kwargs(
            self.profile,
            self.resolver,
            tenant=self.tenant,
            surface=surface,
            trace_id=trace_id,
        )
        try:
            connection = snowflake.connector.connect(**kwargs)
        except Exception as exc:
            logger.warning(
                "snowflake_connect_failed",
                **self.profile.redacted(),
                error=type(exc).__name__,
            )
            raise SnowflakeConnectionError(_friendly_error(exc)) from exc

        logger.info("snowflake_connected", **self.profile.redacted(), surface=surface)
        return connection


def _friendly_error(exc: Exception) -> str:
    """Turn a driver error into something an operator can act on."""
    text = str(exc)
    lowered = text.lower()
    if "incorrect username or password" in lowered or "jwt token is invalid" in lowered:
        return (
            "Authentication failed. For key-pair auth, check that the public key "
            "registered on the Snowflake user matches this private key "
            "(ALTER USER <user> SET RSA_PUBLIC_KEY = '…')."
        )
    if "does not exist or not authorized" in lowered:
        return (
            "The account, role, or warehouse was not found, or this user is not "
            "authorised for it. Check the role has been granted to the user."
        )
    if "could not connect" in lowered or "timed out" in lowered:
        return (
            "Could not reach Snowflake. Check network egress, the account "
            "identifier, and any PrivateLink hostname or proxy configuration."
        )
    return f"Snowflake connection failed: {text}"
