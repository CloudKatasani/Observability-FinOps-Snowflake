import { useState } from "react";

import type { AccountCoverage, Provenance, SourceCoverage } from "@/api/client";
import DataTable from "@/components/DataTable";
import PageFrame from "@/components/PageFrame";
import Panel from "@/components/Panel";
import { StatusPill } from "@/components/badges";
import { ErrorState, LoadingRegion } from "@/components/states";
import { useCoverage, useSources } from "@/hooks/useApi";
import { useScope } from "@/hooks/useScope";
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

/**
 * The accounts the per-account view can speak for.
 *
 * Taken from the sources rather than from `accounts[]`, because an
 * organization's own `ORGANIZATION_USAGE` extract is stamped with the
 * organization's name when it lands — it is not an account, and showing it as a
 * column of a per-account matrix would invite a per-account reading of
 * something that has none. An account earns a column by having landed at least
 * one account-scoped extract, which is the same rule the metrics API applies
 * when it builds the scope selector.
 */
function accountsWithData(sources: readonly SourceCoverage[]): string[] {
  const found = new Set<string>();
  for (const source of sources) {
    if (source.scope !== "account") continue;
    for (const entry of source.accounts) {
      if (entry.status !== "missing") found.add(entry.account);
    }
  }
  return [...found].sort();
}

/**
 * One account's slice of a source, in the shape the rest of the page renders.
 *
 * A source the account has landed nothing for is MISSING *for that account*,
 * even when a sibling account has it — "ACME_PROD has query history, ACME_APAC
 * only has billing" is a coverage answer, not a silence.
 */
function projectToAccount(source: SourceCoverage, account: string): SourceCoverage {
  const slice: AccountCoverage | undefined = source.accounts.find(
    (entry) => entry.account === account,
  );
  if (!slice) {
    return {
      ...source,
      status: "missing",
      rows: 0,
      batches: 0,
      window_start: null,
      window_end: null,
      freshness_minutes: null,
      accounts: [],
    };
  }
  return {
    ...source,
    status: slice.status,
    rows: slice.rows,
    batches: slice.batches,
    window_start: slice.window_start,
    window_end: slice.window_end,
    freshness_minutes: slice.freshness_minutes,
    accounts: [slice],
  };
}

/**
 * What to do about a gap that belongs to one account rather than the tenant.
 *
 * The API's own remediation answers "this source never landed at all"; it has
 * nothing to say about a source three accounts have and a fourth does not, so
 * that sentence is written here — it is an instruction to the operator, not a
 * fact about Snowflake, and no latency or object name is invented for it.
 */
