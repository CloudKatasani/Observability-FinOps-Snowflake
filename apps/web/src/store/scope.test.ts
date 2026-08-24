import { beforeEach, describe, expect, it } from "vitest";

import {
  ORGANIZATION_SCOPE,
  parseScopeSelection,
  sameScope,
  scopeLabel,
  scopeValue,
  selectionFromValue,
  useScopeStore,
} from "@/store/scope";

beforeEach(() => {
  useScopeStore.setState(ORGANIZATION_SCOPE);
});

describe("the scope store", () => {
  it("starts at the organization, the widest scope any deployment has", () => {
    expect(useScopeStore.getState().scope).toBe("organization");
    expect(useScopeStore.getState().account).toBeNull();
  });

  it("selects one account", () => {
    useScopeStore.getState().select({ scope: "account", account: "ACME_PROD" });

    expect(useScopeStore.getState().scope).toBe("account");
    expect(useScopeStore.getState().account).toBe("ACME_PROD");
  });

  it("drops the account when the organization is selected again", () => {
    useScopeStore.getState().select({ scope: "account", account: "ACME_PROD" });
    useScopeStore.getState().select(ORGANIZATION_SCOPE);

    expect(useScopeStore.getState().account).toBeNull();
  });

  it("refuses an account scope with no account rather than inventing one", () => {
    // The API rejects the same pair. Falling back to the organization keeps the
    // store in a state a request can actually be built from, and keeps the
    // label honest about what it is showing.
    useScopeStore.getState().select({ scope: "account", account: null });

    expect(useScopeStore.getState()).toMatchObject(ORGANIZATION_SCOPE);
  });
});

describe("scope selections", () => {
  it("labels a figure with the account it belongs to, or the organization", () => {
    expect(scopeLabel(ORGANIZATION_SCOPE)).toBe("Organization");
    expect(scopeLabel({ scope: "account", account: "ACME_APAC" })).toBe("ACME_APAC");
  });

  it("round-trips through the picker's option value", () => {
    const account = { scope: "account", account: "ACME_APAC" } as const;

    expect(scopeValue(ORGANIZATION_SCOPE)).toBe("organization");
    expect(scopeValue(account)).toBe("ACME_APAC");
    expect(selectionFromValue(scopeValue(account))).toEqual(account);
    expect(selectionFromValue(scopeValue(ORGANIZATION_SCOPE))).toEqual(ORGANIZATION_SCOPE);
  });

  it("compares two selections by both halves", () => {
    expect(sameScope(ORGANIZATION_SCOPE, { scope: "organization", account: null })).toBe(true);
    expect(
      sameScope({ scope: "account", account: "A" }, { scope: "account", account: "B" }),
    ).toBe(false);
  });
});

describe("parseScopeSelection", () => {
  it("reads a shared URL's scope", () => {
    expect(parseScopeSelection("organization", null)).toEqual(ORGANIZATION_SCOPE);
    expect(parseScopeSelection("account", "ACME_PROD")).toEqual({
      scope: "account",
      account: "ACME_PROD",
    });
  });

  it("rejects an account scope with no account instead of widening it", () => {
    // Widening would answer a different question than the link asked, and put
    // the organization's figure under an account's label.
    expect(parseScopeSelection("account", null)).toBeNull();
    expect(parseScopeSelection("account", "")).toBeNull();
  });

  it("ignores a scope it does not recognise", () => {
    expect(parseScopeSelection("region", "eu")).toBeNull();
    expect(parseScopeSelection(null, null)).toBeNull();
  });
});
