// Presentation of API figures. Everything here consumes the *string* the API
// sent and returns a string to render; nothing round-trips through a float.

import type { Decimal } from "@/lib/decimal";
import { divideDecimals, parseDecimal, shiftPoint, toFixed } from "@/lib/decimal";

export type FigureFormat = "number" | "currency" | "percent" | "duration_ms" | "bytes" | "integer";

const CURRENCY_SYMBOLS: Record<string, string> = {
  USD: "$",
  EUR: "€",
  GBP: "£",
  AUD: "A$",
  CAD: "C$",
};

const BYTE_UNITS = ["B", "KiB", "MiB", "GiB", "TiB", "PiB"] as const;
const KIBIBYTE: Decimal = { neg: false, unscaled: 1024n, scale: 0 };
const THOUSAND: Decimal = { neg: false, unscaled: 1000n, scale: 0 };
const SIXTY: Decimal = { neg: false, unscaled: 60n, scale: 0 };

/** Insert thousands separators into fixed-point text produced by `toFixed`. */
export function groupThousands(plain: string): string {
  const negative = plain.startsWith("-");
  const body = negative ? plain.slice(1) : plain;
  const [integerPart, fractionPart] = body.split(".");
  const grouped = integerPart.replace(/\B(?=(\d{3})+(?!\d))/g, ",");
  const joined = fractionPart ? `${grouped}.${fractionPart}` : grouped;
  return negative ? `-${joined}` : joined;
}

function fixedGrouped(value: Decimal, decimals: number): string {
  return groupThousands(toFixed(value, decimals));
}

function formatDuration(value: Decimal): string {
  const milliseconds = shiftPoint(value, 0);
  const seconds = divideDecimals(milliseconds, THOUSAND, 3);
  if (!seconds) return `${fixedGrouped(milliseconds, 0)} ms`;
  if (toFixed(seconds, 0) === "0" || Math.abs(Number(toFixed(seconds, 3))) < 1) {
    return `${fixedGrouped(milliseconds, 0)} ms`;
  }
  const minutes = divideDecimals(seconds, SIXTY, 2);
  if (minutes && Math.abs(Number(toFixed(minutes, 2))) >= 1) {
    return `${fixedGrouped(minutes, 1)} min`;
  }
  return `${fixedGrouped(seconds, 2)} s`;
}

function formatBytes(value: Decimal, decimals: number): string {
  let current = value;
  let unitIndex = 0;
  while (unitIndex < BYTE_UNITS.length - 1 && Math.abs(Number(toFixed(current, 0))) >= 1024) {
    const next = divideDecimals(current, KIBIBYTE, 6);
    if (!next) break;
    current = next;
    unitIndex += 1;
  }
  const places = unitIndex === 0 ? 0 : decimals;
  return `${fixedGrouped(current, places)} ${BYTE_UNITS[unitIndex]}`;
}

/**
 * Render one figure. Returns `null` when the value is unknown — callers must
 * show an explanation rather than substituting zero (R3).
 */
export function formatFigure(
  raw: unknown,
  format: FigureFormat,
  decimals: number,
  unit?: string | null,
): string | null {
  const value = parseDecimal(raw);
  if (!value) return null;

  switch (format) {
    case "integer":
      return fixedGrouped(value, 0);
    case "percent":
      return `${fixedGrouped(shiftPoint(value, 2), decimals)}%`;
    case "currency": {
      const code = (unit ?? "USD").toUpperCase();
      const symbol = CURRENCY_SYMBOLS[code];
      const amount = fixedGrouped(value, decimals);
      return symbol ? `${symbol}${amount}` : `${amount} ${code}`;
    }
    case "duration_ms":
      return formatDuration(value);
    case "bytes":
      return formatBytes(value, decimals);
    case "number":
    default:
      return fixedGrouped(value, decimals);
  }
}

/**
 * The unit caption shown beside a figure. Currency and percent carry their unit
 * inside the figure itself, so they return null.
 */
export function unitLabel(format: FigureFormat, unit?: string | null): string | null {
  if (format === "currency" || format === "percent" || format === "duration_ms") return null;
  if (format === "bytes") return null;
  return unit ?? null;
}

/** Convenience for credit columns, which are always plain decimal strings. */
export function formatCredits(raw: unknown, decimals = 1): string | null {
  return formatFigure(raw, "number", decimals);
}

/** Render an already-parsed exact value, e.g. a total summed across buckets. */
export function formatDecimalValue(value: Decimal, decimals = 1): string {
  return fixedGrouped(value, decimals);
}

/** Render an already-parsed *fraction* as a percentage. */
export function formatDecimalPercent(value: Decimal, decimals = 1): string {
  return `${fixedGrouped(shiftPoint(value, 2), decimals)}%`;
}

/** A percentage the API already expressed in percent units (not a fraction). */
export function formatPercentPoints(raw: unknown, decimals = 3): string | null {
  const value = parseDecimal(raw);
  if (!value) return null;
  return `${fixedGrouped(value, decimals)}%`;
}

export function formatInteger(raw: unknown): string | null {
  return formatFigure(raw, "integer", 0);
}

/** "8h", "45m", "3d" — a duration expressed in whole minutes. */
export function formatMinutes(minutes: number): string {
  if (!Number.isFinite(minutes) || minutes < 0) return "unknown";
  if (minutes < 60) return `${Math.round(minutes)}m`;
  if (minutes < 1440) {
    const hours = minutes / 60;
    return Number.isInteger(hours) ? `${hours}h` : `${hours.toFixed(1)}h`;
  }
  const days = minutes / 1440;
  return Number.isInteger(days) ? `${days}d` : `${days.toFixed(1)}d`;
}

/** Local wall-clock time of an API `as_of` stamp, e.g. "14:32". */
export function formatClockTime(isoTimestamp: string): string {
  const parsed = new Date(isoTimestamp);
  if (Number.isNaN(parsed.getTime())) return "unknown";
  const hours = parsed.getHours().toString().padStart(2, "0");
  const minutes = parsed.getMinutes().toString().padStart(2, "0");
  return `${hours}:${minutes}`;
}

/** "2026-08-20" from any ISO date or timestamp; empty string when absent. */
export function formatIsoDate(value: string | null | undefined): string {
  if (!value) return "";
  return value.slice(0, 10);
}

const MONTH_NAMES = [
  "Jan",
  "Feb",
  "Mar",
  "Apr",
  "May",
  "Jun",
  "Jul",
  "Aug",
  "Sep",
  "Oct",
  "Nov",
  "Dec",
];

/**
 * Axis label for a `TIME_BUCKET` value. The string is split rather than parsed
 * through `Date`, because the API sends a naive bucket boundary and shifting it
 * into the reader's timezone would move figures between days.
 */
export function formatBucketLabel(bucket: string, grain: "hour" | "day" | "week" | "month"): string {
  const [datePart, timePart = ""] = bucket.split("T");
  const [year, month, day] = datePart.split("-");
  const monthName = MONTH_NAMES[Number(month) - 1] ?? month;
  if (!year || !month) return bucket;
  switch (grain) {
    case "month":
      return `${monthName} ${year}`;
    case "hour":
      return `${day} ${monthName} ${timePart.slice(0, 5)}`;
    case "week":
    case "day":
    default:
      return `${day} ${monthName}`;
  }
}

/** The bare view name from a fully qualified Snowflake object. */
export function shortObjectName(qualified: string): string {
  const parts = qualified.split(".");
  return (parts[parts.length - 1] ?? qualified).toUpperCase();
}
