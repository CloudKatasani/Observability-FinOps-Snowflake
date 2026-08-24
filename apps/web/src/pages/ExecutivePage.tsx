import { useMemo } from "react";

import type { MetricQueryResponse } from "@/api/client";
import { NEUTRAL_PALETTE, categoryChartOption } from "@/charts/theme";
import ChartPanel from "@/components/ChartPanel";
import type { ChartRendering } from "@/components/ChartPanel";
import DataTable from "@/components/DataTable";
import KpiTile from "@/components/KpiTile";
import PageFrame from "@/components/PageFrame";
import Panel from "@/components/Panel";
import ProvenanceBar from "@/components/Provenance";
import { EmptyState, ErrorState, LoadingRegion } from "@/components/states";
import { useMeta, useMetricQuery, useMetricTile, useSourceIndex, useSources } from "@/hooks/useApi";
import { useDateRange } from "@/hooks/useDateRange";
import { useScope } from "@/hooks/useScope";
import type { Decimal } from "@/lib/decimal";
import { addAll, divideDecimals, toPlotNumber } from "@/lib/decimal";
import { formatBucketLabel, formatDecimalPercent, formatDecimalValue } from "@/lib/format";
import { alignedTimeSeries, groupTotals } from "@/lib/series";
import { scopeLabel } from "@/store/scope";
import { trailingMonths } from "@/store/timeRange";

const TREND_MONTHS = 13;
const TOP_N = 10;

const TREND_METRICS = ["cost.total_credits", "cost.billed_credits"] as const;
const SERIES_NAMES: Record<string, string> = {
  "cost.total_credits": "Total credits",
  "cost.billed_credits": "Billed credits",
};

/** A group's share of its total, divided exactly rather than through floats. */
function shareOf(part: Decimal, whole: Decimal): string {
  const ratio = divideDecimals(part, whole, 6);
  return ratio ? formatDecimalPercent(ratio, 1) : "—";
}

