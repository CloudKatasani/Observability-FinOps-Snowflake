import { useScopeOptions } from "@/hooks/useApi";
import { useScope } from "@/hooks/useScope";
import { formatInteger } from "@/lib/format";
import { scopeLabel, scopeValue, selectionFromValue, useScopeStore } from "@/store/scope";

const CONTROL =
  "rounded border border-slate-300 bg-white px-2 py-1 text-xs text-slate-900 tabular-nums focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-[var(--brand-primary)]";

/**
 * "84 of 92 KPIs" — the coverage each scope can answer.
 *
 * This is the honest part of the selector rather than decoration: an account
 * that has only had its billing uploaded should visibly narrow the catalogue in
 * the list, before the user picks it and finds tile after tile explaining
 * itself. It is stated in words on the selected option too, so the count is not
 * carried by the dropdown alone.
 */
function coverageCaption(answerable: number, total: number): string {
  return `${formatInteger(answerable) ?? answerable} of ${formatInteger(total) ?? total} KPIs`;
}

/**
 * The global organization / account filter (§9, §16.2). Every metric request on
 * every page is scoped by it, and the shell mirrors it into the URL so a scoped
 * view stays shareable.
 */
export default function ScopePicker() {
  const selection = useScope();
  const select = useScopeStore((state) => state.select);
  const options = useScopeOptions();

  const available = options.data?.options ?? [];
  const current = available.find((option) => option.value === scopeValue(selection));

  if (options.isPending) {
    return (
      <div className="flex items-center gap-1.5 text-xs text-slate-600">
        <span>Scope</span>
        <span className="h-6 w-40 animate-pulse rounded bg-slate-200" aria-hidden />
        <span className="sr-only">Loading the scopes this deployment can answer at</span>
      </div>
    );
  }

  // R3 applies to the filter as much as to a figure: a selector that could not
  // load its options says so, and still states the scope in force.
  if (options.isError) {
    return (
      <p className="flex flex-wrap items-center gap-1.5 text-xs text-slate-600">
        <span>Scope</span>
        <span className="rounded border border-amber-300 bg-amber-50 px-1.5 py-0.5 font-medium text-amber-900">
          <span aria-hidden className="mr-1 font-bold">
            !
          </span>
          {scopeLabel(selection)} — the account list could not be loaded
        </span>
      </p>
    );
  }

  // A single-account deployment has nothing to choose between. A one-item
  // dropdown would imply otherwise, so the scope is simply stated.
  if (available.length <= 1) {
    const only = available[0];
    return (
      <p className="flex flex-wrap items-center gap-1.5 text-xs text-slate-600">
        <span>Scope</span>
        <span className="font-medium text-slate-900">{only?.label ?? scopeLabel(selection)}</span>
        {only ? (
          <span className="text-slate-500">
            · answers {coverageCaption(only.answerable_metrics, only.total_metrics)}
          </span>
        ) : null}
      </p>
    );
  }

  const value = scopeValue(selection);
  // A scope named in a shared URL that this deployment does not have. Widening
  // it to the organization silently would answer a different question than the
  // link asked, so it is kept and flagged instead.
  const unknown = current === undefined;

  return (
    <div className="flex flex-wrap items-center gap-2">
      <label className="flex items-center gap-1.5 text-xs text-slate-600">
        <span>Scope</span>
        <select
          className={CONTROL}
          value={value}
          onChange={(event) => select(selectionFromValue(event.target.value))}
        >
          {unknown ? (
            <option value={value}>{value} — not present in this deployment</option>
          ) : null}
          {available.map((option) => (
            <option key={option.value} value={option.value}>
              {option.label} — {coverageCaption(option.answerable_metrics, option.total_metrics)}
            </option>
          ))}
        </select>
      </label>

      {unknown ? (
        <span
          role="status"
          className="rounded border border-amber-300 bg-amber-50 px-1.5 py-0.5 text-[11px] font-medium text-amber-900"
        >
          <span aria-hidden className="mr-1 font-bold">
            !
          </span>
          No account named {value} has landed data here, so its figures will be empty.
        </span>
      ) : (
        <span className="text-[11px] text-slate-500">
          {current.scope === "organization"
            ? `Every account together · answers ${coverageCaption(current.answerable_metrics, current.total_metrics)}`
            : `One account · answers ${coverageCaption(current.answerable_metrics, current.total_metrics)}`}
        </span>
      )}
    </div>
  );
}
