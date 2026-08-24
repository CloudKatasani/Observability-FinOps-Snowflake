"""``CREATE AGENT`` scoped to one data product (BUILD_PROMPT §13.4).

One agent per product, never one agent over everything: a kitchen-sink agent
degrades on every question it was not built for, and its blast radius is the
union of every product it can reach.

The agent gets Cortex Analyst over the product's semantic view, Cortex Search
over its indexed text column where it has one, and a constrained ``sql_exec``
tool pinned to the agent warehouse with a statement timeout. Its instructions
carry R12 explicitly: the agent quotes tool results and never computes or
estimates a figure itself.

The ``models`` key is emitted only when the deployment pins an orchestration
model. Model availability varies by region and edition, so an unpinned spec
takes the account default rather than hard-coding an identifier this repository
cannot verify against the customer's account (ASSUMPTIONS U-4).
"""

from __future__ import annotations

import json
from typing import Any

import yaml

from snowobs_dataproducts.contracts import DataContract
from snowobs_dataproducts.emitters import (
    SnowflakeTarget,
    header,
    ident,
    literal,
    one_line,
)
from snowobs_dataproducts.emitters.cortex_search import search_service_name
from snowobs_dataproducts.emitters.semantic_view import verified_queries
from snowobs_dataproducts.model import DataProduct
from snowobs_semantics.model import SemanticModel, default_model

#: How many sample questions the agent advertises in Snowflake Intelligence.
SAMPLE_QUESTION_COUNT = 6
#: Rows the agent's constrained SQL tool may return.
SQL_TOOL_MAX_ROWS = 1000


def agent_name(product: DataProduct) -> str:
    return f"{product.slug_upper}_AGENT"


def build_specification(
    product: DataProduct,
    contract: DataContract,
    model: SemanticModel | None = None,
    *,
    target: SnowflakeTarget | None = None,
) -> dict[str, Any]:
    """The agent specification object, before serialisation."""
    resolved_model = model or default_model()
    resolved = target or SnowflakeTarget()
    semantic_view = resolved.qualified(resolved.semantic_schema, product.slug_upper)

    questions = [
        query.question
        for query in verified_queries(product, contract, resolved_model, target=resolved)
        if query.onboarding
    ][:SAMPLE_QUESTION_COUNT]

    tools: list[dict[str, Any]] = [
        {
            "tool_spec": {
                "type": "cortex_analyst_text_to_sql",
                "name": f"{product.id}_analyst",
                "description": one_line(
                    f"Answer aggregate, trend, and comparison questions about "
                    f"{product.name} from its governed semantic view. Covers "
                    f"{len(contract.metric_ids)} metrics: "
                    f"{', '.join(contract.metric_ids)}."
                ),
            }
        }
    ]
    tool_resources: dict[str, Any] = {
        f"{product.id}_analyst": {"semantic_view": semantic_view},
    }

    if product.search is not None:
        service = resolved.qualified(resolved.search_schema, search_service_name(product))
        tools.append(
            {
                "tool_spec": {
                    "type": "cortex_search",
                    "name": f"{product.id}_search",
                    "description": one_line(
                        f"Find specific {product.search.column.lower()} values by free text. "
                        f"Use when the question is about which particular items match a "
                        f"description, rather than how many there are."
                    ),
                }
            }
        )
        tool_resources[f"{product.id}_search"] = {
            "name": service,
            "max_results": 10,
            "title_column": product.search.column,
        }

    tools.append(
        {
            "tool_spec": {
                "type": "sql_exec",
                "name": f"{product.id}_sql",
                "description": one_line(
                    "Execute a follow-up query against this product's published views "
                    "only. Never against curated or raw schemas."
                ),
            }
        }
    )
    tool_resources[f"{product.id}_sql"] = {
        "execution_environment": {
            "type": "warehouse",
            "warehouse": resolved.agent_warehouse,
            "query_timeout": resolved.agent_query_timeout_seconds,
        },
        "max_rows": SQL_TOOL_MAX_ROWS,
    }

    specification: dict[str, Any] = {}
    if resolved.orchestration_model:
        specification["models"] = {"orchestration": resolved.orchestration_model}
    specification["orchestration"] = {
        "budget": {
            "seconds": resolved.orchestration_budget_seconds,
            "tokens": resolved.orchestration_budget_tokens,
        }
    }
    specification["instructions"] = {
        "response": _response_instructions(product, contract),
        "orchestration": _orchestration_instructions(product),
        "sample_questions": [{"question": question} for question in questions],
    }
    specification["tools"] = tools
    specification["tool_resources"] = tool_resources
    return specification


