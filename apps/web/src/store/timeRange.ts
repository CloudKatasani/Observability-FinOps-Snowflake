// The global time-range filter (§16.2). One store, applied to every data
// request on every page, and mirrored into the URL so a view can be shared or
// reloaded without losing its context.

import { create } from "zustand";

export type PresetId = "7d" | "30d" | "90d" | "mtd" | "13m" | "custom";

export interface DateRange {
  start: string;
  end: string;
}

export interface PresetDefinition {
  id: Exclude<PresetId, "custom">;
  label: string;
}

export const PRESETS: readonly PresetDefinition[] = [
  { id: "7d", label: "Last 7 days" },
  { id: "30d", label: "Last 30 days" },
  { id: "90d", label: "Last 90 days" },
  { id: "mtd", label: "Month to date" },
  { id: "13m", label: "Last 13 months" },
];

export const DEFAULT_PRESET: PresetId = "30d";

export function toIsoDate(date: Date): string {
  const year = date.getFullYear().toString().padStart(4, "0");
  const month = (date.getMonth() + 1).toString().padStart(2, "0");
  const day = date.getDate().toString().padStart(2, "0");
  return `${year}-${month}-${day}`;
}

/** Resolve a preset against a reference day (today, unless a test says otherwise). */
export function presetRange(preset: PresetId, today: Date = new Date()): DateRange {
  const end = new Date(today.getFullYear(), today.getMonth(), today.getDate());
  const start = new Date(end);
  switch (preset) {
    case "7d":
      start.setDate(end.getDate() - 6);
      break;
    case "90d":
      start.setDate(end.getDate() - 89);
      break;
    case "mtd":
      start.setDate(1);
      break;
    case "13m":
      start.setMonth(end.getMonth() - 12);
      start.setDate(1);
      break;
    case "30d":
    case "custom":
    default:
      start.setDate(end.getDate() - 29);
      break;
  }
  return { start: toIsoDate(start), end: toIsoDate(end) };
}

/**
 * The trailing 13 months ending with the selected period — the window the
 * executive trend chart is defined over (§16.1).
 */
export function trailingMonths(end: string, months: number): DateRange {
  const parsed = new Date(`${end}T00:00:00`);
  const start = new Date(parsed.getFullYear(), parsed.getMonth() - (months - 1), 1);
  return { start: toIsoDate(start), end };
}

const ISO_DATE = /^\d{4}-\d{2}-\d{2}$/;

export function isIsoDate(value: string | null | undefined): value is string {
  return typeof value === "string" && ISO_DATE.test(value);
}

interface TimeRangeState extends DateRange {
  preset: PresetId;
  selectPreset: (preset: PresetId) => void;
  setCustomRange: (range: Partial<DateRange>) => void;
  /** Adopt a range read from the URL without re-deriving it from a preset. */
  hydrate: (preset: PresetId, range: DateRange) => void;
}

const initial = presetRange(DEFAULT_PRESET);

export const useTimeRangeStore = create<TimeRangeState>((set, get) => ({
  preset: DEFAULT_PRESET,
  start: initial.start,
  end: initial.end,
  selectPreset: (preset) => {
    if (preset === "custom") {
      set({ preset });
      return;
    }
    set({ preset, ...presetRange(preset) });
  },
  setCustomRange: (range) => {
    const next = { start: range.start ?? get().start, end: range.end ?? get().end };
    if (!isIsoDate(next.start) || !isIsoDate(next.end) || next.end < next.start) return;
    set({ preset: "custom", ...next });
  },
  hydrate: (preset, range) => set({ preset, ...range }),
}));
