import type { Reconciliation } from "@/api/client";
import DataTable from "@/components/DataTable";
import { formatClockTime, formatCredits, formatPercentPoints } from "@/lib/format";

interface ReconciliationBannerProps {
  reconciliation: Reconciliation;
  /** The gate's verdict, taken from `figures_published` — not inferred. */
  published: boolean;
}

/**
 * R6 made visible. Allocated cost either reconciles to the metered bill or it
 * does not publish; this banner states which, in words, before any team figure
 * appears on the page. When the gate is red it is the page's alert, and the
 * team table is replaced by it.
 */
export default function ReconciliationBanner({
  reconciliation,
  published,
}: ReconciliationBannerProps) {
  const tone = published
    ? "border-emerald-300 bg-emerald-50 text-emerald-950"
    : "border-red-400 bg-red-50 text-red-950";

  const worstDays = reconciliation.worst_days.filter((day) => day.usage_day);

  return (
    <section
      role={published ? undefined : "alert"}
      aria-label="Reconciliation gate"
      className={`rounded border-2 p-4 ${tone}`}
    >
      <p className="flex items-center gap-2 text-sm font-semibold">
        <span aria-hidden className="text-base">
          {published ? "✓" : "✕"}
        </span>
        {published
          ? "Reconciled — chargeback figures are published"
          : "Blocked — chargeback figures are withheld"}
      </p>

      <p className="mt-1.5 text-sm">{reconciliation.banner}</p>

      {!published ? (
        <p className="mt-2 text-sm font-medium">
          Allocated cost did not reconcile to the metered bill within tolerance, so no team is
          charged from this run. Correct the allocation inputs for the worst-variance days below,
          re-run the allocation, and publish only once the gate passes.
        </p>
      ) : null}

      <dl className="mt-3 grid grid-cols-2 gap-x-6 gap-y-2 text-sm sm:grid-cols-4">
        <div>
          <dt className="text-[11px] font-semibold tracking-wider uppercase opacity-70">
            Allocated
          </dt>
          <dd className="tabular-nums">
            {formatCredits(reconciliation.allocated_credits, 2) ?? "unknown"} cr
          </dd>
        </div>
        <div>
          <dt className="text-[11px] font-semibold tracking-wider uppercase opacity-70">Metered</dt>
          <dd className="tabular-nums">
            {formatCredits(reconciliation.metered_credits, 2) ?? "unknown"} cr
          </dd>
        </div>
        <div>
          <dt className="text-[11px] font-semibold tracking-wider uppercase opacity-70">
            Variance
          </dt>
          <dd className="tabular-nums">
            {formatCredits(reconciliation.variance_credits, 2) ?? "unknown"} cr
            {reconciliation.variance_pct
              ? ` (${formatPercentPoints(reconciliation.variance_pct, 3)})`
              : ""}
          </dd>
        </div>
        <div>
          <dt className="text-[11px] font-semibold tracking-wider uppercase opacity-70">
            Tolerance
          </dt>
          <dd className="tabular-nums">
            ±{formatPercentPoints(reconciliation.tolerance_pct, 2) ?? "unknown"}
          </dd>
        </div>
      </dl>

      <p className="mt-2 text-[11px] opacity-80">
        Outcome <span className="font-mono">{reconciliation.outcome}</span>, run at{" "}
        <span className="tabular-nums">{formatClockTime(reconciliation.ran_at)}</span>.
      </p>

      {worstDays.length > 0 ? (
        <details className="mt-3" open={!published}>
          <summary className="cursor-pointer text-xs font-medium underline decoration-dotted underline-offset-2 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-current">
            Worst-variance days ({worstDays.length})
          </summary>
          <div className="mt-2 rounded border border-current/20 bg-white/70 p-1">
            <DataTable
              caption="Days with the largest gap between allocated and metered credits"
              dense
              columns={[
                { key: "day", header: "Day" },
                { key: "allocated", header: "Allocated", numeric: true },
                { key: "metered", header: "Metered", numeric: true },
                { key: "variance", header: "Variance", numeric: true },
                { key: "pct", header: "Variance %", numeric: true },
              ]}
              rows={worstDays.map((day) => ({
                day: day.usage_day,
                allocated: formatCredits(day.allocated_credits, 2) ?? "unknown",
                metered: formatCredits(day.metered_credits, 2) ?? "unknown",
                variance: formatCredits(day.variance_credits, 2) ?? "unknown",
                pct: formatPercentPoints(day.variance_pct, 3) ?? "unknown",
              }))}
            />
          </div>
        </details>
      ) : null}
    </section>
  );
}