def _response_instructions(product: DataProduct, contract: DataContract) -> str:
    relations = ", ".join(contract.dataset_names)
    return one_line(
        f"""
        You answer questions about {product.name}, an observability data product
        owned by {product.owner}. Scope: {relations}.

        Every number you state must come from a tool result. Never compute,
        estimate, extrapolate, or round a figure yourself; if a tool did not
        return it, say you cannot ground the claim and offer the question you
        could answer instead.

        State the time range and the as-of point of every figure. This product's
        freshness guarantee is {contract.freshness_guarantee_minutes} minutes,
        which is the documented latency of its slowest source: never imply a
        figure is fresher than that, and say so when a user asks about the last
        hour.

        A null is not a zero. If a measure is null, report it as unknown for that
        slice and explain why, rather than reporting no activity.

        Cite the relation and the governed metric behind each figure so the user
        can check it. Answer only from this product; if a question needs data
        outside {relations}, say which product would have it.
        """
    )


def _orchestration_instructions(product: DataProduct) -> str:
    search_clause = (
        f"If the question asks which specific {product.search.column.lower()} values match a "
        f"description, use the search tool."
        if product.search is not None
        else "This product has no free-text search; do not attempt one."
    )
    return one_line(
        f"""
        Decompose the question first. If it asks how much, how many, a trend, or a
        comparison, use the analyst tool over the semantic view. {search_clause}
        Use the SQL tool only to follow up on a result the analyst tool already
        returned, and only against this product's published views.

        Never invent a dimension value. If a filter value does not appear in the
        data, say so rather than returning an empty result as if it were zero.

        If a question is ambiguous between two slices, ask which one before
        answering.
        """
    )


def emit_agent_spec(
    product: DataProduct,
    contract: DataContract,
    model: SemanticModel | None = None,
    *,
    target: SnowflakeTarget | None = None,
) -> str:
    """Render ``CREATE AGENT`` with the product-scoped specification."""
    resolved = target or SnowflakeTarget()
    specification = build_specification(product, contract, model, target=resolved)
    body = yaml.safe_dump(
        specification, sort_keys=False, default_flow_style=False, width=100, allow_unicode=True
    )
    profile = json.dumps({"display_name": f"{product.name} Copilot"}, sort_keys=True)
    name = resolved.qualified(resolved.agent_schema, agent_name(product))
    comment = one_line(
        f"Conversational interface to {product.name} {product.version}. "
        f"Scoped to the product's semantic view; quotes tool results only."
    )
    return (
        header(
            f"{product.name} {product.version} — Cortex Agent",
            [
                "One agent per data product. It can reach this product's semantic view,",
                "its search service, and its published views — nothing else.",
                "",
                "Surfaced in Snowflake Intelligence to any role holding USAGE on the",
                "agent; grants are in the grants script.",
            ],
        )
        + f"USE ROLE {ident(resolved.publisher_role)};\n"
        + "\n"
        + f"CREATE OR REPLACE AGENT {name}\n"
        + f"  COMMENT = {literal(comment)}\n"
        + f"  PROFILE = {literal(profile)}\n"
        + "  FROM SPECIFICATION $$\n"
        + body.rstrip("\n")
        + "\n$$;\n"
    )
