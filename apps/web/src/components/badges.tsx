// Status is always carried by a word and a glyph as well as a colour, so the
// meaning survives greyscale, colour blindness, and a screen reader (§16.2).

export type Tone = "good" | "warn" | "bad" | "info" | "muted";

const TONE_CLASSES: Record<Tone, string> = {
  good: "border-emerald-300 bg-emerald-50 text-emerald-900",
  warn: "border-amber-300 bg-amber-50 text-amber-900",
  bad: "border-red-300 bg-red-50 text-red-900",
  info: "border-sky-300 bg-sky-50 text-sky-900",
  muted: "border-slate-300 bg-slate-50 text-slate-700",
};

const TONE_GLYPHS: Record<Tone, string> = {
  good: "✓",
  warn: "!",
  bad: "✕",
  info: "i",
  muted: "–",
};

export function StatusPill({ tone, label }: { tone: Tone; label: string }) {
  return (
    <span
      className={`inline-flex items-center gap-1.5 rounded border px-1.5 py-0.5 text-xs font-medium whitespace-nowrap ${TONE_CLASSES[tone]}`}
    >
      <span aria-hidden className="font-bold">
        {TONE_GLYPHS[tone]}
      </span>
      {label}
    </span>
  );
}

/**
 * Marks a figure inside a restatement window (§16.2). Sources such as
 * `USAGE_IN_CURRENCY_DAILY` restate until month close; the badge says so rather
 * than letting the number look final.
 */
export function ProvisionalBadge({ className = "" }: { className?: string }) {
  return (
    <span
      title="This figure may restate — its source is still inside its restatement window."
      className={`inline-flex items-center gap-1 rounded border border-amber-300 bg-amber-50 px-1.5 py-0.5 text-[11px] font-medium text-amber-900 ${className}`}
    >
      <span aria-hidden>◐</span>
      Provisional
    </span>
  );
}
