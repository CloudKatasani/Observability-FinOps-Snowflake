import { useQuery } from "@tanstack/react-query";

import { fetchLiveness, fetchMeta, fetchReadiness } from "@/api/client";

function StatusBadge({ ok, label }: { ok: boolean; label: string }) {
  // Status is encoded with text + shape, never colour alone (WCAG, §16.2).
  return (
    <span
      className={`inline-flex items-center gap-1.5 rounded-full px-2.5 py-0.5 text-sm font-medium ${
        ok ? "bg-emerald-100 text-emerald-900" : "bg-red-100 text-red-900"
      }`}
    >
      <span aria-hidden>{ok ? "✓" : "✗"}</span>
      {label}
    </span>
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
          {meta.data?.branding.display_name ?? "Observability & FinOps Platform"}
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

      <section aria-labelledby="api-status" className="rounded-lg border border-slate-200 p-6">
        <h2 id="api-status" className="mb-4 text-lg font-medium text-slate-900">
          API
        </h2>
        {liveness.isPending ? (
          <p className="text-sm text-slate-500">Checking…</p>
        ) : liveness.isError ? (
          <StatusBadge ok={false} label="API unreachable" />
        ) : (
          <StatusBadge ok label={`API up (v${liveness.data.version})`} />
        )}

        <h2 className="mb-4 mt-6 text-lg font-medium text-slate-900">Backing services</h2>
        {readiness.isPending ? (
          <p className="text-sm text-slate-500">Checking…</p>
        ) : readiness.isError ? (
          <p className="text-sm text-red-900">
            Readiness could not be determined — the API did not answer. Check that the API
            container is running and reachable.
          </p>
        ) : (
          <ul className="space-y-2">
            {readiness.data.components.map((component) => (
              <li key={component.name} className="flex items-center gap-3">
                <StatusBadge
                  ok={component.status === "ok"}
                  label={
                    component.status === "ok"
                      ? `${component.name} reachable`
                      : `${component.name} unavailable${component.detail ? ` (${component.detail})` : ""}`
                  }
                />
              </li>
            ))}
          </ul>
        )}
      </section>
    </main>
  );
}
