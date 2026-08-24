"""Tenant identifiers, validated before they reach a filesystem path (§17).

Every tenant's data lives under ``{storage_root}/{tenant}/``, so the tenant id
is a path segment — and a path segment built from a caller-supplied string is
only as safe as the validation in front of it. ``acme/../globex`` joins to a
path that resolves into *another tenant's* prefix, and a catalog built on it
returns that tenant's figures without erroring: the wrong customer's spend,
rendered as though it were yours.

The rule is deliberately strict. A tenant identifier is a slug, and anything
else is refused rather than sanitised, because silently rewriting an identifier
would let two different inputs address the same tenant.
"""

from __future__ import annotations

import re
from pathlib import Path

from snowobs_common.errors import AppError

#: Lowercase alphanumerics, hyphen, and underscore. No dots (``..``), no
#: separators, no leading hyphen, and long enough not to collide by accident.
_TENANT_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_-]{0,62}$")

#: Names that mean something to a filesystem rather than to us.
_RESERVED = frozenset({"", ".", "..", "con", "prn", "aux", "nul"})


class TenancyError(AppError):
    """A tenant identifier that cannot safely be used as a path segment."""

    status_code = 400
    title = "Invalid tenant identifier"
    problem_type = "https://snowobs.dev/problems/tenant"


def validate_tenant(tenant: str) -> str:
    """Return the tenant id, or raise if it cannot be a safe path segment."""
    if tenant.lower() in _RESERVED:
        raise TenancyError(f"Reserved tenant identifier: {tenant!r}")
    if not _TENANT_PATTERN.match(tenant):
        raise TenancyError(
            f"Invalid tenant identifier {tenant!r}. A tenant id is 1–63 characters of "
            "lowercase letters, digits, hyphen, or underscore, starting with a letter "
            "or digit. Separators and '..' are refused because the id becomes a "
            "directory name."
        )
    return tenant


def tenant_root(storage_root: Path, tenant: str) -> Path:
    """The validated, resolved root for one tenant's data.

    Validation alone would be enough today; the containment check is here so
    that a future change to the pattern cannot reopen the hole quietly. A
    traversal that somehow passed the regex would still be caught by the
    resolved path leaving the storage root.
    """
    validated = validate_tenant(tenant)
    root = storage_root.resolve()
    candidate = (root / validated).resolve()
    if candidate != root and root not in candidate.parents:
        raise TenancyError(f"Tenant path for {tenant!r} resolves outside the storage root.")
    return storage_root / validated
