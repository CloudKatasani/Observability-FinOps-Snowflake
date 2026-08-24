// Pending, empty, and error states. R3 in three components: never a bare
// spinner that resolves into a silent zero, never "no data" without a reason,
// never an error without the action that fixes it.

interface SkeletonProps {
  /** Number of shimmer lines to stand in for the content being fetched. */
  lines?: number;
  className?: string;
}

export function Skeleton({ lines = 3, className = "" }: SkeletonProps) {
  return (
    <div className={`space-y-2 ${className}`} aria-hidden>
      {Array.from({ length: lines }, (_, index) => (
        <div
          key={index}
          className="h-3 animate-pulse rounded bg-slate-200"
          style={{ width: `${100 - index * 12}%` }}
        />
      ))}
    </div>
  );
}

export function LoadingRegion({ label, lines = 3 }: { label: string; lines?: number }) {
  return (
    <div role="status" aria-live="polite">
      <span className="sr-only">{label}</span>
      <Skeleton lines={lines} />
    </div>
  );
}

interface EmptyStateProps {
  /** Why there is nothing here — the `unavailable_reason` or an equivalent. */
  reason: string;
  /** What to do about it, when the API told us. */
  remediation?: string | null;
}

export function EmptyState({ reason, remediation }: EmptyStateProps) {
  return (
    <div className="rounded border border-dashed border-slate-300 bg-slate-50 p-4">
      <p className="flex items-start gap-2 text-sm text-slate-700">
        <span aria-hidden className="mt-0.5 font-semibold text-slate-500">
          —
        </span>
        <span>{reason}</span>
      </p>
      {remediation ? (
        <p className="mt-2 border-t border-slate-200 pt-2 font-mono text-xs text-slate-600">
          {remediation}
        </p>
      ) : null}
    </div>
  );
}

interface ErrorStateProps {
  title: string;
  error: unknown;
  /** The concrete next step — checked API, missing grant, absent extract. */
  remediation: string;
  onRetry?: () => void;
}

export function ErrorState({ title, error, remediation, onRetry }: ErrorStateProps) {
  const detail = error instanceof Error ? error.message : String(error);
  return (
    <div role="alert" className="rounded border border-red-300 bg-red-50 p-4">
      <p className="flex items-center gap-2 text-sm font-semibold text-red-900">
        <span aria-hidden>✕</span>
        {title}
      </p>
      <p className="mt-1 text-sm text-red-900">{remediation}</p>
      <p className="mt-2 font-mono text-xs break-words text-red-800">{detail}</p>
      {onRetry ? (
        <button
          type="button"
          onClick={onRetry}
          className="mt-3 rounded border border-red-400 bg-white px-2.5 py-1 text-xs font-medium text-red-900 hover:bg-red-100 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-red-700"
        >
          Try again
        </button>
      ) : null}
    </div>
  );
}
