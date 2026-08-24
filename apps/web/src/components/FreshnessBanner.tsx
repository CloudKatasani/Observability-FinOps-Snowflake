import type { Provenance, SourceSummary } from "@/api/client";
import { ProvisionalBadge } from "@/components/badges";
import { summariseFreshness } from "@/lib/freshness";

interface FreshnessBannerProps {
  /** Every provenance block feeding the page; undefined entries are ignored. */
  contributions: readonly (Provenance | undefined | null)[];
  sources?: readonly SourceSummary[];
}

/**
 * R7 on every page: state the freshness floor of the slowest contributing
 * source, by name, with the time the figures were computed.
 */
export default function FreshnessBanner({ contributions, sources }: FreshnessBannerProps) {
  const summary = summariseFreshness(contributions, sources);

  return (
    <div
      aria-live="polite"
      className="flex flex-wrap items-center gap-x-3 gap-y-1 border-b border-slate-200 bg-slate-50 px-4 py-1.5 text-xs text-slate-600"
    >
      <span aria-hidden className="text-slate-400">
        ◷
      </span>
      <span className="tabular-nums">{summary.text}</span>
      {summary.provisional ? (
        <span className="flex items-center gap-1.5">
          <ProvisionalBadge />
          <span>figures on this page may restate</span>
        </span>
      ) : null}
    </div>
  );
}
