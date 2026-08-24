import { useMemo } from "react";

import type { ScopeSelection } from "@/store/scope";
import { useScopeStore } from "@/store/scope";

/**
 * The active global scope as a stable object, safe to use directly in a query
 * key and to spread into a metric request. Selecting the two fields separately
 * keeps the store snapshot referentially stable, which `useSyncExternalStore`
 * requires.
 */
export function useScope(): ScopeSelection {
  const scope = useScopeStore((state) => state.scope);
  const account = useScopeStore((state) => state.account);
  return useMemo(() => ({ scope, account }), [scope, account]);
}
