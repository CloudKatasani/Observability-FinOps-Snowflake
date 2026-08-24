import { useMemo } from "react";

import type { DateRange } from "@/store/timeRange";
import { useTimeRangeStore } from "@/store/timeRange";

/**
 * The active global range as a stable object, safe to use directly in a query
 * key. Selecting the two fields separately keeps the store snapshot referen-
 * tially stable, which `useSyncExternalStore` requires.
 */
export function useDateRange(): DateRange {
  const start = useTimeRangeStore((state) => state.start);
  const end = useTimeRangeStore((state) => state.end);
  return useMemo(() => ({ start, end }), [start, end]);
}
