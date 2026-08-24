// The global organization / account filter (§9, §16.2). One store, applied to
// every metric request on every page, and mirrored into the URL by the shell so
// a scoped view can be shared or reloaded without losing which account it meant.
//
// The field names match the API's `scope` / `account` parameters exactly, so a
// selection is also the request fragment that carries it — there is no mapping
// layer in which an account could be dropped or renamed on its way to a query.

import { create } from "zustand";

export type ScopeKind = "organization" | "account";

export interface ScopeSelection {
  scope: ScopeKind;
  /** The account, when `scope` is "account"; null at organization scope. */
  account: string | null;
}

/** The default, and the only scope a single-account deployment ever has. */
export const ORGANIZATION_SCOPE: ScopeSelection = { scope: "organization", account: null };

/** The `<option>` value standing for the organization; accounts use their name. */
export const ORGANIZATION_VALUE = "organization";

/** What a figure computed at this scope is filed under, in the reader's words. */
export function scopeLabel(selection: ScopeSelection): string {
  return selection.scope === "account" && selection.account ? selection.account : "Organization";
}

/** The picker's `<select>` value for a selection. */
export function scopeValue(selection: ScopeSelection): string {
  return selection.scope === "account" && selection.account
    ? selection.account
    : ORGANIZATION_VALUE;
}

/** The selection a picker value stands for. */
export function selectionFromValue(value: string): ScopeSelection {
  if (value === ORGANIZATION_VALUE) return ORGANIZATION_SCOPE;
  return { scope: "account", account: value };
}

/**
 * Read a selection out of URL parameters, or null when the URL names none.
 *
 * `scope=account` without an account is rejected rather than widened: the API
 * rejects it too, and quietly answering at organization scope would put the
 * organization's figure under an account's label.
 */
export function parseScopeSelection(
  scope: string | null | undefined,
  account: string | null | undefined,
): ScopeSelection | null {
  if (scope === "organization") return ORGANIZATION_SCOPE;
  if (scope === "account") {
    return account ? { scope: "account", account } : null;
  }
  return null;
}

export function sameScope(a: ScopeSelection, b: ScopeSelection): boolean {
  return a.scope === b.scope && a.account === b.account;
}

interface ScopeState extends ScopeSelection {
  select: (selection: ScopeSelection) => void;
}

export const useScopeStore = create<ScopeState>((set) => ({
  ...ORGANIZATION_SCOPE,
  select: (selection) =>
    set(
      selection.scope === "account" && selection.account
        ? { scope: "account", account: selection.account }
        : ORGANIZATION_SCOPE,
    ),
}));
