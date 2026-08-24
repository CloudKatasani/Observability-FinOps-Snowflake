import type { MetricQueryResponse, SourceCoverage } from "@/api/client";
import { NEUTRAL_PALETTE, categoryChartOption } from "@/charts/theme";
import ChartPanel from "@/components/ChartPanel";
import type { ChartRendering } from "@/components/ChartPanel";
import DataTable from "@/components/DataTable";
import KpiTile from "@/components/KpiTile";
import PageFrame from "@/components/PageFrame";
import Panel from "@/components/Panel";
import ProvenanceBar from "@/components/Provenance";
import { StatusPill } from "@/components/badges";
import { EmptyState, ErrorState, LoadingRegion } from "@/components/states";
import {
  useCoverage,
  useMeta,
  useMetricQuery,
  useMetricTile,
  useSourceIndex,
  useSources,
} from "@/hooks/useApi";
import { useDateRange } from "@/hooks/useDateRange";
import { useScope } from "@/hooks/useScope";
import { divideDecimals, toPlotNumber } from "@/lib/decimal";
import {
  formatBucketLabel,
  formatClockTime,
  formatDecimalPercent,
  formatDecimalValue,
  formatInteger,
  formatMinutes,
  shortObjectName,
} from "@/lib/format";
import { groupTotals, peakByGroup, timeSeries } from "@/lib/series";
import { freshnessVerdict, statusLabel, statusTone } from "@/lib/status";
import { scopeLabel } from "@/store/scope";

const TOP_N = 10;

/** Worst first: missing, then stale, then the freshest landed source. */
function byUrgency(a: SourceCoverage, b: SourceCoverage): number {
  const rank = { missing: 0, empty: 1, stale: 2, available: 3 };
  const difference = rank[a.status] - rank[b.status];
  if (difference !== 0) return difference;
  return (b.freshness_minutes ?? 0) - (a.freshness_minutes ?? 0);
}

