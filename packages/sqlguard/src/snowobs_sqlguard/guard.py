"""The SQL guard (BUILD_PROMPT §12.5, R9).

Every statement that reaches an engine — compiled from the semantic layer, typed
by an admin, or proposed by an agent — passes through here first. The guard
parses with SQLGlot (never regex), rejects anything that is not a single
read-only ``SELECT``/``WITH``, restricts table references to an allowlisted
schema set, forces a ``LIMIT``, and returns the execution envelope (timeout,
warehouse pin, query tag) the engine must apply.

There is no bypass. A caller that wants to skip the guard is a bug.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import StrEnum

import sqlglot
from sqlglot import exp

from snowobs_common.errors import AppError

DEFAULT_MAX_ROWS = 50_000
DEFAULT_TIMEOUT_SECONDS = 300

#: Functions that read the account's state, escape the SQL sandbox, or perform
#: side effects. Rejected outright regardless of context.
FORBIDDEN_FUNCTION_PREFIXES = ("SYSTEM$",)
FORBIDDEN_FUNCTIONS = frozenset(
    {
        "GET_DDL",
        "CURRENT_ACCOUNT",  # account identity is not the agent's business
        "GETVARIABLE",
        "SETVARIABLE",
        "EXTERNAL_FUNCTION",
        "GET_PRESIGNED_URL",
        "GET_STAGE_LOCATION",
    }
)

#: Statement keywords that must never appear, even inside a CTE or subquery.
FORBIDDEN_STATEMENTS = (
    exp.Insert,
    exp.Update,
    exp.Delete,
    exp.Drop,
    exp.Create,
    exp.Alter,
    exp.Merge,
    exp.Command,  # COPY, PUT, GET, CALL, GRANT, USE, SET … parse as Command
    exp.Grant,
    exp.Use,
    exp.Set,
    exp.Transaction,
    exp.Commit,
    exp.Rollback,
)

_COMMENT_PATTERN = re.compile(r"/\*.*?\*/|--[^\n]*", re.DOTALL)


class GuardVerdict(StrEnum):
    ALLOWED = "allowed"
    REJECTED = "rejected"


class SqlGuardError(AppError):
    """A statement was refused. The message is safe to show a user."""

    status_code = 400
    title = "SQL rejected by the guard"
    problem_type = "https://snowobs.dev/problems/sql-guard"


@dataclass(frozen=True)
class GuardPolicy:
    """What this caller is allowed to run."""

    #: Fully-qualified schemas (``DB.SCHEMA``) or bare relation names that may be
    #: referenced. Bare names cover the OFFLINE catalog's registered views.
    allowed_schemas: frozenset[str] = frozenset()
    allowed_relations: frozenset[str] = frozenset()
    max_rows: int = DEFAULT_MAX_ROWS
    statement_timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS
    warehouse: str | None = None
    query_tag: str | None = None
    #: Allow relations not on either list. Only ever true for the semantic
    #: compiler's own output in tests; never for user or agent SQL.
    allow_unlisted_relations: bool = False


@dataclass(frozen=True)
class GuardedStatement:
    """A statement that passed, plus the envelope the engine must apply."""

    sql: str
    dialect: str
    relations: tuple[str, ...]
    limit: int
    statement_timeout_seconds: int
    warehouse: str | None
    query_tag: str | None
    #: Non-fatal notes: what the guard changed (e.g. "LIMIT added").
    adjustments: tuple[str, ...] = field(default=())


def _function_name(node: exp.Func) -> str:
    """The function's name as written.

    SQLGlot parses unrecognised functions (which is what every Snowflake
    ``SYSTEM$…`` and ``GET_DDL`` is) as :class:`~sqlglot.exp.Anonymous`, whose
    ``sql_name()`` is the literal string "ANONYMOUS" — the real name lives in
    ``this``. Reading only ``sql_name()`` would let precisely the dangerous
    functions through.
    """
    if isinstance(node, exp.Anonymous):
        return str(node.this).upper()
    return (node.sql_name() or "").upper()


def _relation_name(table: exp.Table) -> str:
    parts = [part for part in (table.catalog, table.db, table.name) if part]
    return ".".join(parts).upper()


def _is_allowed_relation(name: str, policy: GuardPolicy) -> bool:
    if policy.allow_unlisted_relations:
        return True
    if name in policy.allowed_relations:
        return True
    # DB.SCHEMA.TABLE is allowed when DB.SCHEMA is allowlisted.
    parts = name.split(".")
    for depth in range(len(parts) - 1, 0, -1):
        if ".".join(parts[:depth]) in policy.allowed_schemas:
            return True
    return False


def check(sql: str, policy: GuardPolicy, *, dialect: str = "duckdb") -> GuardedStatement:
    """Validate and normalise a statement, or raise :class:`SqlGuardError`."""
    if not sql or not sql.strip():
        raise SqlGuardError("Empty statement")

    # Reject multiple statements before parsing: a trailing statement after a
    # semicolon is the oldest injection trick there is.
    stripped = _COMMENT_PATTERN.sub(" ", sql).strip().rstrip(";").strip()
    if ";" in stripped:
        raise SqlGuardError("Only a single statement may be executed")

    try:
        statements = sqlglot.parse(sql, read=dialect)
    except Exception as exc:
        raise SqlGuardError(f"Statement could not be parsed: {exc}") from exc

    parsed = [s for s in statements if s is not None]
    if len(parsed) != 1:
        raise SqlGuardError(f"Exactly one statement is allowed, found {len(parsed)}")
    tree = parsed[0]

    if not isinstance(tree, exp.Select | exp.Union | exp.Subquery):
        raise SqlGuardError(
            f"Only read-only SELECT/WITH statements are allowed, got {type(tree).__name__}"
        )

    for node in tree.walk():
        if isinstance(node, FORBIDDEN_STATEMENTS):
            raise SqlGuardError(f"Statement type {type(node).__name__.upper()} is not permitted")
        if isinstance(node, exp.Func):
            name = _function_name(node)
            if name in FORBIDDEN_FUNCTIONS or any(
                name.startswith(prefix) for prefix in FORBIDDEN_FUNCTION_PREFIXES
            ):
                raise SqlGuardError(f"Function {name} is not permitted")

    relations: list[str] = []
    for table in tree.find_all(exp.Table):
        name = _relation_name(table)
        if not name:
            continue
        # A CTE reference is not a base relation; those are already checked.
        if name.lower() in {cte.alias_or_name.lower() for cte in tree.find_all(exp.CTE)}:
            continue
        if name not in relations:
            relations.append(name)

    denied = [name for name in relations if not _is_allowed_relation(name, policy)]
    if denied:
        raise SqlGuardError(
            "Statement references relations outside the allowed schemas: "
            + ", ".join(sorted(denied))
        )

    adjustments: list[str] = []
    limit_value = _existing_limit(tree)
    if limit_value is None:
        tree = tree.limit(policy.max_rows)
        limit_value = policy.max_rows
        adjustments.append(f"LIMIT {policy.max_rows} applied")
    elif limit_value > policy.max_rows:
        tree = _replace_limit(tree, policy.max_rows)
        adjustments.append(f"LIMIT reduced from {limit_value} to {policy.max_rows}")
        limit_value = policy.max_rows

    return GuardedStatement(
        sql=tree.sql(dialect=dialect),
        dialect=dialect,
        relations=tuple(relations),
        limit=limit_value,
        statement_timeout_seconds=policy.statement_timeout_seconds,
        warehouse=policy.warehouse,
        query_tag=policy.query_tag,
        adjustments=tuple(adjustments),
    )


def _existing_limit(tree: exp.Expression) -> int | None:
    limit = tree.args.get("limit")
    if limit is None:
        return None
    expression = limit.expression if isinstance(limit, exp.Limit) else limit
    if isinstance(expression, exp.Literal) and expression.is_int:
        return int(expression.this)
    return None


def _replace_limit(tree: exp.Expression, value: int) -> exp.Expression:
    tree.set("limit", exp.Limit(expression=exp.Literal.number(value)))
    return tree


def is_allowed(sql: str, policy: GuardPolicy, *, dialect: str = "duckdb") -> bool:
    """Convenience predicate for callers that only need the verdict."""
    try:
        check(sql, policy, dialect=dialect)
    except SqlGuardError:
        return False
    return True


def offline_policy(relations: frozenset[str], *, max_rows: int = DEFAULT_MAX_ROWS) -> GuardPolicy:
    """Policy for the DuckDB catalog: only registered source views are readable."""
    return GuardPolicy(
        allowed_relations=frozenset(name.upper() for name in relations),
        max_rows=max_rows,
    )


def live_policy(
    *,
    max_rows: int = DEFAULT_MAX_ROWS,
    warehouse: str | None = None,
    query_tag: str | None = None,
    timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS,
    extra_schemas: frozenset[str] = frozenset(),
) -> GuardPolicy:
    """Policy for Snowflake pushdown: the usage schemas, plus any curated ones."""
    return GuardPolicy(
        allowed_schemas=frozenset(
            {
                "SNOWFLAKE.ACCOUNT_USAGE",
                "SNOWFLAKE.ORGANIZATION_USAGE",
                "SNOWFLAKE.READER_ACCOUNT_USAGE",
            }
        )
        | frozenset(s.upper() for s in extra_schemas),
        max_rows=max_rows,
        statement_timeout_seconds=timeout_seconds,
        warehouse=warehouse,
        query_tag=query_tag,
    )
