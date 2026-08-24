import type { UseQueryResult } from "@tanstack/react-query";
import type { EChartsOption } from "echarts";
import type { ReactNode } from "react";

import type { MetricQueryResponse, SourceSummary } from "@/api/client";
import Chart from "@/charts/Chart";
import DataTable from "@/components/DataTable";
import type { Column } from "@/components/DataTable";
import Panel from "@/components/Panel";
import ProvenanceBar from "@/components/Provenance";
import { EmptyState, ErrorState, LoadingRegion } from "@/components/states";

export interface ChartRendering {
  option: EChartsOption;
  /** What the chart shows, for readers who cannot see it. */
  ariaLabel: string;
  /** The same figures as text — keyboard reachable and screen-reader legible. */
  table: { columns: Column[]; rows: Record<string, ReactNode>[] };
}

interface ChartPanelProps {
  title: string;
  subtitle?: string;
  query: UseQueryResult<MetricQueryResponse>;
  registry?: Map<string, SourceSummary>;
  /** Why this panel might legitimately have nothing to draw. */
  emptyReason: string;
  /** How to fix an API failure, in the operator's terms. */
  errorRemediation?: string;
  height?: number;
  render: (response: MetricQueryResponse) => ChartRendering;
  className?: string;
}

/**
 * A chart with everything a chart in this product must carry: a provisional
 * badge when the figures may restate, an accessible data table, and the
 * compiled SQL behind it (R5).
 */
export default function ChartPanel({
  title,
  subtitle,
  query,
  registry,
  emptyReason,
  errorRemediation = "The metrics API did not answer. Check that the API service is running, then retry.",
  height = 240,
  render,
  className,
}: ChartPanelProps) {
  const rendering = query.data && query.data.rows.length > 0 ? render(query.data) : null;

  return (
    <Panel
      title={title}
      subtitle={subtitle}
      provisional={query.data?.provisional ?? false}
      className={className}
    >
      {query.isPending ? (
        <LoadingRegion label={`Loading ${title}`} lines={5} />
      ) : query.isError ? (
        <ErrorState
          title="Chart unavailable"
          error={query.error}
          remediation={errorRemediation}
          onRetry={() => void query.refetch()}
        />
      ) : !rendering ? (
        <EmptyState reason={emptyReason} />
      ) : (
        <>
          <Chart option={rendering.option} ariaLabel={rendering.ariaLabel} height={height} />
          <details className="mt-2">
            <summary className="cursor-pointer text-[11px] font-medium text-slate-600 underline decoration-dotted underline-offset-2 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-slate-700">
              Data table
            </summary>
            <div className="mt-2 max-h-72 overflow-auto rounded border border-slate-200">
              <DataTable
                caption={`${title} — underlying figures`}
                columns={rendering.table.columns}
                rows={rendering.table.rows}
                dense
              />
            </div>
          </details>
        </>
      )}

      {query.data ? (
        <ProvenanceBar
          provenance={query.data}
          sql={query.data.sql}
          registry={registry}
          label={title}
        />
      ) : null}
    </Panel>
  );
}
