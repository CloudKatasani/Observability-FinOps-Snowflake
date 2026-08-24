"""Wire the golden question set to a live agent (BUILD_PROMPT §12.6).

Run it against whatever provider is configured::

    uv run python -m snowobs_agents.evals.runner

With no API key this exercises the deterministic path, which is a genuine
operating mode but cannot be held to every gate: a router that matches
questions to metrics does not decline a governance question on principle or
ask a clarifying one, because it does not reason about the question at all.
Rather than score those categories as failures — which would make the report
say the platform is broken when it is merely unnarrated — they are reported as
*unassertable*, and the gates apply to the rest.

The two gates that always apply, in every mode, are the two that describe harm
rather than quality: no fabricated figure, and no injection obeyed.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass

from snowobs_agents.evals.harness import (
    Category,
    EvalReport,
    GoldenQuestion,
    load_questions,
    score,
)
from snowobs_agents.runtime.supervisor import AgentRuntime, Supervisor
from snowobs_agents.runtime.tools import ToolContext
from snowobs_agents.specialists.registry import all_agents, build_agent

#: Categories that need a model to reason about the *question* rather than
#: look up an answer. The deterministic router cannot be held to these.
LLM_ONLY_CATEGORIES = frozenset(
    {Category.AMBIGUOUS, Category.OUT_OF_SCOPE, Category.REFUSAL, Category.UNAVAILABLE}
)

#: Categories every mode is accountable for, because failing them is a harm and
#: not a missing feature.
ALWAYS_ASSERTED_CATEGORIES = frozenset({Category.INJECTION})


@dataclass
class EvalRun:
    """A scored run plus what it could not assert, so a report cannot mislead."""

    report: EvalReport
    skipped: list[GoldenQuestion]
    narrative: bool

    def summary(self) -> str:
        lines = [self.report.summary()]
        if self.skipped:
            categories = sorted({question.category.value for question in self.skipped})
            lines.append(
                f"  Unassertable   : {len(self.skipped)} question(s) in {', '.join(categories)} "
                "— these need a narrative provider; no LLM is configured."
            )
        return "\n".join(lines)


def evaluate(
    context: ToolContext,
    runtime: AgentRuntime,
    questions: list[GoldenQuestion] | None = None,
) -> EvalRun:
    """Run the golden set through a real supervisor and score every answer."""
    del context  # the runtime already holds it; kept for call-site symmetry
    supervisor = Supervisor(runtime=runtime, agents=all_agents())
    narrative = runtime.provider.generates_narrative
    report = EvalReport()
    skipped: list[GoldenQuestion] = []

    for question in questions if questions is not None else load_questions():
        if (
            not narrative
            and question.category in LLM_ONLY_CATEGORIES
            and question.category not in ALWAYS_ASSERTED_CATEGORIES
        ):
            skipped.append(question)
            continue

        agent = (
            build_agent(question.expected_agent)
            if question.expected_agent
            else supervisor.route(question.question)
        )
        result = runtime.run(agent, question.question)
        report.results.append(
            score(
                question,
                answer=result.answer,
                tools_called=[str(step.detail.get("tool", "")) for step in result.trace.tool_calls],
                metrics_used=result.trace.metrics_used,
                grounded=result.grounded,
                refused=result.refused,
                tool_outputs=result.tool_outputs,
            )
        )
    return EvalRun(report=report, skipped=skipped, narrative=narrative)


def main() -> int:  # pragma: no cover - operator entry point
    from pathlib import Path

    from snowobs_common.config import Settings
    from snowobs_engines.duckdb_engine import DuckDBEngine
    from snowobs_ingest.catalog import DuckDBCatalog
    from snowobs_ingest.coverage import build_coverage_matrix
    from snowobs_llm.providers import build_provider
    from snowobs_semantics.compiler import SemanticCompiler
    from snowobs_semantics.model import default_model

    settings = Settings()
    root = Path(settings.storage.bucket if settings.storage.provider == "local" else ".data")
    catalog = DuckDBCatalog(root, tenant="default")
    catalog.register_all()
    try:
        model = default_model()
        context = ToolContext(
            engine=DuckDBEngine(catalog),
            compiler=SemanticCompiler(),
            model=model,
            actor="evals",
            coverage=build_coverage_matrix(catalog, metric_requirements=model.requirements()),
        )
        run = evaluate(context, AgentRuntime(build_provider(settings.llm), context))
    finally:
        catalog.close()

    print(run.summary())
    return 0 if run.report.gates_met else 1


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
