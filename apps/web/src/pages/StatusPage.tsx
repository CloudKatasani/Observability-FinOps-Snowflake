import { useQuery } from "@tanstack/react-query";

import type { ComponentStatus } from "@/api/client";
import { fetchLiveness, fetchMeta, fetchReadiness } from "@/api/client";

type Tone = "ok" | "bad" | "muted";

const TONE_CLASSES: Record<Tone, string> = {
  ok: "bg-emerald-100 text-emerald-900",
  bad: "bg-red-100 text-red-900",
  // Neutral, not amber: a component this deployment does not use is not a
  // degraded one, and colouring it as a warning is the same false alarm in a
  // quieter voice.
  muted: "bg-slate-100 text-slate-700",
};

const TONE_GLYPHS: Record<Tone, string> = { ok: "✓", bad: "✗", muted: "–" };

function StatusBadge({ tone, label }: { tone: Tone; label: string }) {
  // Status is encoded with text + shape, never colour alone (WCAG, §16.2).
  return (
    <span
      className={`inline-flex items-center gap-1.5 rounded-full px-2.5 py-0.5 text-sm font-medium ${TONE_CLASSES[tone]}`}
    >
      <span aria-hidden>{TONE_GLYPHS[tone]}</span>
      {label}
    </span>
  );
}

/** One backing service, in the three states readiness actually reports. */
function ComponentRow({ component }: { component: ComponentStatus }) {
  const tone: Tone =
    component.status === "ok"
      ? "ok"
      : component.status === "unavailable"
        ? "bad"
        : "muted";
  const label =
    component.status === "ok"
      ? `${component.name} reachable`
      : component.status === "unavailable"
        ? `${component.name} unavailable${component.detail ? ` (${component.detail})` : ""}`
        : `${component.name} not required`;

  return (
    <li className="space-y-1">
      <StatusBadge tone={tone} label={label} />
      {/* The reason is the useful half for a component that is switched off:
          without it "not required" invites the reader to go looking for a
          misconfiguration that is not there. */}
      {component.status === "not_required" && component.detail ? (
        <p className="pl-1 text-xs text-slate-600">{component.detail}</p>
      ) : null}
    </li>
  );
}

export default function StatusPage() {
  const meta = useQuery({ queryKey: ["meta"], queryFn: fetchMeta });
  const liveness = useQuery({ queryKey: ["healthz"], queryFn: fetchLiveness });
  const readiness = useQuery({
    queryKey: ["readyz"],
    queryFn: fetchReadiness,
    refetchInterval: 15_000,
  });

  return (
    <main className="mx-auto max-w-3xl p-8">
      <header className="mb-8">
        <h1 className="text-2xl font-semibold text-slate-900">
          {meta.data?.branding.display_name ??
            "Observability & FinOps Platform"}
        </h1>
        <p className="mt-1 text-sm text-slate-600">
          System status
          {meta.data ? (
            <span className="tabular-nums">
              {" "}
              — v{meta.data.version}, mode {meta.data.mode}
            </span>
          ) : null}
        </p>
      </header>

      <section
        aria-labelledby="api-status"
        className="rounded-lg border border-slate-200 p-6"
      >
        <h2 id="api-status" className="mb-4 text-lg font-medium text-slate-900">
          API
        </h2>
        {liveness.isPending ? (
          <p className="text-sm text-slate-500">Checking…</p>
        ) : liveness.isError ? (
          <StatusBadge tone="bad" label="API unreachable" />
        ) : (
          <StatusBadge tone="ok" label={`API up (v${liveness.data.version})`} />
        )}

        <h2 className="mt-6 mb-4 text-lg font-medium text-slate-900">
          Backing services
        </h2>
        {readiness.isPending ? (
          <p className="text-sm text-slate-500">Checking…</p>
        ) : readiness.isError ? (
          <p className="text-sm text-red-900">
            Readiness could not be determined — the API did not answer. Check
            that the API container is running and reachable.
          </p>
        ) : (
          <ul className="space-y-3">
            {readiness.data.components.map((component) => (
              <ComponentRow key={component.name} component={component} />
            ))}
          </ul>
        )}
      </section>
    </main>
  );
}
