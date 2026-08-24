// Reshaping governed metric result sets for tiles, tables, and charts.
//
// The compiler always emits its columns in upper case (it quotes identifiers),
// with a `TIME_BUCKET` first when the entity has a time column, then the
// requested dimensions, then one column per metric named after the metric id
// with dots replaced by underscores.

import type { Cell, MetricQueryResponse } from "@/api/client";
import type { Decimal } from "@/lib/decimal";
import { compareDecimals, sumDecimals } from "@/lib/decimal";

export const TIME_BUCKET = "TIME_BUCKET";

/** The result column a metric id lands in, e.g. `cost.total_credits`. */
export function metricColumn(metricId: string): string {
  return metricId.replace(/\./g, "_").toUpperCase();
}

function indexOfColumn(response: MetricQueryResponse, column: string): number {
  const wanted = column.toUpperCase();
  return response.columns.findIndex((name) => name.toUpperCase() === wanted);
}

/** Cell values for one column, in row order. Empty when the column is absent. */
export function columnValues(response: MetricQueryResponse, column: string): Cell[] {
  const index = indexOfColumn(response, column);
  if (index < 0) return [];
  return response.rows.map((row) => row[index] ?? null);
}

export interface GroupTotal {
  key: string;
  total: Decimal;
}

/**
 * Sum a metric across every time bucket, grouped by one dimension.
 *
 * The API returns one row per (bucket, dimension) tuple; a "top 10 warehouses
 * for the period" view has to fold the buckets away. That is done here with
 * exact decimal addition, so the totals shown are the API's own figures added
 * up — not floats.
 */
export function groupTotals(
  response: MetricQueryResponse,
  dimension: string,
  metricId: string,
  options: { limit?: number } = {},
): GroupTotal[] {
  const keyIndex = indexOfColumn(response, dimension);
  const valueIndex = indexOfColumn(response, metricColumn(metricId));
  if (keyIndex < 0 || valueIndex < 0) return [];

  const buckets = new Map<string, Cell[]>();
  for (const row of response.rows) {
    const rawKey = row[keyIndex];
    const key = rawKey === null || rawKey === undefined ? "—" : String(rawKey);
    const existing = buckets.get(key);
    if (existing) {
      existing.push(row[valueIndex] ?? null);
    } else {
      buckets.set(key, [row[valueIndex] ?? null]);
    }
  }

  const totals = [...buckets.entries()].map(([key, values]) => ({
    key,
    total: sumDecimals(values),
  }));
  totals.sort((a, b) => compareDecimals(b.total, a.total));
  return options.limit ? totals.slice(0, options.limit) : totals;
}

export interface BucketPoint {
  bucket: string;
  value: Decimal | null;
  raw: Cell;
}

/**
 * One point per time bucket for a single metric, ordered oldest first.
 * Buckets the API did not return are absent — never invented as zero (R3).
 */
export function timeSeries(response: MetricQueryResponse, metricId: string): BucketPoint[] {
  const bucketIndex = indexOfColumn(response, TIME_BUCKET);
  const valueIndex = indexOfColumn(response, metricColumn(metricId));
  if (bucketIndex < 0 || valueIndex < 0) return [];

  const points = new Map<string, Cell[]>();
  for (const row of response.rows) {
    const bucket = String(row[bucketIndex] ?? "");
    if (!bucket) continue;
    const existing = points.get(bucket);
    if (existing) {
      existing.push(row[valueIndex] ?? null);
    } else {
      points.set(bucket, [row[valueIndex] ?? null]);
    }
  }

  return [...points.entries()]
    .sort(([a], [b]) => (a < b ? -1 : a > b ? 1 : 0))
    .map(([bucket, values]) => {
      const known = values.filter((value) => value !== null);
      return {
        bucket,
        value: known.length > 0 ? sumDecimals(known) : null,
        raw: known.length > 0 ? known[0] : null,
      };
    });
}

/** Rows as objects keyed by upper-case column name — handy for small tables. */
export function toRecords(response: MetricQueryResponse): Record<string, Cell>[] {
  return response.rows.map((row) => {
    const record: Record<string, Cell> = {};
    response.columns.forEach((column, index) => {
      record[column.toUpperCase()] = row[index] ?? null;
    });
    return record;
  });
}
