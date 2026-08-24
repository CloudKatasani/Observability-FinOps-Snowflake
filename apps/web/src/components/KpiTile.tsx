import type { UseQueryResult } from "@tanstack/react-query";

import type { MetricTile as MetricTilePayload, SourceSummary } from "@/api/client";
import ProvenanceBar from "@/components/Provenance";
import { ProvisionalBadge } from "@/components/badges";
import { EmptyState, ErrorState, LoadingRegion } from "@/components/states";
import { formatFigure, unitLabel } from "@/lib/format";

interface KpiTileProps {
  /** The tile query, owned by the page so its provenance can feed the banner. */
  query: UseQueryResult<MetricTilePayload>;
  /** Overrides the metric's catalogue name where the dashboard wants shorter wording. */
  label?: string;
  registry?: Map<string, SourceSummary>;
}

export default function KpiTile({ query, label, registry }: KpiTileProps) {
  const heading = label ?? query.data?.name ?? "Figure";

  return (
    <article className="flex flex-col rounded border border-slate-200 bg-white p-4">
      <h3 className="flex flex-wrap items-center gap-2 text-[11px] font-semibold tracking-wider text-slate-500 uppercase">
        {heading}
        {query.data?.provisional ? <ProvisionalBadge /> : null}
      </h3>

      <div className="mt-2 flex-1">
        {query.isPending ? (
          <LoadingRegion label={`Loading ${heading}`} lines={2} />
        ) : query.isError ? (
          <ErrorState
            title="Figure unavailable"
            error={query.error}
            remediation="The metrics API did not answer. Check that the API service is running, then retry."
            onRetry={() => void query.refetch()}
          />
        ) : query.data.unavailable_reason ? (
          // R3: a missing source explains itself. It never renders as 0.
          <EmptyState reason={query.data.unavailable_reason} />
        ) : (
          <TileValue
            value={formatFigure(
              query.data.value,
              query.data.format_type,
              query.data.format_decimals,
              query.data.unit,
            )}
            unit={unitLabel(query.data.format_type, query.data.unit)}
          />
        )}
      </div>

      {query.data && !query.data.unavailable_reason ? (
        <ProvenanceBar
          provenance={query.data}
          sql={query.data.sql}
          registry={registry}
          label={heading}
        />
      ) : null}
    </article>
  );
}

function TileValue({ value, unit }: { value: string | null; unit: string | null }) {
  if (value === null) {
    return (
      <EmptyState reason="No rows matched this period, so the figure is unknown rather than zero." />
    );
  }
  return (
    <p className="flex items-baseline gap-1.5">
      <span className="text-2xl leading-none font-semibold tabular-nums text-slate-900">
        {value}
      </span>
      {unit ? <span className="text-xs text-slate-500">{unit}</span> : null}
    </p>
  );
}
