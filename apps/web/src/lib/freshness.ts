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
 * Two figures compete for the floor: the `latency_floor_minutes` each response
 * declares, and the documented latency the registry holds for the source views
 * those responses read. They can disagree — a metric may declare a tighter
 * floor than one of its own inputs promises — and when they do, the *larger*
 * wins. Understating staleness would be the one failure R7 exists to prevent.
 *
 * The named source is the one that justifies the floor shown. When the registry
 * has not loaded and the floor cannot be attributed to a specific view, the
 * sentence states the floor without naming a source rather than guessing.
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
  const declaredFloor = Math.max(...present.map((entry) => entry.latency_floor_minutes));
  const provisional = present.some((entry) => entry.provisional);

  const index = registryIndex(sources);
  const contributingIds = [...new Set(present.flatMap((entry) => entry.sources))];

  let registryFloor = Number.NEGATIVE_INFINITY;
  let registrySource: string | null = null;
  for (const id of contributingIds) {
    const definition = index.get(id);
    if (!definition) continue;
    if (definition.documented_latency_minutes > registryFloor) {
      registryFloor = definition.documented_latency_minutes;
      registrySource = shortObjectName(definition.snowflake_object);
    }
  }

  let latencyFloorMinutes = declaredFloor;
  let slowestSource: string | null = null;
  if (registrySource !== null && registryFloor >= declaredFloor) {
    latencyFloorMinutes = registryFloor;
    slowestSource = registrySource;
  } else if (contributingIds.length === 1) {
    // One input, so the floor is unambiguously its own even without the registry.
    slowestSource = index.has(contributingIds[0])
      ? shortObjectName(index.get(contributingIds[0])!.snowflake_object)
      : contributingIds[0].toUpperCase();
  }

  const attribution = slowestSource ? ` (${slowestSource})` : "";
  const text =
    `As of ${formatClockTime(asOf)} · data no fresher than ` +
    `${formatMinutes(latencyFloorMinutes)}${attribution}`;

  return { asOf, latencyFloorMinutes, slowestSource, provisional, text };
}
