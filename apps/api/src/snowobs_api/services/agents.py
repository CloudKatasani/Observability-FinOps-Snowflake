"""Agent orchestration for the API (BUILD_PROMPT §12, §14).

Builds a tool context over the tenant's landed data and runs one turn. The
context is assembled here, once, so a tool can never reach around it: row-level
filters, the caller's roles, and the coverage matrix all arrive as arguments
rather than being fetched from inside a tool.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import AbstractContextManager
from typing import Any

from snowobs_agents.runtime.guardrails import BudgetTracker
from snowobs_agents.runtime.supervisor import (
    AgentDefinition,
    AgentRuntime,
    Supervisor,
    TurnResult,
    turn_events,
)
from snowobs_agents.runtime.tools import ToolContext
from snowobs_agents.runtime.trace import Trace, TraceStore
from snowobs_agents.specialists.registry import agent_names, all_agents, build_agent
from snowobs_api.services.engines import EngineChoice, open_engine
from snowobs_common.config import Settings
from snowobs_common.errors import ConfigurationError
from snowobs_ingest.coverage import build_coverage_matrix
from snowobs_llm.providers import build_provider
from snowobs_semantics.compiler import SemanticCompiler
from snowobs_semantics.model import SemanticModel, default_model


class AgentService:
    """One turn of one agent, against this tenant's data."""

    def __init__(
        self,
        settings: Settings,
        tenant: str = "default",
        *,
        actor: str = "anonymous",
        roles: frozenset[str] = frozenset(),
    ) -> None:
        self.settings = settings
        self.tenant = tenant
        self.actor = actor
        self.roles = roles
        # A tracker per process, so a runaway conversation is stopped by the
        # same accounting that stops a runaway script (§12.5).
        self._budget = _shared_budget()
        self._traces = _shared_traces()

    def _storage_root(self) -> Any:
        from snowobs_api.services.datasets import storage_root

        return storage_root(self.settings)

    def catalogue(self) -> list[AgentDefinition]:
        return [build_agent(name) for name in agent_names()]

    def _runtime(self, chosen: EngineChoice) -> AgentRuntime:
        model = default_model()
        context = ToolContext(
            engine=chosen.engine,
            compiler=SemanticCompiler(),
            model=model,
            tenant=self.tenant,
            actor=self.actor,
            roles=self.roles,
            # R3: without coverage the agent cannot tell "zero" from "not
            # loaded", which is the distinction the whole product turns on.
            # LIVE reads whole schemas rather than an enumerated view list, so
            # there is no landed-source matrix to build there; the grants probe
            # on the connections page is that mode's equivalent.
            coverage=self._coverage(chosen, model),
            # §12.3: the ad-hoc hatch stays shut unless a deployment opens it.
            allow_adhoc_sql=self.settings.guardrails.allow_adhoc_sql,
        )
        provider = build_provider(self.settings.llm)
        return AgentRuntime(provider, context, budget=self._budget)

    @staticmethod
    def _coverage(chosen: EngineChoice, model: SemanticModel) -> Any:
        catalog = getattr(chosen.engine, "catalog", None)
        if catalog is None:
            return None
        return build_coverage_matrix(
            catalog,
            metric_requirements=model.requirements(),
            optional_requirements=model.optional_requirements(),
        )

    def _open(self) -> AbstractContextManager[EngineChoice]:
        return open_engine(self.settings, tenant=self.tenant)

    def ask(self, question: str, agent: str | None = None) -> TurnResult:
        with self._open() as chosen:
            runtime = self._runtime(chosen)
            if agent is not None and agent not in agent_names():
                raise ConfigurationError(
                    f"Unknown agent '{agent}'. Available: {', '.join(agent_names())}"
                )
            result = (
                runtime.run(build_agent(agent), question)
                if agent is not None
                else Supervisor(runtime=runtime, agents=all_agents()).ask(question)
            )
            self._traces.save(result.trace)
            return result

    def stream(self, question: str, agent: str | None = None) -> Iterator[dict[str, Any]]:
        """Stream one turn as SSE-ready events.

        The engine stays open for the whole generator, so a consumer that
        disconnects mid-stream still releases it — the context manager exits
        when the generator is closed.
        """
        with self._open() as chosen:
            runtime = self._runtime(chosen)
            definition = (
                build_agent(agent)
                if agent is not None
                else Supervisor(runtime=runtime, agents=all_agents()).route(question)
            )
            yield {"event": "agent_selected", "agent": definition.name}
            result = runtime.run(definition, question)
            self._traces.save(result.trace)
            yield from turn_events(result)


_BUDGET = BudgetTracker()
_TRACES = TraceStore()


def _shared_budget() -> BudgetTracker:
    """Daily spend is per actor and per tenant, so it outlives one request."""
    return _BUDGET


def _shared_traces() -> TraceStore:
    """Recent traces, so the id on an answer can be looked up afterwards."""
    return _TRACES


def recent_traces(limit: int = 20) -> list[Trace]:
    return _TRACES.recent(limit)


def get_trace(trace_id: str) -> Trace | None:
    return _TRACES.get(trace_id)
