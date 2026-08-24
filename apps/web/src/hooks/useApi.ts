// TanStack Query bindings over the validated API client. Nothing in the pages
// calls `fetch` directly, and nothing sees an unvalidated payload.

import { useQuery } from "@tanstack/react-query";
import type { UseQueryResult } from "@tanstack/react-query";
import { useMemo } from "react";

import type {
  Allocation,
  CoverageMatrix,
  Meta,
  MetricQueryRequest,
  MetricQueryResponse,
  MetricTile,
  ScopeOptions,
  SourceSummary,
} from "@/api/client";
import {
  fetchAllocation,
  fetchCoverage,
  fetchMeta,
  fetchMetricTile,
  fetchScopeOptions,
  fetchSources,
  queryMetrics,
} from "@/api/client";
import type { ScopeSelection } from "@/store/scope";
import type { DateRange } from "@/store/timeRange";

/** Branding and deployment metadata. Stable for the life of a deployment. */
export function useMeta(): UseQueryResult<Meta> {
  return useQuery({ queryKey: ["meta"], queryFn: fetchMeta, staleTime: 5 * 60_000 });
}

/** The source registry — documented latencies for the freshness banner (R7). */
export function useSources(): UseQueryResult<SourceSummary[]> {
  return useQuery({ queryKey: ["sources"], queryFn: fetchSources, staleTime: 5 * 60_000 });
}

/** Source id → definition, for labelling provenance and ranking staleness. */
export function useSourceIndex(): Map<string, SourceSummary> {
  const sources = useSources();
  return useMemo(() => {
    const index = new Map<string, SourceSummary>();
    for (const source of sources.data ?? []) index.set(source.id, source);
    return index;
  }, [sources.data]);
}

export function useCoverage(): UseQueryResult<CoverageMatrix> {
  return useQuery({ queryKey: ["coverage"], queryFn: fetchCoverage, staleTime: 60_000 });
}

/** The organization/account scopes on offer, and what each can answer (§9). */
export function useScopeOptions(): UseQueryResult<ScopeOptions> {
  return useQuery({
    queryKey: ["scopes"],
    queryFn: fetchScopeOptions,
    staleTime: 5 * 60_000,
  });
}

/**
 * One KPI tile at the selected scope.
 *
 * The scope is part of the query key, not just the request: without it, moving
 * from the organization to an account would leave the previous figure on screen
 * under the new account's label until the refetch landed — the one failure the
 * scope filter exists to prevent.
 */
export function useMetricTile(
  metricId: string,
  range: DateRange,
  scope: ScopeSelection,
): UseQueryResult<MetricTile> {
  return useQuery({
    queryKey: ["tile", metricId, range.start, range.end, scope.scope, scope.account],
    queryFn: () => fetchMetricTile(metricId, range, scope),
  });
}

/**
 * A governed metric query. The whole request is the cache key, so the scope it
 * carries distinguishes one account's rows from another's without a second
 * mechanism to keep in step.
 */
export function useMetricQuery(
  key: string,
  request: MetricQueryRequest,
  enabled = true,
): UseQueryResult<MetricQueryResponse> {
  return useQuery({
    queryKey: ["metrics", key, request],
    queryFn: () => queryMetrics(request),
    enabled,
  });
}

/**
 * The allocation at the selected scope.
 *
 * The scope is in the key for the same reason it is in a tile's: an account
 * switch must not leave the previous account's chargeback on screen under the
 * new account's name while the refetch is in flight.
 */
export function useAllocation(range: DateRange, scope: ScopeSelection): UseQueryResult<Allocation> {
  return useQuery({
    queryKey: ["allocation", range.start, range.end, scope.scope, scope.account],
    queryFn: () => fetchAllocation(range, scope),
  });
}