export default function ExecutivePage() {
  const range = useDateRange();
  const scope = useScope();
  const meta = useMeta();
  const sources = useSources();
  const registry = useSourceIndex();
  const palette = meta.data?.branding.palette ?? NEUTRAL_PALETTE;

  const totalCredits = useMetricTile("cost.total_credits", range, scope);
  const billedCredits = useMetricTile("cost.billed_credits", range, scope);
  const spend = useMetricTile("cost.spend_usd", range, scope);
  const unattributed = useMetricTile("cost.unattributed_share", range, scope);
  const idleShare = useMetricTile("wh.idle_pct", range, scope);
  const costPerQuery = useMetricTile("cost.per_query", range, scope);

  // The trend is defined over 13 months (§16.1) and ends with the selected
  // period, so the global range still moves it.
  const trendRange = useMemo(() => trailingMonths(range.end, TREND_MONTHS), [range.end]);
  const trend = useMetricQuery("executive-trend", {
    metrics: [...TREND_METRICS],
    start: trendRange.start,
    end: trendRange.end,
    grain: "month",
    limit: 200,
    order: [{ field: "TIME_BUCKET", descending: false }],
    ...scope,
  });

  const byServiceType = useMetricQuery("executive-service-type", {
    metrics: ["cost.total_credits"],
    dimensions: ["service_type"],
    start: range.start,
    end: range.end,
    grain: "month",
    limit: 2000,
    ...scope,
  });

  const byWarehouse = useMetricQuery("executive-warehouse", {
    metrics: ["cost.by_warehouse_credits"],
    dimensions: ["warehouse"],
    start: range.start,
    end: range.end,
    grain: "month",
    limit: 5000,
    ...scope,
  });

  const offenders = useMetricQuery("executive-offenders", {
    metrics: ["q.offender_credits"],
    dimensions: ["fingerprint"],
    start: range.start,
    end: range.end,
    grain: "month",
    limit: 5000,
    ...scope,
  });

  const contributions = [
    totalCredits.data,
    billedCredits.data,
    spend.data,
    unattributed.data,
    idleShare.data,
    costPerQuery.data,
    trend.data,
    byServiceType.data,
    byWarehouse.data,
    offenders.data,
  ];

  const renderTrend = (response: MetricQueryResponse): ChartRendering => {
    const aligned = alignedTimeSeries(response, TREND_METRICS);
    const labels = aligned.buckets.map((bucket) => formatBucketLabel(bucket, "month"));
    const text: Record<string, string[]> = {};
    for (const id of TREND_METRICS) {
      text[SERIES_NAMES[id]] = aligned.values[id].map((value) =>
        value ? formatDecimalValue(value, 1) : "unknown",
      );
    }

    return {
      ariaLabel: `Monthly credits over the ${TREND_MONTHS} months to ${trendRange.end}`,
      option: categoryChartOption({
        palette,
        categories: labels,
        kind: "line",
        series: TREND_METRICS.map((id) => ({
          name: SERIES_NAMES[id],
          values: aligned.values[id].map((value) => (value ? toPlotNumber(value) : null)),
        })),
        valueLabel: (seriesName, index) => text[seriesName]?.[index] ?? "unknown",
      }),
      table: {
        columns: [
          { key: "period", header: "Period" },
          { key: "total", header: "Total credits", numeric: true },
          { key: "billed", header: "Billed credits", numeric: true },
        ],
        rows: labels.map((label, index) => ({
          period: label,
          total: text["Total credits"][index],
          billed: text["Billed credits"][index],
        })),
      },
    };
  };

  const renderServiceType = (response: MetricQueryResponse): ChartRendering => {
    const totals = groupTotals(response, "service_type", "cost.total_credits");
    const grand = addAll(totals.map((entry) => entry.total));
    // ECharts draws a horizontal bar axis bottom-up, so the largest bar sits at
    // the top only if the categories are reversed.
    const ascending = [...totals].reverse();

    return {
      ariaLabel: "Credits by Snowflake service type for the selected period",
      option: categoryChartOption({
        palette,
        horizontal: true,
        categories: ascending.map((entry) => entry.key),
        series: [{ name: "Credits", values: ascending.map((entry) => toPlotNumber(entry.total)) }],
        valueLabel: (_seriesName, index) => formatDecimalValue(ascending[index].total, 1),
      }),
      table: {
        columns: [
          { key: "service", header: "Service type" },
          { key: "credits", header: "Credits", numeric: true },
          { key: "share", header: "Share", numeric: true },
        ],
        rows: totals.map((entry) => ({
          service: entry.key,
          credits: formatDecimalValue(entry.total, 1),
          share: shareOf(entry.total, grand),
        })),
      },
    };
  };

  const allWarehouses = byWarehouse.data
    ? groupTotals(byWarehouse.data, "warehouse", "cost.by_warehouse_credits")
    : [];
  const warehouseGrand = addAll(allWarehouses.map((entry) => entry.total));

  const allOffenders = offenders.data
    ? groupTotals(offenders.data, "fingerprint", "q.offender_credits")
    : [];
  const offenderGrand = addAll(allOffenders.map((entry) => entry.total));

  return (
    <PageFrame
      title="Executive cost dashboard"
      description={`Where the Snowflake bill went at ${scopeLabel(scope)} scope between ${range.start} and ${range.end}, and how much of it is attributable.`}
      contributions={contributions}
      sources={sources.data}
    >
      <section aria-label="Key figures" className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
        <KpiTile query={totalCredits} label="Total credits" registry={registry} />
        <KpiTile query={billedCredits} label="Billed credits" registry={registry} />
        <KpiTile query={spend} label="Spend" registry={registry} />
        <KpiTile query={unattributed} label="Unattributed spend" registry={registry} />
        <KpiTile query={idleShare} label="Idle credit share" registry={registry} />
        <KpiTile query={costPerQuery} label="Cost per query" registry={registry} />
      </section>

      <ChartPanel
        title={`${TREND_MONTHS}-month credit trend`}
        subtitle={`${trendRange.start} to ${trendRange.end}, by calendar month`}
        query={trend}
        registry={registry}
        height={260}
        emptyReason={`No metering rows landed in the ${TREND_MONTHS} months to ${trendRange.end}. Load METERING_DAILY_HISTORY to populate the trend.`}
        render={renderTrend}
      />

      <div className="grid gap-4 xl:grid-cols-2">
        <ChartPanel
          title="Cost by service type"
          subtitle="Total credits for the selected period"
          query={byServiceType}
          registry={registry}
          height={260}
          emptyReason="No metering rows landed for this period, so the service-type split is unknown rather than empty."
          render={renderServiceType}
        />

        <Panel
          title={`Cost by warehouse — top ${TOP_N}`}
          subtitle="Metered compute credits for the selected period"
          provisional={byWarehouse.data?.provisional ?? false}
        >
          {byWarehouse.isPending ? (
            <LoadingRegion label="Loading warehouse costs" lines={6} />
          ) : byWarehouse.isError ? (
            <ErrorState
              title="Warehouse costs unavailable"
              error={byWarehouse.error}
              remediation="The metrics API did not answer. Check that the API service is running, then retry."
              onRetry={() => void byWarehouse.refetch()}
            />
          ) : allWarehouses.length === 0 ? (
            <EmptyState reason="No warehouse metering rows landed for this period. Load WAREHOUSE_METERING_HISTORY to rank warehouses by cost." />
          ) : (
            <DataTable
              caption={`Top ${TOP_N} warehouses by metered credits`}
              columns={[
                { key: "warehouse", header: "Warehouse" },
                { key: "credits", header: "Credits", numeric: true },
                { key: "share", header: "Share", numeric: true },
              ]}
              rows={allWarehouses.slice(0, TOP_N).map((entry) => ({
                warehouse: <span className="font-mono text-xs">{entry.key}</span>,
                credits: formatDecimalValue(entry.total, 1),
                share: shareOf(entry.total, warehouseGrand),
              }))}
            />
          )}
          {byWarehouse.data ? (
            <ProvenanceBar
              provenance={byWarehouse.data}
              sql={byWarehouse.data.sql}
              registry={registry}
              label="Cost by warehouse"
              scope={byWarehouse.data}
            />
          ) : null}
        </Panel>
      </div>

      <Panel
        title={`Top offender fingerprints — top ${TOP_N}`}
        subtitle="Attributed credits by normalised query shape"
        provisional={offenders.data?.provisional ?? false}
      >
        {offenders.isPending ? (
          <LoadingRegion label="Loading offender fingerprints" lines={6} />
        ) : offenders.isError ? (
          <ErrorState
            title="Offender ranking unavailable"
            error={offenders.error}
            remediation="The metrics API did not answer. Check that the API service is running, then retry."
            onRetry={() => void offenders.refetch()}
          />
        ) : allOffenders.length === 0 ? (
          <EmptyState reason="No attributed queries landed for this period. QUERY_HISTORY and QUERY_ATTRIBUTION_HISTORY are both required to rank fingerprints." />
        ) : (
          <DataTable
            caption={`Top ${TOP_N} query fingerprints by attributed credits`}
            columns={[
              { key: "rank", header: "#", numeric: true },
              { key: "fingerprint", header: "Fingerprint" },
              { key: "credits", header: "Attributed credits", numeric: true },
              { key: "share", header: "Share of attributed", numeric: true },
            ]}
            rows={allOffenders.slice(0, TOP_N).map((entry, index) => ({
              rank: index + 1,
              fingerprint: <span className="font-mono text-xs">{entry.key}</span>,
              credits: formatDecimalValue(entry.total, 3),
              share: shareOf(entry.total, offenderGrand),
            }))}
          />
        )}
        {offenders.data ? (
          <ProvenanceBar
            provenance={offenders.data}
            sql={offenders.data.sql}
            registry={registry}
            label="Top offender fingerprints"
            scope={offenders.data}
          />
        ) : null}
      </Panel>
    </PageFrame>
  );
}
