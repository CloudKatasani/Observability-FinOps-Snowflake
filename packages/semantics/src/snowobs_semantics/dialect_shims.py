"""Dialect shims (BUILD_PROMPT §8.4).

A shim translates **one construct** between engines. If you find yourself
writing engine-specific *business logic* here, stop — that is an R1 violation:
the metric definition is wrong, not the shim.

Metric and entity SQL is written in a small portable vocabulary of shim
functions (``SAFE_RATIO``, ``TS_TRUNC``, ``JSON_GET``, …). The compiler
rewrites each call into the target dialect. Every shim carries a parity test in
``packages/semantics/tests/test_parity.py``.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from typing import cast

import sqlglot
from sqlglot import exp

from snowobs_common.errors import AppError


class Dialect(StrEnum):
    SNOWFLAKE = "snowflake"
    DUCKDB = "duckdb"


class ShimError(AppError):
    status_code = 500
    title = "Dialect shim error"
    problem_type = "https://snowobs.dev/problems/shim"


#: Credits and currency are fixed-point everywhere (§27.7). DuckDB division
#: returns floating point, so every ratio is cast back to this scale.
MONEY_TYPE = "DECIMAL(38, 9)"
MONEY_PRECISION = 38
MONEY_SCALE = 9

#: Ratios carry more scale than money: a percentage of a large credit figure
#: needs headroom below the ninth place before rounding becomes visible.
RATIO_TYPE = "DECIMAL(38, 15)"
RATIO_SCALE = 15


@dataclass(frozen=True)
class Shim:
    """One portable construct and its rendering per dialect."""

    name: str
    arity: tuple[int, int]  # (min, max) argument count
    render: dict[Dialect, Callable[[list[str]], str]]
    description: str


def _safe_divide(args: list[str]) -> str:
    """n / d, yielding NULL (not 0) when d is 0 — an unknown ratio stays unknown (R3).

    The quotient is cast to fixed point on **both** engines. DuckDB's ``/``
    returns DOUBLE and needs the cast to avoid float money (§27.7); Snowflake's
    fixed-point division does not, but rendering the cast in both keeps the two
    engines bit-identical rather than merely close, which is the difference
    between a parity suite that passes and one that needs a tolerance.
    """
    numerator, denominator = args[0], args[1]
    quotient = (
        f"CASE WHEN ({denominator}) = 0 OR ({denominator}) IS NULL THEN NULL "
        f"ELSE CAST(({numerator}) AS {RATIO_TYPE}) / CAST(({denominator}) AS {RATIO_TYPE}) END"
    )
    return f"CAST({quotient} AS {RATIO_TYPE})"


def _ts_trunc(args: list[str]) -> str:
    unit, value = args[0], args[1]
    return f"DATE_TRUNC({unit}, {value})"


def _ts_parse_snowflake(args: list[str]) -> str:
    # Landed timestamps are ISO-8601 text (see ingest.mapper).
    return f"TRY_TO_TIMESTAMP_NTZ({args[0]})"


def _ts_parse_duckdb(args: list[str]) -> str:
    return f"TRY_CAST({args[0]} AS TIMESTAMP)"


def _json_get_snowflake(args: list[str]) -> str:
    column, path = args[0], args[1]
    # JSON_EXTRACT_PATH_TEXT returns unquoted VARCHAR. The `col:path::STRING`
    # form is equivalent in Snowflake but renders the value with its JSON
    # quotes intact under some paths, which would make 'TEAM_X' and '"TEAM_X"'
    # different keys between engines.
    return f"JSON_EXTRACT_PATH_TEXT({column}, {path})"


def _json_get_duckdb(args: list[str]) -> str:
    column, path = args[0], args[1]
    return f"json_extract_string({column}, '$.{path.strip(chr(39))}')"


def _percentile_snowflake(args: list[str]) -> str:
    fraction, value = args[0], args[1]
    return f"APPROX_PERCENTILE({value}, {fraction})"


def _percentile_duckdb(args: list[str]) -> str:
    fraction, value = args[0], args[1]
    # Exact rather than approximate — documented in docs/PARITY_EXCEPTIONS.md.
    return f"quantile_cont({value}, {fraction})"


def _money(args: list[str]) -> str:
    return f"CAST({args[0]} AS {MONEY_TYPE})"


def _regex_contains_snowflake(args: list[str]) -> str:
    return f"({args[0]} RLIKE {args[1]})"


def _regex_contains_duckdb(args: list[str]) -> str:
    return f"regexp_matches({args[0]}, {args[1]})"


def _date_diff_days(args: list[str]) -> str:
    return f"DATE_DIFF('day', {args[0]}, {args[1]})"


def _epoch_seconds_snowflake(args: list[str]) -> str:
    return f"DATEDIFF('second', {args[0]}, {args[1]})"


def _epoch_seconds_duckdb(args: list[str]) -> str:
    return f"DATE_DIFF('second', {args[0]}, {args[1]})"


SHIMS: dict[str, Shim] = {
    "SAFE_RATIO": Shim(
        name="SAFE_RATIO",
        arity=(2, 2),
        render={Dialect.SNOWFLAKE: _safe_divide, Dialect.DUCKDB: _safe_divide},
        description="Division yielding NULL on a zero or null denominator, fixed-point result.",
    ),
    "TS_TRUNC": Shim(
        name="TS_TRUNC",
        arity=(2, 2),
        render={Dialect.SNOWFLAKE: _ts_trunc, Dialect.DUCKDB: _ts_trunc},
        description="Truncate a timestamp to a unit.",
    ),
    "TS_PARSE": Shim(
        name="TS_PARSE",
        arity=(1, 1),
        render={Dialect.SNOWFLAKE: _ts_parse_snowflake, Dialect.DUCKDB: _ts_parse_duckdb},
        description="Parse landed ISO-8601 timestamp text, NULL when unparseable.",
    ),
    "JSON_GET": Shim(
        name="JSON_GET",
        arity=(2, 2),
        render={Dialect.SNOWFLAKE: _json_get_snowflake, Dialect.DUCKDB: _json_get_duckdb},
        description="Read a top-level string field from a JSON/VARIANT column.",
    ),
    "PERCENTILE": Shim(
        name="PERCENTILE",
        arity=(2, 2),
        render={Dialect.SNOWFLAKE: _percentile_snowflake, Dialect.DUCKDB: _percentile_duckdb},
        description="Percentile of a numeric column (approximate on Snowflake, exact on DuckDB).",
    ),
    "MONEY": Shim(
        name="MONEY",
        arity=(1, 1),
        render={Dialect.SNOWFLAKE: _money, Dialect.DUCKDB: _money},
        description="Cast to the fixed-point credit/currency type — never float.",
    ),
    "REGEX_CONTAINS": Shim(
        name="REGEX_CONTAINS",
        arity=(2, 2),
        render={
            Dialect.SNOWFLAKE: _regex_contains_snowflake,
            Dialect.DUCKDB: _regex_contains_duckdb,
        },
        description="Boolean regular-expression match.",
    ),
    "DATE_DIFF_DAYS": Shim(
        name="DATE_DIFF_DAYS",
        arity=(2, 2),
        render={Dialect.SNOWFLAKE: _date_diff_days, Dialect.DUCKDB: _date_diff_days},
        description="Whole days between two dates/timestamps.",
    ),
    "EPOCH_SECONDS": Shim(
        name="EPOCH_SECONDS",
        arity=(2, 2),
        render={
            Dialect.SNOWFLAKE: _epoch_seconds_snowflake,
            Dialect.DUCKDB: _epoch_seconds_duckdb,
        },
        description="Whole seconds between two timestamps.",
    ),
}


def _parse(sql: str, *, dialect: str = "duckdb") -> exp.Expression:
    """Parse to a plain :class:`~sqlglot.exp.Expression`.

    ``sqlglot.parse_one`` is annotated with a bound type variable, which mypy
    resolves to the unhelpfully narrow ``Expr``; this keeps the widening in one
    place rather than at every call site.
    """
    return cast(exp.Expression, sqlglot.parse_one(sql, read=dialect))


def assert_shim_names_are_unclaimed() -> None:
    """Fail loudly if SQLGlot recognises a shim name as one of its own functions.

    A name SQLGlot parses into a typed node (``SAFE_DIVIDE`` → ``exp.SafeDivide``)
    never reaches the ``Anonymous`` branch of the rewriter, so its shim is
    silently skipped and the engine's own rendering is used instead — which for
    a ratio means losing the fixed-point cast and returning a float. That is a
    §27.7 violation that produces no error, so it is checked explicitly.
    """
    claimed: list[str] = []
    for name in SHIMS:
        arguments = ", ".join("a" * (i + 1) for i in range(SHIMS[name].arity[0]))
        # A probe that will not parse cannot collide, so it is simply skipped.
        probe = _parse(f"SELECT {name}({arguments})")
        for node in probe.find_all(exp.Func):
            if not isinstance(node, exp.Anonymous):
                claimed.append(f"{name} -> sqlglot.exp.{type(node).__name__}")
    if claimed:
        raise ShimError(
            "Shim names collide with SQLGlot built-ins and would be silently "
            f"bypassed: {claimed}. Rename the shim."
        )


#: A shim's rendering can itself contain shim calls (``TS_TRUNC`` wrapping
#: ``TS_PARSE`` is the common case). Rewriting is therefore repeated to a fixed
#: point rather than in a single pass, which would leave the inner call intact
#: because the outer shim stringifies its arguments before they are rewritten.
_MAX_SHIM_PASSES = 8


def apply_shims(sql: str, dialect: Dialect) -> str:
    """Rewrite every shim call in a SQL fragment for the target dialect.

    Parsing is done with SQLGlot so a shim name inside a string literal or an
    identifier is never rewritten by accident.
    """
    try:
        expression = _parse(sql)
    except Exception as exc:
        raise ShimError(f"Could not parse expression: {sql!r} ({exc})") from exc

    def transform(node: exp.Expression) -> exp.Expression:
        if isinstance(node, exp.Anonymous):
            shim = SHIMS.get(str(node.this).upper())
            if shim is not None:
                args = [arg.sql(dialect=dialect.value) for arg in node.expressions]
                low, high = shim.arity
                if not low <= len(args) <= high:
                    raise ShimError(f"{shim.name} takes {low}..{high} arguments, got {len(args)}")
                rendered = shim.render[dialect](args)
                return exp.paren(_parse(rendered, dialect=dialect.value), copy=False)
        return node

    for _ in range(_MAX_SHIM_PASSES):
        if not _has_unrendered_shim(expression):
            break
        expression = expression.transform(transform)
    else:
        remaining = sorted(_unrendered_shim_names(expression))
        raise ShimError(f"Shim rewriting did not converge; still present: {remaining}")

    rendered: str = expression.sql(dialect=dialect.value)
    return rendered


def _unrendered_shim_names(tree: exp.Expression) -> set[str]:
    return {
        str(node.this).upper()
        for node in tree.find_all(exp.Anonymous)
        if str(node.this).upper() in SHIMS
    }


def _has_unrendered_shim(tree: exp.Expression) -> bool:
    return bool(_unrendered_shim_names(tree))


def shim_catalog() -> list[Shim]:
    """Every registered shim — rendered into the docs so divergences stay visible."""
    return [SHIMS[name] for name in sorted(SHIMS)]
