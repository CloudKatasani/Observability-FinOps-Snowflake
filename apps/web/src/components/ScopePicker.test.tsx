import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import ScopePicker from "@/components/ScopePicker";
import { SCOPE_OPTIONS, SINGLE_SCOPE_OPTIONS } from "@/test/fixtures";
import { stubFetch } from "@/test/http";
import { renderWithClient } from "@/test/render";
import { ORGANIZATION_SCOPE, useScopeStore } from "@/store/scope";

function stub(body: unknown = SCOPE_OPTIONS, status = 200) {
  stubFetch({ "/api/v1/metrics/scopes": { body, status } });
}

beforeEach(() => {
  useScopeStore.setState(ORGANIZATION_SCOPE);
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("ScopePicker", () => {
  it("lists the organization first, then every account that has landed data", async () => {
    stub();
    renderWithClient(<ScopePicker />);

    const picker = await screen.findByLabelText("Scope");
    const labels = [...picker.querySelectorAll("option")].map((option) => option.textContent);

    expect(labels[0]).toMatch(/^Organization/);
    expect(labels.slice(1).map((label) => label?.split(" —")[0])).toEqual([
      "ACME_PROD",
      "ACME_ANALYTICS",
      "ACME_SANDBOX",
    ]);
  });

  it("says how much of the catalogue each scope can answer", async () => {
    // The count is the point, not decoration: an account that has only had its
    // billing uploaded must visibly narrow the catalogue in the list, before
    // the user picks it and finds tile after tile explaining itself.
    stub();
    renderWithClient(<ScopePicker />);

    await screen.findByLabelText("Scope");
    expect(screen.getByRole("option", { name: "Organization — 92 of 92 KPIs" })).toBeInTheDocument();
    expect(screen.getByRole("option", { name: "ACME_PROD — 84 of 92 KPIs" })).toBeInTheDocument();
    expect(screen.getByRole("option", { name: "ACME_SANDBOX — 11 of 92 KPIs" })).toBeInTheDocument();
  });

  it("restates the selected scope's coverage in words beside the control", async () => {
    stub();
    renderWithClient(<ScopePicker />);

    expect(
      await screen.findByText("Every account together · answers 92 of 92 KPIs"),
    ).toBeInTheDocument();

    await userEvent.selectOptions(screen.getByLabelText("Scope"), "ACME_SANDBOX");
    expect(screen.getByText("One account · answers 11 of 92 KPIs")).toBeInTheDocument();
  });

  it("puts the chosen account into the shared store", async () => {
    stub();
    renderWithClient(<ScopePicker />);

    await userEvent.selectOptions(await screen.findByLabelText("Scope"), "ACME_PROD");
    expect(useScopeStore.getState()).toMatchObject({ scope: "account", account: "ACME_PROD" });

    await userEvent.selectOptions(screen.getByLabelText("Scope"), "organization");
    expect(useScopeStore.getState()).toMatchObject(ORGANIZATION_SCOPE);
  });

  it("states the scope instead of offering a one-item dropdown", async () => {
    // A single-account deployment has nothing to choose between; a dropdown
    // would imply otherwise.
    stub(SINGLE_SCOPE_OPTIONS);
    renderWithClient(<ScopePicker />);

    expect(await screen.findByText("Organization")).toBeInTheDocument();
    expect(screen.getByText("· answers 92 of 92 KPIs")).toBeInTheDocument();
    expect(screen.queryByRole("combobox")).not.toBeInTheDocument();
  });

  it("keeps a scope this deployment does not have, and says it has no data", async () => {
    // A shared link naming another deployment's account. Widening it silently
    // would answer a different question than the link asked.
    useScopeStore.setState({ scope: "account", account: "ACME_LEGACY" });
    stub();
    renderWithClient(<ScopePicker />);

    expect(
      await screen.findByText(/No account named ACME_LEGACY has landed data here/),
    ).toBeInTheDocument();
    expect(useScopeStore.getState().account).toBe("ACME_LEGACY");
  });

  it("says the account list is missing rather than showing an empty picker", async () => {
    stub({ detail: "no" }, 500);
    renderWithClient(<ScopePicker />);

    expect(
      await screen.findByText(/Organization — the account list could not be loaded/),
    ).toBeInTheDocument();
  });
});
