import { NEUTRAL_PALETTE, categoryChartOption } from "@/charts/theme";
import Chart from "@/charts/Chart";
import DataTable from "@/components/DataTable";
import PageFrame from "@/components/PageFrame";
import Panel from "@/components/Panel";
import ProvenanceBar from "@/components/Provenance";
import ReconciliationBanner from "@/components/ReconciliationBanner";
import { ErrorState, LoadingRegion } from "@/components/states";
import type { SqlDisclosure, TeamCost } from "@/api/client";
import { useAllocation, useMeta, useSourceIndex, useSources } from "@/hooks/useApi";
import { useDateRange } from "@/hooks/useDateRange";
import { useScope } from "@/hooks/useScope";
import { parseDecimal, toPlotNumber } from "@/lib/decimal";
import { formatCredits, formatFigure } from "@/lib/format";

/**
 * The allocation is a composite of several metric queries, so "show the SQL"
 * shows all of them, each labelled with the part of the waterfall it feeds.
 * One concatenated blob would be traceable in principle and useless in
 * practice — a reader checking the idle apportionment needs to know which of
 * the statements produced it.
 */
function allocationSql(disclosures: readonly SqlDisclosure[]): string {
  return disclosures
    .map((disclosure) => {
      const sliced =
        disclosure.dimensions.length > 0 ? ` by ${disclosure.dimensions.join(", ")}` : "";
      return [
        `-- ${disclosure.purpose}`,
        `-- ${disclosure.metrics.join(", ")}${sliced}`,
        disclosure.sql,
      ].join("\n");
    })
    .join("\n\n");
}

/** The three components the allocation waterfall produces (HLD §10.2). */
const COMPONENTS: { label: string; pick: (team: TeamCost) => string }[] = [
  { label: "Direct", pick: (team) => team.direct_credits },
  { label: "Idle", pick: (team) => team.idle_credits },
  { label: "Cloud services", pick: (team) => team.cloud_services_credits },
];

