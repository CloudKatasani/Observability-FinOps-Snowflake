import { useState } from "react";

import type { Provenance, SourceCoverage } from "@/api/client";
import DataTable from "@/components/DataTable";
import PageFrame from "@/components/PageFrame";
import Panel from "@/components/Panel";
import { StatusPill } from "@/components/badges";
import { ErrorState, LoadingRegion } from "@/components/states";
import { useCoverage, useSources } from "@/hooks/useApi";
import { formatClockTime, formatInteger, formatIsoDate, formatMinutes, shortObjectName } from "@/lib/format";
import { countByStatus, freshnessVerdict, statusLabel, statusTone } from "@/lib/status";

const CRITICALITY_TONES = {
  core: "bad",
  important: "warn",
  optional: "muted",
} as const;

function isBlocking(source: SourceCoverage): boolean {
  return source.status === "missing" || source.status === "empty" || source.status === "stale";
}

function Remediation({ text }: { text: string }) {
  const [copied, setCopied] = useState(false);
  const copy = async () => {
    try {
      await navigator.clipboard?.writeText(text);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 2000);
    } catch {
      setCopied(false);
    }
  };

  return (
    <div className="flex items-start gap-2">
      <code className="min-w-0 flex-1 font-mono text-[11px] leading-relaxed break-words whitespace-pre-wrap text-slate-700">
        {text}
      </code>
      <button
        type="button"
        onClick={copy}
        className="shrink-0 rounded border border-slate-300 bg-white px-1.5 py-0.5 text-[11px] font-medium text-slate-700 hover:bg-slate-100 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-slate-700"
      >
        {copied ? "Copied" : "Copy"}
      </button>
    </div>
  );
}

