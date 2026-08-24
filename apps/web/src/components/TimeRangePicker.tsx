import type { PresetId } from "@/store/timeRange";
import { PRESETS, useTimeRangeStore } from "@/store/timeRange";

const CONTROL =
  "rounded border border-slate-300 bg-white px-2 py-1 text-xs text-slate-900 tabular-nums focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-[var(--brand-primary)]";

/**
 * The global range (§16.2). Every request on every page is scoped by it, and
 * it is mirrored into the URL by the shell so a view stays shareable.
 */
export default function TimeRangePicker() {
  const preset = useTimeRangeStore((state) => state.preset);
  const start = useTimeRangeStore((state) => state.start);
  const end = useTimeRangeStore((state) => state.end);
  const selectPreset = useTimeRangeStore((state) => state.selectPreset);
  const setCustomRange = useTimeRangeStore((state) => state.setCustomRange);

  return (
    <div className="flex flex-wrap items-center gap-2">
      <label className="flex items-center gap-1.5 text-xs text-slate-600">
        <span>Period</span>
        <select
          className={CONTROL}
          value={preset}
          onChange={(event) => selectPreset(event.target.value as PresetId)}
        >
          {PRESETS.map((option) => (
            <option key={option.id} value={option.id}>
              {option.label}
            </option>
          ))}
          <option value="custom">Custom</option>
        </select>
      </label>

      <label className="flex items-center gap-1.5 text-xs text-slate-600">
        <span className="sr-only">Start date</span>
        <input
          type="date"
          className={CONTROL}
          value={start}
          max={end}
          onChange={(event) => setCustomRange({ start: event.target.value })}
        />
      </label>
      <span aria-hidden className="text-xs text-slate-400">
        →
      </span>
      <label className="flex items-center gap-1.5 text-xs text-slate-600">
        <span className="sr-only">End date</span>
        <input
          type="date"
          className={CONTROL}
          value={end}
          min={start}
          onChange={(event) => setCustomRange({ end: event.target.value })}
        />
      </label>
    </div>
  );
}
