import type { ProvenanceContribution, SourceSummary } from "@/api/client";
import { ProvisionalBadge } from "@/components/badges";
import { summariseFreshness } from "@/lib/freshness";
import { summariseScope } from "@/lib/scope";

interface FreshnessBannerProps {
  /** Every provenance block feeding the page; undefined entries are ignored. */
  contributions: readonly (ProvenanceContribution | undefined | null)[];
  sources?: readonly SourceSummary[];
}

/**
 * R7 on every page: state the freshness floor of the slowest contributing
 * source, by name, with the time the figures were computed.
 *
 * The same strip carries the scope, because the two qualifications answer the
 * same question — how far to trust the numbers below. A page whose organization
 * figures cover only the accounts landed so far says so here, once and in full,
 * rather than leaving the reader to open a disclosure on each tile.
 */
export default function FreshnessBanner({ contributions, sources }: FreshnessBannerProps) {
  const summary = summariseFreshness(contributions, sources);
  const scope = summariseScope(contributions);

  return (
    <div
      aria-live="polite"
      className="border-b border-slate-200 bg-slate-50 px-4 py-1.5 text-xs text-slate-600"
    >
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
        <span aria-hidden className="text-slate-400">
          ◷
        </span>
        <span className="tabular-nums">{summary.text}</span>
        {scope.label ? (
          <span>
            · scope <span className="font-medium text-slate-800">{scope.label}</span>
          </span>
        ) : null}
        {summary.provisional ? (
          <span className="flex items-center gap-1.5">
            <ProvisionalBadge />
            <span>figures on this page may restate</span>
          </span>
        ) : null}
      </div>

      {scope.warning ? (
        <p className="mt-1 flex items-start gap-1.5 rounded border border-amber-300 bg-amber-50 px-2 py-1 text-amber-900">
          <span aria-hidden className="font-bold">
            !
          </span>
          <span>{scope.warning}</span>
        </p>
      ) : null}
    </div>
  );
}
