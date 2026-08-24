// Saying whose figure this is (§9, R5). Every metric response reports the scope
// it was computed at and, for an organization roll-up, which accounts actually
// contributed. Reducing those to one sentence is this module's whole job.
//
// The distinction that matters: an organization figure computed over the
// accounts landed so far is *not* the whole organization. It is a correct
// figure with an incomplete denominator, and presenting it as the fleet total
// would be the same class of error as substituting a zero for an unknown (R3).

import type { ProvenanceContribution, ScopeContext } from "@/api/client";

/** What a figure computed at this scope is filed under, in the reader's words. */
export function scopeContextLabel(context: ScopeContext): string {
  if (context.scope === "account") return context.scope_account ?? "Unnamed account";
  return "Organization";
}

/** "Organization · 4 accounts" / "ACME_PROD" — the strip's scope caption. */
export function scopeCaption(context: ScopeContext): string {
  const label = scopeContextLabel(context);
  if (context.scope === "account") return label;
  const count = context.contributing_accounts.length;
  if (count === 0) return label;
  return `${label} · ${count} ${count === 1 ? "account" : "accounts"}`;
}

/** The sentence shown when an organization roll-up covers only part of the fleet. */
export function partialWarning(context: ScopeContext): string {
  const accounts = context.contributing_accounts;
  return (
    `Partial organization roll-up: computed over the ${accounts.length} ` +
    `accounts landed so far (${accounts.join(", ")}). Any account not yet ` +
    "uploaded is absent from this figure, not counted as zero."
  );
}

export interface ScopeSummary {
  /** The scope every contribution agrees on, or null when they disagree. */
  label: string | null;
  /** True when any contributing figure is a partial organization roll-up. */
  partial: boolean;
  /** The union of the accounts behind the partial contributions. */
  accounts: string[];
  /** The warning sentence, or null when nothing on the page is partial. */
  warning: string | null;
}

/** A contribution that did report its scope. */
type ScopedContribution = ProvenanceContribution & ScopeContext;

/**
 * Not every endpoint reports a scope — the allocation and coverage endpoints do
 * not — so a page's contributions are filtered rather than assumed.
 */
function hasScope(entry: ProvenanceContribution | undefined | null): entry is ScopedContribution {
  return Boolean(entry?.scope && Array.isArray(entry.contributing_accounts));
}

/**
 * Reduce every scope-bearing response on a page to one honest statement.
 *
 * The accounts are unioned rather than intersected: a page whose tiles rest on
 * different subsets of the fleet is partial over all of them, and naming fewer
 * accounts than actually contributed would understate the gap.
 */
export function summariseScope(
  contributions: readonly (ProvenanceContribution | undefined | null)[],
): ScopeSummary {
  const present = contributions.filter(hasScope);
  if (present.length === 0) {
    return { label: null, partial: false, accounts: [], warning: null };
  }

  const labels = new Set(present.map(scopeContextLabel));
  const label = labels.size === 1 ? [...labels][0] : null;

  const partialEntries = present.filter((entry) => entry.scope_partial);
  const accounts = [
    ...new Set(partialEntries.flatMap((entry) => entry.contributing_accounts)),
  ].sort();

  if (partialEntries.length === 0 || accounts.length === 0) {
    return { label, partial: false, accounts: [], warning: null };
  }

  return {
    label,
    partial: true,
    accounts,
    warning: partialWarning({ ...partialEntries[0], contributing_accounts: accounts }),
  };
}
