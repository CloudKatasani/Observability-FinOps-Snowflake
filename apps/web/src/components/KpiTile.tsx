import type { SourceSummary } from "@/api/client";
import ProvenanceBar from "@/components/Provenance";
import { ProvisionalBadge } from "@/components/badges";
import { EmptyState, ErrorState, LoadingRegion } from "@/components/states";
import { useMetricTile } from "@/hooks/useApi";
import { formatFigure, unitLabel } from "@/lib/format";
import type { DateRange } from "@/store/timeRange";

interface KpiTileProps {
  metricId: string;
  /** Overrides the metric's catalogue name when the dashboard wants shorter wording. */
  label?: string;
  range: DateRange;
  registry?: Map<string, SourceSummary>;
}

export default function KpiTile({ metricId, label, range, registry }: KpiTileProps) {
  const tile = useMetricTile(metricId, range);
  const heading = label ?? tile.data?.name ?? metricId;

  return (
    <article className="flex flex-col rounded border border-slate-200 bg-white p-4">
      <h3 className="flex items-center gap-2 text-[11px] font-semibold tracking-wider text-slate-500 uppercase">
        {heading}
        {tile.data?.provisional ? <ProvisionalBadge /> : null}
      </h3>

      <div className="mt-2 flex-1">
        {tile.isPending ? (
          <LoadingRegion label={`Loading ${heading}`} lines={2} />
        ) : tile.isError ? (
          <ErrorState
            title="Figure unavailable"
            error={tile.error}
            remediation="The metrics API did not answer. Check that the API service is running, then retry."
            onRetry={() => void tile.refetch()}
          />
        ) : tile.data.unavailable_reason ? (
          // R3: a missing source explains itself. It never renders as 0.
          <EmptyState reason={tile.data.unavailable_reason} />
        ) : (
          <TileValue
            value={formatFigure(
              tile.data.value,
              tile.data.format_type,
              tile.data.format_decimals,
              tile.data.unit,
            )}
            unit={unitLabel(tile.data.format_type, tile.data.unit)}
          />
        )}
      </div>

      {tile.data && !tile.data.unavailable_reason ? (
        <ProvenanceBar
          provenance={tile.data}
          sql={tile.data.sql}
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
