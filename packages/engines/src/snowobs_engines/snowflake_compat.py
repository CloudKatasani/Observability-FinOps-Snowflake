"""Snowflake-semantics function shims for DuckDB, used by the parity harness.

The parity suite executes the **actual Snowflake-dialect SQL** the platform
would send, rather than a re-derivation of it. To do that without a Snowflake
account, the handful of Snowflake-specific functions the compiler emits are
registered as DuckDB macros implementing the same semantics.

This is a *test* facility. It is never used to serve a query: the DuckDB engine
executes DuckDB-dialect SQL compiled from the same semantic definitions. Its
purpose is to make "both engines agree" a claim CI can check on every commit,
with the live comparison against a real account running nightly (§22.2.5).

Each macro is a documented equivalence, and where the equivalence is inexact
(percentiles) the difference is declared in ``PARITY_EXCEPTIONS`` and
``docs/PARITY_EXCEPTIONS.md`` rather than hidden here.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover - typing only
    import duckdb

#: name → (arguments, body). Kept small on purpose: every entry is a construct
#: the compiler genuinely emits for Snowflake.
SNOWFLAKE_MACROS: dict[str, tuple[tuple[str, ...], str]] = {
    # TRY_TO_TIMESTAMP_NTZ parses text to a timestamp, yielding NULL rather than
    # raising on unparseable input — DuckDB's TRY_CAST has the same contract.
    "TRY_TO_TIMESTAMP_NTZ": (("value",), "TRY_CAST(value AS TIMESTAMP)"),
    "TRY_TO_TIMESTAMP": (("value",), "TRY_CAST(value AS TIMESTAMP)"),
    "TRY_TO_DATE": (("value",), "TRY_CAST(value AS DATE)"),
    "TRY_TO_NUMBER": (("value",), "TRY_CAST(value AS DECIMAL(38, 9))"),
    # DIV0 / DIV0NULL: Snowflake's zero-safe division helpers.
    "DIV0": (("n", "d"), "CASE WHEN d = 0 THEN 0 ELSE n / d END"),
    "DIV0NULL": (("n", "d"), "CASE WHEN d = 0 OR d IS NULL THEN 0 ELSE n / d END"),
    # ZEROIFNULL / NULLIFZERO.
    "ZEROIFNULL": (("value",), "COALESCE(value, 0)"),
    "NULLIFZERO": (("value",), "CASE WHEN value = 0 THEN NULL ELSE value END"),
    # IFF is Snowflake's ternary.
    "IFF": (("condition", "a", "b"), "CASE WHEN condition THEN a ELSE b END"),
}

#: Aggregate macros must be registered separately (DuckDB macro bodies cannot
#: contain aggregates when declared as scalar macros).
SNOWFLAKE_AGGREGATE_MACROS: dict[str, tuple[tuple[str, ...], str]] = {
    # APPROX_PERCENTILE(value, fraction) — Snowflake's argument order is
    # (column, fraction), the reverse of DuckDB's quantile_cont. The estimate is
    # approximate on Snowflake and exact here; that difference is the documented
    # parity exception for the percentile metrics, not something to paper over.
    "APPROX_PERCENTILE": (("value", "fraction"), "quantile_cont(value, fraction)"),
    "MEDIAN": (("value",), "quantile_cont(value, 0.5)"),
}


def install(connection: duckdb.DuckDBPyConnection) -> list[str]:
    """Register the Snowflake-compatibility macros. Returns the names installed."""
    installed: list[str] = []
    for name, (arguments, body) in SNOWFLAKE_MACROS.items():
        signature = ", ".join(arguments)
        connection.execute(f"CREATE OR REPLACE MACRO {name}({signature}) AS {body}")
        installed.append(name)
    for name, (arguments, body) in SNOWFLAKE_AGGREGATE_MACROS.items():
        signature = ", ".join(arguments)
        connection.execute(f"CREATE OR REPLACE MACRO {name}({signature}) AS {body}")
        installed.append(name)
    return installed


def uninstall(connection: duckdb.DuckDBPyConnection) -> None:
    """Remove the macros — used to prove the engine does not depend on them."""
    for name in [*SNOWFLAKE_MACROS, *SNOWFLAKE_AGGREGATE_MACROS]:
        connection.execute(f"DROP MACRO IF EXISTS {name}")
