"""The specialist agents (BUILD_PROMPT §12.2).

Prompts are versioned markdown in ``packages/agents/prompts/``, never inline in
code (§12.4): they are reviewed by the same people who review the metric
definitions, and a prompt change should show up in a diff as prose, not as a
Python string literal.

Each specialist gets only the tools its role needs. A governance agent has no
business simulating a cost lever, and narrowing the tool surface is the cheapest
way to keep an agent on task.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from snowobs_agents.runtime.supervisor import AgentDefinition
from snowobs_common.errors import ConfigurationError

PROMPTS_DIR = Path(__file__).resolve().parent.parent / "prompts"

#: Tools every specialist needs to answer anything at all.
_BASE_TOOLS = ("query_metric", "list_metrics", "describe_metric", "get_coverage")

_SPECIALISTS: dict[str, tuple[str, tuple[str, ...]]] = {
    "finops": (
        "Spend, allocation, chargeback, forecast, and close support.",
        (*_BASE_TOOLS, "explain_delta"),
    ),
    "sre": (
        "Pipeline health, freshness, failures, root-cause chains, warehouse behaviour.",
        (*_BASE_TOOLS, "explain_delta"),
    ),
    "governance": (
        "Access, grant drift, dormancy, tagging and policy coverage.",
        _BASE_TOOLS,
    ),
    "usage": (
        "Adoption and consumption patterns: who uses what, how much, and how it is changing.",
        (*_BASE_TOOLS, "explain_delta"),
    ),
    "health": (
        "Platform health as a state: what is failing, stale, or saturated, and its blast radius.",
        (*_BASE_TOOLS, "explain_delta"),
    ),
    "org": (
        "Cross-account roll-ups, account comparison, contracts and commitment burn-down.",
        (*_BASE_TOOLS, "explain_delta"),
    ),
    "optimisation": (
        "Lever ranking, simulation, and change drafting.",
        (*_BASE_TOOLS, "explain_delta"),
    ),
    "onboarding": (
        "Source identification, column mapping, drift resolution, coverage.",
        _BASE_TOOLS,
    ),
    "curator": (
        "Propose, version, contract, and publish data products.",
        _BASE_TOOLS,
    ),
    "supervisor": (
        "Intent routing, multi-agent composition, final synthesis.",
        (*_BASE_TOOLS, "explain_delta"),
    ),
}


def _read_prompt(name: str) -> str:
    path = PROMPTS_DIR / f"{name}.md"
    if not path.exists():
        raise ConfigurationError(f"Missing agent prompt: {path.name}")
    return path.read_text(encoding="utf-8")


@lru_cache(maxsize=1)
def shared_prompt() -> str:
    """Grounding, injection defence, and refusal rules — applied to every agent."""
    return _read_prompt("_shared")


@lru_cache(maxsize=len(_SPECIALISTS))
def build_agent(name: str) -> AgentDefinition:
    if name not in _SPECIALISTS:
        raise ConfigurationError(
            f"Unknown agent '{name}'. Available: {', '.join(sorted(_SPECIALISTS))}"
        )
    description, tools = _SPECIALISTS[name]
    return AgentDefinition(
        name=name,
        # The shared rules come first so a specialist prompt cannot accidentally
        # loosen them by being read later.
        system_prompt=f"{shared_prompt()}\n\n---\n\n{_read_prompt(name)}",
        tool_names=tools,
        description=description,
    )


def all_agents() -> dict[str, AgentDefinition]:
    return {name: build_agent(name) for name in _SPECIALISTS}


def agent_names() -> list[str]:
    return sorted(_SPECIALISTS)