export default function ChargebackPage() {
  const range = useDateRange();
  const scope = useScope();
  const meta = useMeta();
  const sources = useSources();
  const registry = useSourceIndex();
  const allocation = useAllocation(range);
  const palette = meta.data?.branding.palette ?? NEUTRAL_PALETTE;

  const data = allocation.data;
  // R6: the gate's verdict decides whether any team figure is rendered. It is
  // never inferred from the shape of the payload — an empty table and a blocked
  // gate are different states, and a populated table behind a blocked gate must
  // still show nothing.
  const published = data?.figures_published === true;
  const teams = published ? (data?.teams ?? []) : [];
  // A horizontal bar axis reads bottom-up, so the largest team sits at the top
  // only once the order is reversed.
  const chartTeams = [...teams].reverse();

  return (
    <PageFrame
      title="Team chargeback"
      description={`Fully allocated cost by team for ${range.start} to ${range.end}, allocated across every landed account and published only behind the reconciliation gate.`}
      contributions={[data]}
      sources={sources.data}
    >
      {scope.scope === "account" ? <OrganizationOnlyNotice account={scope.account} /> : null}

      {allocation.isPending ? (
        <Panel title="Reconciliation gate">
          <LoadingRegion label="Running the allocation and its reconciliation" lines={4} />
        </Panel>
      ) : allocation.isError ? (
        <ErrorState
          title="Chargeback unavailable"
          error={allocation.error}
          remediation="The chargeback endpoint did not answer. Check that the API service is running and that WAREHOUSE_METERING_HISTORY and QUERY_ATTRIBUTION_HISTORY have been loaded, then retry."
          onRetry={() => void allocation.refetch()}
        />
      ) : !data ? (
        <Panel title="Reconciliation gate">
          <p className="rounded border border-dashed border-slate-300 bg-slate-50 p-4 text-sm text-slate-700">
            The allocation endpoint returned no body, so neither the gate's verdict nor any team
            figure can be shown.
          </p>
        </Panel>
      ) : (
        <>
          <ReconciliationBanner reconciliation={data.reconciliation} published={published} />

          <div className="grid gap-4 xl:grid-cols-[2fr_1fr]">
            <Panel
              title="Allocated cost by team"
              subtitle={
                published
                  ? `${data.mode} mode · direct, idle, and cloud-services components`
                  : "Withheld until the allocation reconciles"
              }
              provisional={data.provisional}
            >
              {published ? (
                <DataTable
                  caption="Allocated credits by team, by component"
                  columns={[
                    { key: "team", header: "Team" },
                    { key: "direct", header: "Direct", numeric: true },
                    { key: "idle", header: "Idle", numeric: true },
                    { key: "cloud", header: "Cloud services", numeric: true },
                    { key: "total", header: "Total credits", numeric: true },
                    { key: "cost", header: "Cost", numeric: true },
                    { key: "share", header: "Share", numeric: true },
                  ]}
                  emptyReason="The gate passed, but the allocation returned no teams for this period — no attributable usage was found."
                  rows={teams.map((team) => ({
                    team: <span className="font-medium">{team.team}</span>,
                    direct: formatCredits(team.direct_credits, 1) ?? "unknown",
                    idle: formatCredits(team.idle_credits, 1) ?? "unknown",
                    cloud: formatCredits(team.cloud_services_credits, 1) ?? "unknown",
                    total: formatCredits(team.total_credits, 1) ?? "unknown",
                    cost:
                      formatFigure(team.cost_usd, "currency", 2, "USD") ??
                      "no credit price configured",
                    share: formatFigure(team.share_of_total, "percent", 1) ?? "unknown",
                  }))}
                />
              ) : (
                <p className="rounded border border-dashed border-red-300 bg-red-50 p-4 text-sm text-red-950">
                  No team figures are shown. The reconciliation gate above did not pass, and R6
                  forbids publishing allocated cost that does not reconcile to the metered bill.
                  Resolve the variance and re-run the allocation.
                </p>
              )}
              <ProvenanceBar
                provenance={data}
                sql={allocationSql(data.sql)}
                registry={registry}
                label="Allocated cost by team"
              />
            </Panel>

            <div className="space-y-4">
              <Panel
                title="Unattributed share"
                subtitle="Credits with no team tag, before the waterfall's fallback rules"
              >
                <p className="flex items-baseline gap-1.5">
                  <span className="text-2xl leading-none font-semibold tabular-nums text-slate-900">
                    {formatFigure(data.unattributed_share, "percent", 1) ?? "unknown"}
                  </span>
                </p>
                <p className="mt-2 text-xs text-slate-600">
                  Credit price:{" "}
                  <span className="tabular-nums">
                    {data.credit_price_usd
                      ? (formatFigure(data.credit_price_usd, "currency", 4, "USD") ?? "unknown")
                      : "not configured — costs are shown in credits only"}
                  </span>
                </p>
              </Panel>

              <Panel
                title="Component mix"
                subtitle={
                  published
                    ? "Direct, idle, and cloud-services credits per team"
                    : "Withheld until the allocation reconciles"
                }
              >
                {published && chartTeams.length > 0 ? (
                  <Chart
                    height={Math.max(200, chartTeams.length * 30)}
                    ariaLabel="Allocated credits per team, split into direct, idle, and cloud-services components. The same figures appear in the table to the left."
                    option={categoryChartOption({
                      palette,
                      horizontal: true,
                      stacked: true,
                      categories: chartTeams.map((team) => team.team),
                      series: COMPONENTS.map((component) => ({
                        name: component.label,
                        values: chartTeams.map((team) => plot(component.pick(team))),
                      })),
                      valueLabel: (seriesName, index) => {
                        const team = chartTeams[index];
                        const component = COMPONENTS.find((entry) => entry.label === seriesName);
                        if (!team || !component) return "unknown";
                        return formatCredits(component.pick(team), 1) ?? "unknown";
                      },
                    })}
                  />
                ) : (
                  <p className="rounded border border-dashed border-slate-300 bg-slate-50 p-4 text-sm text-slate-700">
                    {published
                      ? "The allocation returned no teams for this period."
                      : "No chart is drawn: the reconciliation gate did not pass, so there are no publishable figures to plot."}
                  </p>
                )}
              </Panel>
            </div>
          </div>
        </>
      )}
    </PageFrame>
  );
}

/**
 * The allocation waterfall runs over the whole tenant: the chargeback endpoint
 * takes no account filter, and the gate reconciles against the account-day
 * metering total for every landed account together.
 *
 * Rendering these figures under an account's name in the scope picker would be
 * exactly the mis-scoping the scope filter exists to prevent — an organization
 * figure wearing an account's label, undetectable downstream. So the page says
 * plainly what it is showing instead, and keeps showing it.
 */
function OrganizationOnlyNotice({ account }: { account: string | null }) {
  return (
    <section
      role="note"
      aria-label="Scope notice"
      className="rounded border border-amber-300 bg-amber-50 p-3 text-sm text-amber-950"
    >
      <p className="font-semibold">
        <span aria-hidden className="mr-1.5 font-bold">
          !
        </span>
        These figures are organization-wide, not {account ?? "this account"}&rsquo;s.
      </p>
      <p className="mt-1">
        The allocation waterfall and its reconciliation gate run over every landed account
        together, so the chargeback endpoint cannot be narrowed to one account. Nothing below is
        filtered to {account ?? "the selected account"}. Switch the scope filter back to
        Organization to read this page as it is computed.
      </p>
    </section>
  );
}

/** Chart plotting only — the readable figure always comes from the string. */
function plot(value: string): number | null {
  const parsed = parseDecimal(value);
  return parsed ? toPlotNumber(parsed) : null;
}
