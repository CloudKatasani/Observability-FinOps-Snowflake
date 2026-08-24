// Turning coverage state into words. Status is never carried by colour alone,
// so every tone below is paired with a label the reader can act on (§16.2).

import type { SourceCoverage, SourceStatus } from "@/api/client";
import { formatMinutes } from "@/lib/format";

export type Tone = "good" | "warn" | "bad" | "info" | "muted";

const STATUS_TONES: Record<SourceStatus, Tone> = {
  available: "good",
  stale: "warn",
  empty: "warn",
  missing: "bad",
};

const STATUS_LABELS: Record<SourceStatus, string> = {
  available: "Available",
  stale: "Stale",
  empty: "Empty",
  missing: "Missing",
};

export function statusTone(status: SourceStatus): Tone {
  return STATUS_TONES[status];
}

export function statusLabel(status: SourceStatus): string {
  return STATUS_LABELS[status];
}

export interface FreshnessVerdict {
  tone: Tone;
  /** "4h behind" / "within latency" / "never landed". */
  label: string;
  /** The comparison spelled out: observed age against the documented floor. */
  detail: string;
}

/**
 * Compare a source's observed age against the latency its documentation
 * promises. The documented figure comes from the API's source registry — R7
 * forbids writing latencies down in application code.
 */
export function freshnessVerdict(source: SourceCoverage): FreshnessVerdict {
  const documented = formatMinutes(source.documented_latency_minutes);

  if (source.status === "missing") {
    return {
      tone: "bad",
      label: "Never landed",
      detail: `No rows have been loaded. Documented latency is ${documented}.`,
    };
  }
  if (source.status === "empty") {
    return {
      tone: "warn",
      label: "No usable rows",
      detail: `The extract landed but held no rows. Documented latency is ${documented}.`,
    };
  }
  if (source.freshness_minutes === null || source.freshness_minutes === undefined) {
    return {
      tone: "info",
      label: "Not time-stamped",
      detail: `A snapshot source has no time column, so it cannot go stale. Documented latency is ${documented}.`,
    };
  }

  const observed = formatMinutes(source.freshness_minutes);
  const withinBudget = source.status !== "stale";
  return {
    tone: withinBudget ? "good" : "warn",
    label: withinBudget ? `${observed} old` : `${observed} old — beyond budget`,
    detail: withinBudget
      ? `Newest row is ${observed} old, inside the ${documented} documented latency plus a day of extract age.`
      : `Newest row is ${observed} old, past the ${documented} documented latency plus a day of extract age.`,
  };
}

/** How many sources sit in each state — the headline of the coverage page. */
export function countByStatus(sources: readonly SourceCoverage[]): Record<SourceStatus, number> {
  const counts: Record<SourceStatus, number> = {
    available: 0,
    stale: 0,
    empty: 0,
    missing: 0,
  };
  for (const source of sources) counts[source.status] += 1;
  return counts;
}