export default function CoveragePage() {
  const coverage = useCoverage();
  const sources = useSources();
  const [problemsOnly, setProblemsOnly] = useState(false);

  const all = coverage.data?.sources ?? [];
  const counts = countByStatus(all);
  const visible = problemsOnly ? all.filter(isBlocking) : all;

  const domains = [...new Set(visible.map((source) => source.domain))].sort();
  const unavailableMetrics = (coverage.data?.metrics ?? []).filter(
    (metric) => metric.availability !== "enabled",
  );

  // The coverage matrix is not a metric response, but it still has a freshness
  // floor: the slowest documented latency among the sources that actually
  // landed. Synthesising it here keeps R7's banner on this page too.
  const landed = all.filter((source) => source.status === "available" || source.status === "stale");
  const contribution: Provenance | null =
    coverage.data && landed.length > 0
      ? {
          as_of: coverage.data.as_of,
          latency_floor_minutes: Math.max(
            ...landed.map((source) => source.documented_latency_minutes),
          ),
          provisional: false,
          sources: landed.map((source) => source.source_id),
        }
      : null;

  return (
    <PageFrame
      title="Coverage & sources"
      description="What landed, how fresh it is against its documented latency, and exactly what to run to fill each gap."
      contributions={[contribution]}
      sources={sources.data}
    >
      {coverage.isPending ? (
        <Panel title="Coverage matrix">
          <LoadingRegion label="Assessing every registered source" lines={8} />
        </Panel>
      ) : coverage.isError ? (
        <ErrorState
          title="Coverage unavailable"
          error={coverage.error}
          remediation="The coverage endpoint did not answer. Check that the API service is running, then retry."
          onRetry={() => void coverage.refetch()}
        />
      ) : (
        <>
          <section
            aria-label="Coverage summary"
            className="flex flex-wrap items-center gap-3 rounded border border-slate-200 bg-white p-4"
          >
            <p className="text-sm text-slate-700">
              <span className="font-semibold tabular-nums">{all.length}</span> registered sources,
              assessed at{" "}
              <span className="tabular-nums">
                {coverage.data.as_of ? formatClockTime(coverage.data.as_of) : "unknown"}
              </span>{" "}
              in <span className="font-mono text-xs">{coverage.data.mode}</span> mode.
            </p>
            <span className="flex flex-wrap gap-1.5">
              <StatusPill tone="good" label={`${counts.available} available`} />
              <StatusPill tone="warn" label={`${counts.stale} stale`} />
              <StatusPill tone="warn" label={`${counts.empty} empty`} />
              <StatusPill tone="bad" label={`${counts.missing} missing`} />
            </span>
            <label className="ml-auto flex items-center gap-2 text-sm text-slate-700">
              <input
                type="checkbox"
                checked={problemsOnly}
                onChange={(event) => setProblemsOnly(event.target.checked)}
                className="size-4 accent-[var(--brand-primary,#475569)]"
              />
              Show only sources needing action
            </label>
          </section>

          {domains.length === 0 ? (
            <Panel title="Coverage matrix">
              <p className="rounded border border-dashed border-slate-300 bg-slate-50 p-4 text-sm text-slate-700">
                Every registered source is present and inside its documented latency. Nothing needs
                action.
              </p>
            </Panel>
          ) : (
            domains.map((domain) => {
              const rows = visible
                .filter((source) => source.domain === domain)
                .sort((a, b) => a.source_id.localeCompare(b.source_id));

              return (
                <Panel
                  key={domain}
                  title={`${domain} sources`}
                  subtitle={`${rows.length} of ${all.filter((source) => source.domain === domain).length} shown`}
                >
                  <DataTable
                    caption={`Coverage of the ${domain} domain`}
                    dense
                    columns={[
                      { key: "source", header: "Source" },
                      { key: "criticality", header: "Criticality" },
                      { key: "status", header: "Status" },
                      { key: "rows", header: "Rows", numeric: true },
                      { key: "window", header: "Window" },
                      { key: "freshness", header: "Freshness vs documented" },
                      { key: "enables", header: "KPIs", numeric: true },
                    ]}
                    rows={rows.map((source) => {
                      const verdict = freshnessVerdict(source);
                      const criticality =
                        CRITICALITY_TONES[source.criticality as keyof typeof CRITICALITY_TONES] ??
                        "muted";
                      return {
                        source: (
                          <span
                            className="font-mono text-xs"
                            title={source.snowflake_object}
                          >
                            {shortObjectName(source.snowflake_object)}
                          </span>
                        ),
                        criticality: <StatusPill tone={criticality} label={source.criticality} />,
                        status: (
                          <StatusPill
                            tone={statusTone(source.status)}
                            label={statusLabel(source.status)}
                          />
                        ),
                        rows: formatInteger(source.rows) ?? "0",
                        window:
                          source.window_start && source.window_end ? (
                            <span className="tabular-nums">
                              {formatIsoDate(source.window_start)} →{" "}
                              {formatIsoDate(source.window_end)}
                            </span>
                          ) : (
                            <span className="text-slate-500">none</span>
                          ),
                        freshness: (
                          <span className="block">
                            <StatusPill tone={verdict.tone} label={verdict.label} />
                            <span className="mt-0.5 block text-[11px] text-slate-500">
                              documented {formatMinutes(source.documented_latency_minutes)}
                              {source.latency_verified ? "" : " (unverified)"}
                            </span>
                          </span>
                        ),
                        enables: formatInteger(source.enables_metric_count) ?? "0",
                      };
                    })}
                  />

                  {rows.some((source) => source.remediation) ? (
                    <div className="mt-3 space-y-2 border-t border-slate-200 pt-3">
                      <p className="text-[11px] font-semibold tracking-wide text-slate-600 uppercase">
                        Remediation
                      </p>
                      {rows
                        .filter((source) => source.remediation)
                        .map((source) => (
                          <div
                            key={source.source_id}
                            className="rounded border border-slate-200 bg-slate-50 p-2"
                          >
                            <p className="mb-1 font-mono text-[11px] font-semibold text-slate-800">
                              {shortObjectName(source.snowflake_object)}
                            </p>
                            <Remediation text={source.remediation as string} />
                          </div>
                        ))}
                    </div>
                  ) : null}
                </Panel>
              );
            })
          )}

          {unavailableMetrics.length > 0 ? (
            <Panel
              title="KPIs affected"
              subtitle="Every metric that a missing or partial source degrades or blocks"
            >
              <DataTable
                caption="Metric availability and the sources blocking each one"
                dense
                columns={[
                  { key: "metric", header: "Metric" },
                  { key: "availability", header: "Availability" },
                  { key: "explanation", header: "Explanation" },
                ]}
                rows={unavailableMetrics.map((metric) => ({
                  metric: <span className="font-mono text-xs">{metric.metric_id}</span>,
                  availability: (
                    <StatusPill
                      tone={metric.availability === "degraded" ? "warn" : "bad"}
                      label={metric.availability}
                    />
                  ),
                  explanation: metric.explanation,
                }))}
              />
            </Panel>
          ) : null}
        </>
      )}
    </PageFrame>
  );
}
