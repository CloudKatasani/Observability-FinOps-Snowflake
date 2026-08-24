// The freshness banner (R7): every surface states the staleness floor of its
// slowest contributing source. Latency figures come from the source registry
// the API serves — they are never written down in this codebase.

import type { Provenance, SourceSummary } from "@/api/client";
import { formatClockTime, formatMinutes, shortObjectName } from "@/lib/format";

export interface FreshnessSummary {
  /** Oldest `as_of` among the contributing responses. */
  asOf: string | null;
  /** Greatest latency floor among them, in minutes. */
  latencyFloorMinutes: number | null;
  /** Display name of the source that sets that floor. */
  slowestSource: string | null;
  /** Whether any contributing figure may still restate. */
  provisional: boolean;
  /** The sentence rendered in the banner. */
  text: string;
}

const WAITING = "Waiting for data — no source has answered yet.";

function registryIndex(
  sources: readonly SourceSummary[] | undefined,
): Map<string, SourceSummary> {
  const index = new Map<string, SourceSummary>();
  for (const source of sources ?? []) {
    index.set(source.id, source);
  }
  return index;
}

/**
 * Reduce every provenance block on a page to one honest sentence.
 *
 * The slowest source is chosen by its *documented* latency from the registry,
 * falling back to the order the API listed its sources when a source id is not
 * in the registry (an unregistered source cannot be ranked, so it is named only
 * if nothing else can be).
 */
export function summariseFreshness(
  contributions: readonly (Provenance | undefined | null)[],
  sources?: readonly SourceSummary[],
): FreshnessSummary {
  const present = contributions.filter((entry): entry is Provenance => Boolean(entry));
  if (present.length === 0) {
    return {
      asOf: null,
      latencyFloorMinutes: null,
      slowestSource: null,
      provisional: false,
      text: WAITING,
    };
  }

  const asOf = present
    .map((entry) => entry.as_of)
    .reduce((oldest, current) => (current < oldest ? current : oldest));
  const latencyFloorMinutes = Math.max(...present.map((entry) => entry.latency_floor_minutes));
  const provisional = present.some((entry) => entry.provisional);

  const index = registryIndex(sources);
  const contributingIds = new Set(present.flatMap((entry) => entry.sources));

  let slowestSource: string | null = null;
  let slowestLatency = -1;
  for (const id of contributingIds) {
    const definition = index.get(id);
    const latency = definition?.documented_latency_minutes ?? -1;
    if (latency > slowestLatency) {
      slowestLatency = latency;
      slowestSource = definition ? shortObjectName(definition.snowflake_object) : id.toUpperCase();
    }
  }

  const attribution = slowestSource ? ` (${slowestSource})` : "";
  const text =
    `As of ${formatClockTime(asOf)} · data no fresher than ` +
    `${formatMinutes(latencyFloorMinutes)}${attribution}`;

  return { asOf, latencyFloorMinutes, slowestSource, provisional, text };
}