function accountRemediation(source: SourceCoverage, account: string): string | null {
  if (source.remediation) return source.remediation;
  if (source.status === "available" || source.status === "stale") {
    return (
      `${source.snowflake_object} has landed for other accounts but not for ${account}. ` +
      `Upload ${account}'s extract of it, tagged with that account name, so its rows are ` +
      "attributed to the right account."
    );
  }
  return null;
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

/**
 * "Which of my accounts can you see, and how deeply" — the enterprise question
 * this page exists to answer once more than one account has landed.
 */
function AccountMatrix({
  sources,
  accounts,
}: {
  sources: readonly SourceCoverage[];
  accounts: readonly string[];
}) {
  const core = sources.filter((source) => source.criticality === "core");

  return (
    <Panel
      title="Coverage by account"
      subtitle={`${accounts.length} accounts have landed data, across ${sources.length} account-scoped sources`}
    >
      <DataTable
        caption="How much of the account-scoped source set each account has landed"
        dense
        columns={[
          { key: "account", header: "Account" },
          { key: "landed", header: "Sources landed", numeric: true },
          { key: "core", header: "Core sources landed", numeric: true },
          { key: "rows", header: "Rows", numeric: true },
          { key: "gaps", header: "Gaps" },
        ]}
        rows={accounts.map((account) => {
          const view = sources.map((source) => projectToAccount(source, account));
          const counts = countByStatus(view);
          const coreView = core.map((source) => projectToAccount(source, account));
          const coreLanded = coreView.filter(
            (source) => source.status === "available" || source.status === "stale",
          ).length;
          return {
            account: <span className="font-mono text-xs font-semibold">{account}</span>,
            landed: `${counts.available + counts.stale} of ${sources.length}`,
            core: `${coreLanded} of ${core.length}`,
            rows: formatInteger(view.reduce((total, source) => total + source.rows, 0)) ?? "0",
            gaps: (
              <span className="flex flex-wrap gap-1">
                {counts.missing > 0 ? (
                  <StatusPill tone="bad" label={`${counts.missing} missing`} />
                ) : null}
                {counts.stale > 0 ? (
                  <StatusPill tone="warn" label={`${counts.stale} stale`} />
                ) : null}
                {counts.empty > 0 ? (
                  <StatusPill tone="warn" label={`${counts.empty} empty`} />
                ) : null}
                {counts.missing + counts.stale + counts.empty === 0 ? (
                  <StatusPill tone="good" label="complete" />
                ) : null}
              </span>
            ),
          };
        })}
      />

      <details className="mt-3">
        <summary className="cursor-pointer text-[11px] font-medium text-slate-600 underline decoration-dotted underline-offset-2 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-slate-700">
          Source by account
        </summary>
        <div className="mt-2 max-h-96 overflow-auto rounded border border-slate-200">
          <DataTable
            caption="Every account-scoped source's status in every account"
            dense
            columns={[
              { key: "source", header: "Source" },
              ...accounts.map((account) => ({ key: account, header: account })),
            ]}
            rows={sources.map((source) => {
              const row: Record<string, React.ReactNode> = {
                source: (
                  <span className="font-mono text-xs" title={source.snowflake_object}>
                    {shortObjectName(source.snowflake_object)}
                  </span>
                ),
              };
              for (const account of accounts) {
                const view = projectToAccount(source, account);
                row[account] = (
                  <span title={freshnessVerdict(view).detail}>
                    <StatusPill tone={statusTone(view.status)} label={statusLabel(view.status)} />
                  </span>
                );
              }
              return row;
            })}
          />
        </div>
      </details>
    </Panel>
  );
}

/**
 * `ORGANIZATION_USAGE` is exported once for the whole fleet, from whichever
 * account holds the grant. Listing these beside an account's sources would
 * imply every account owes an upload of them, which is not true.
 */
function OrganizationScopedPanel({
  sources,
  account,
}: {
  sources: readonly SourceCoverage[];
  account: string;
}) {
  return (
    <Panel
      title="Organization-scoped sources"
      subtitle={`Exported once for the whole fleet, not per account — these are not ${account}'s to upload`}
    >
      <DataTable
        caption="Organization-scoped sources and their tenant-wide status"
        dense
        columns={[
          { key: "source", header: "Source" },
          { key: "domain", header: "Domain" },
          { key: "status", header: "Status" },
          { key: "rows", header: "Rows", numeric: true },
          { key: "enables", header: "KPIs", numeric: true },
        ]}
        emptyReason="This deployment registers no organization-scoped sources."
        rows={sources.map((source) => ({
          source: (
            <span className="font-mono text-xs" title={source.snowflake_object}>
              {shortObjectName(source.snowflake_object)}
            </span>
          ),
          domain: source.domain,
          status: (
            <StatusPill tone={statusTone(source.status)} label={statusLabel(source.status)} />
          ),
          rows: formatInteger(source.rows) ?? "0",
          enables: formatInteger(source.enables_metric_count) ?? "0",
        }))}
      />
      <p className="mt-2 text-[11px] text-slate-600">
        These figures cover every account together. They are shown at {account} scope for context,
        and they are the same rows the organization scope reads.
      </p>
    </Panel>
  );
}

export default function CoveragePage() {
  const coverage = useCoverage();
  const sources = useSources();
  const scope = useScope();
  const [problemsOnly, setProblemsOnly] = useState(false);

  const everySource = coverage.data?.sources ?? [];
  const knownAccounts = accountsWithData(everySource);
  const account = scope.scope === "account" ? scope.account : null;

  // At account scope the page answers for one account: account-scoped sources
  // carry that account's own rows, and organization-scoped ones are moved out
  // of the domain tables into a panel that says why they are not per account.
  const organizationScoped = account
    ? everySource.filter((source) => source.scope === "organization")
    : [];
  const all = account
    ? everySource
        .filter((source) => source.scope === "account")
        .map((source) => projectToAccount(source, account))
    : everySource;

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
      description={
        account
          ? `What ${account} has landed, how fresh it is against its documented latency, and exactly what to run to fill each gap.`
          : "What landed, how fresh it is against its documented latency, and exactly what to run to fill each gap."
      }
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
              <span className="font-semibold tabular-nums">{all.length}</span>{" "}
              {account ? "account-scoped" : "registered"} sources, assessed at{" "}
              <span className="tabular-nums">
                {coverage.data.as_of ? formatClockTime(coverage.data.as_of) : "unknown"}
              </span>{" "}
              in <span className="font-mono text-xs">{coverage.data.mode}</span> mode
              {account ? (
                <>
                  {" "}
                  for <span className="font-mono text-xs font-semibold">{account}</span>
                </>
              ) : knownAccounts.length > 0 ? (
                <>
                  {" "}
                  across{" "}
                  <span className="font-semibold tabular-nums">{knownAccounts.length}</span>{" "}
                  accounts
                </>
              ) : null}
              .
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

          {!account && knownAccounts.length > 0 ? (
            <AccountMatrix
              sources={everySource.filter((source) => source.scope === "account")}
              accounts={knownAccounts}
            />
          ) : null}

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
              const remediations = rows
                .map((source) => ({
                  source,
                  text: account
                    ? accountRemediation(source, account)
                    : (source.remediation ?? null),
                }))
                .filter((entry): entry is { source: SourceCoverage; text: string } =>
                  Boolean(entry.text),
                );

              return (
                <Panel
                  key={domain}
                  title={`${domain} sources`}
                  subtitle={`${rows.length} of ${all.filter((source) => source.domain === domain).length} shown${account ? ` · ${account}` : ""}`}
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

                  {remediations.length > 0 ? (
                    <div className="mt-3 space-y-2 border-t border-slate-200 pt-3">
                      <p className="text-[11px] font-semibold tracking-wide text-slate-600 uppercase">
                        Remediation
                      </p>
                      {remediations.map((entry) => (
                        <div
                          key={entry.source.source_id}
                          className="rounded border border-slate-200 bg-slate-50 p-2"
                        >
                          <p className="mb-1 font-mono text-[11px] font-semibold text-slate-800">
                            {shortObjectName(entry.source.snowflake_object)}
                          </p>
                          <Remediation text={entry.text} />
                        </div>
                      ))}
                    </div>
                  ) : null}
                </Panel>
              );
            })
          )}

          {account ? (
            <OrganizationScopedPanel sources={organizationScoped} account={account} />
          ) : null}

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
