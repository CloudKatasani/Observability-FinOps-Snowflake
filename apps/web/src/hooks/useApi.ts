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
  SourceSummary,
} from "@/api/client";
import {
  fetchAllocation,
  fetchCoverage,
  fetchMeta,
  fetchMetricTile,
  fetchSources,
  queryMetrics,
} from "@/api/client";
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

export function useMetricTile(metricId: string, range: DateRange): UseQueryResult<MetricTile> {
  return useQuery({
    queryKey: ["tile", metricId, range.start, range.end],
    queryFn: () => fetchMetricTile(metricId, range),
  });
}

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

export function useAllocation(range: DateRange): UseQueryResult<Allocation> {
  return useQuery({
    queryKey: ["allocation", range.start, range.end],
    queryFn: () => fetchAllocation(range),
  });
}