export default function HealthPage() {
  const range = useDateRange();
  const scope = useScope();
  const meta = useMeta();
  const sources = useSources();
  const registry = useSourceIndex();
  const coverage = useCoverage();
  const palette = meta.data?.branding.palette ?? NEUTRAL_PALETTE;

  const failureRate = useMetricTile("q.failure_rate", range, scope);
  const queryVolume = useMetricTile("q.volume", range, scope);
  const queueOverload = useMetricTile("wh.queue_overload_pct", range, scope);

  const failureTrend = useMetricQuery("health-failure-trend", {
    metrics: ["q.failure_rate"],
    start: range.start,
    end: range.end,
    grain: "day",
    limit: 400,
    order: [{ field: "TIME_BUCKET", descending: false }],
    ...scope,
  });

  const queueByWarehouse = useMetricQuery("health-queue", {
    metrics: ["wh.queue_overload_pct"],
    dimensions: ["warehouse"],
    start: range.start,
    end: range.end,
    grain: "month",
    limit: 5000,
    ...scope,
  });

  // Utilisation and idle share are ratios, so they cannot be added across
  // periods. Their numerators and denominators can: summing the credit metrics
  // and dividing reproduces the metric's own definition exactly.
  const warehouseCredits = useMetricQuery("health-warehouse-credits", {
    metrics: ["cost.attributed_credits", "cost.idle_credits", "cost.by_warehouse_credits"],
    dimensions: ["warehouse"],
    start: range.start,
    end: range.end,
    grain: "month",
    limit: 5000,
    ...scope,
  });

  const contributions = [
    failureRate.data,
    queryVolume.data,
    queueOverload.data,
    failureTrend.data,
    queueByWarehouse.data,
    warehouseCredits.data,
  ];

  const renderFailureTrend = (response: MetricQueryResponse): ChartRendering => {
    const points = timeSeries(response, "q.failure_rate");
    const labels = points.map((point) => formatBucketLabel(point.bucket, "day"));
    const text = points.map((point) =>
      point.value ? formatDecimalPercent(point.value, 2) : "unknown",
    );

    return {
      ariaLabel: `Daily query failure rate from ${range.start} to ${range.end}`,
      option: categoryChartOption({
        palette,
        categories: labels,
        kind: "line",
        series: [
          {
            name: "Failure rate",
            values: points.map((point) => (point.value ? toPlotNumber(point.value) : null)),
          },
        ],
        valueLabel: (_seriesName, index) => text[index] ?? "unknown",
      }),
      table: {
        columns: [
          { key: "day", header: "Day" },
          { key: "rate", header: "Failure rate", numeric: true },
        ],
        rows: labels.map((label, index) => ({ day: label, rate: text[index] })),
      },
    };
  };

  const renderQueue = (response: MetricQueryResponse): ChartRendering => {
    const peaks = peakByGroup(response, "warehouse", "wh.queue_overload_pct", { limit: TOP_N });
    const ascending = [...peaks].reverse();

    return {
      ariaLabel: "Worst monthly queue-overload share by warehouse",
      option: categoryChartOption({
        palette,
        horizontal: true,
        categories: ascending.map((entry) => entry.key),
        series: [
          { name: "Queue overload", values: ascending.map((entry) => toPlotNumber(entry.total)) },
        ],
        valueLabel: (_seriesName, index) => formatDecimalPercent(ascending[index].total, 2),
      }),
      table: {
        columns: [
          { key: "warehouse", header: "Warehouse" },
          { key: "share", header: "Worst monthly share", numeric: true },
        ],
        rows: peaks.map((entry) => ({
          warehouse: entry.key,
          share: formatDecimalPercent(entry.total, 2),
        })),
      },
    };
  };

  const utilisationRows = (() => {
    if (!warehouseCredits.data) return [];
    const compute = groupTotals(warehouseCredits.data, "warehouse", "cost.by_warehouse_credits");
    const attributed = new Map(
      groupTotals(warehouseCredits.data, "warehouse", "cost.attributed_credits").map((entry) => [
        entry.key,
        entry.total,
      ]),
    );
    const idle = new Map(
      groupTotals(warehouseCredits.data, "warehouse", "cost.idle_credits").map((entry) => [
        entry.key,
        entry.total,
      ]),
    );

    return compute.slice(0, TOP_N).map((entry) => {
      const attributedTotal = attributed.get(entry.key);
      const idleTotal = idle.get(entry.key);
      const utilisation = attributedTotal
        ? divideDecimals(attributedTotal, entry.total, 6)
        : null;
      const idleShare = idleTotal ? divideDecimals(idleTotal, entry.total, 6) : null;
      return {
        warehouse: entry.key,
        compute: entry.total,
        utilisation,
        idleShare,
      };
    });
  })();

  const staleSources = [...(coverage.data?.sources ?? [])].sort(byUrgency);

  return (
    <PageFrame
      title="Platform health"
      description={`Whether the telemetry is arriving on time, whether queries are succeeding, and where warehouses are contended, at ${scopeLabel(scope)} scope.`}
      contributions={contributions}
      sources={sources.data}
    >
      <section aria-label="Health figures" className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
        <KpiTile query={failureRate} label="Query failure rate" registry={registry} />
        <KpiTile query={queryVolume} label="Query volume" registry={registry} />
        <KpiTile query={queueOverload} label="Queue overload share" registry={registry} />
      </section>

      <Panel
        title="Source freshness"
        subtitle={
          coverage.data
            ? `${coverage.data.sources.length} registered sources, assessed at ${formatClockTime(coverage.data.as_of)} in ${coverage.data.mode} mode`
            : "Per-source freshness against documented latency"
        }
      >
        {coverage.isPending ? (
          <LoadingRegion label="Loading source coverage" lines={8} />
        ) : coverage.isError ? (
          <ErrorState
            title="Coverage unavailable"
            error={coverage.error}
            remediation="The coverage endpoint did not answer. Check that the API service is running, then retry."
            onRetry={() => void coverage.refetch()}
          />
        ) : staleSources.length === 0 ? (
          <EmptyState reason="The source registry returned no entries, so freshness cannot be assessed." />
        ) : (
          <div className="max-h-96 overflow-auto rounded border border-slate-200">
            <DataTable
              caption="Freshness of every registered source against its documented latency"
              dense
              columns={[
                { key: "source", header: "Source" },
                { key: "domain", header: "Domain" },
                { key: "status", header: "Status" },
                { key: "observed", header: "Observed age" },
                { key: "documented", header: "Documented latency", numeric: true },
                { key: "rows", header: "Rows", numeric: true },
              ]}
              rows={staleSources.map((source) => {
                const verdict = freshnessVerdict(source);
                return {
                  source: (
                    <span className="font-mono text-xs">
                      {shortObjectName(source.snowflake_object)}
                    </span>
                  ),
                  domain: source.domain,
                  status: <StatusPill tone={statusTone(source.status)} label={statusLabel(source.status)} />,
                  observed: (
                    <span title={verdict.detail}>
                      <StatusPill tone={verdict.tone} label={verdict.label} />
                    </span>
                  ),
                  documented: formatMinutes(source.documented_latency_minutes),
                  rows: formatInteger(source.rows) ?? "0",
                };
              })}
            />
          </div>
        )}
      </Panel>

      <ChartPanel
        title="Query failure rate"
        subtitle="Share of queries that did not complete successfully, by day"
        query={failureTrend}
        registry={registry}
        height={220}
        emptyReason="No query history landed for this period, so the failure rate is unknown rather than zero."
        render={renderFailureTrend}
      />

      <div className="grid gap-4 xl:grid-cols-2">
        <ChartPanel
          title={`Queue overload by warehouse — top ${TOP_N}`}
          subtitle="Worst monthly share of elapsed time spent queued behind a saturated warehouse"
          query={queueByWarehouse}
          registry={registry}
          height={260}
          emptyReason="No query history landed for this period, so queueing cannot be measured."
          render={renderQueue}
        />

        <Panel
          title={`Warehouse utilisation — top ${TOP_N} by credits`}
          subtitle="Attributed and idle credits as a share of metered compute, over the whole period"
          provisional={warehouseCredits.data?.provisional ?? false}
        >
          {warehouseCredits.isPending ? (
            <LoadingRegion label="Loading warehouse utilisation" lines={6} />
          ) : warehouseCredits.isError ? (
            <ErrorState
              title="Utilisation unavailable"
              error={warehouseCredits.error}
              remediation="The metrics API did not answer. Check that the API service is running, then retry."
              onRetry={() => void warehouseCredits.refetch()}
            />
          ) : utilisationRows.length === 0 ? (
            <EmptyState reason="No warehouse metering rows landed for this period. WAREHOUSE_METERING_HISTORY and QUERY_ATTRIBUTION_HISTORY are both required." />
          ) : (
            <DataTable
              caption="Warehouse utilisation and idle share for the selected period"
              dense
              columns={[
                { key: "warehouse", header: "Warehouse" },
                { key: "compute", header: "Compute credits", numeric: true },
                { key: "utilisation", header: "Utilisation", numeric: true },
                { key: "idle", header: "Idle share", numeric: true },
              ]}
              rows={utilisationRows.map((row) => ({
                warehouse: <span className="font-mono text-xs">{row.warehouse}</span>,
                compute: formatDecimalValue(row.compute, 1),
                utilisation: row.utilisation ? formatDecimalPercent(row.utilisation, 1) : "unknown",
                idle: row.idleShare ? formatDecimalPercent(row.idleShare, 1) : "unknown",
              }))}
            />
          )}
          {warehouseCredits.data ? (
            <ProvenanceBar
              provenance={warehouseCredits.data}
              sql={warehouseCredits.data.sql}
              registry={registry}
              label="Warehouse utilisation"
              scope={warehouseCredits.data}
            />
          ) : null}
        </Panel>
      </div>
    </PageFrame>
  );
}
