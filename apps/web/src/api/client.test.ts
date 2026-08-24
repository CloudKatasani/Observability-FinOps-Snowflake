import { afterEach, describe, expect, it, vi } from "vitest";

import { ApiError, fetchLiveness, fetchReadiness, readinessSchema } from "@/api/client";

function mockFetch(status: number, body: unknown) {
  vi.stubGlobal(
    "fetch",
    vi.fn(async () => ({
      ok: status >= 200 && status < 300,
      status,
      json: async () => body,
    })),
  );
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("fetchLiveness", () => {
  it("parses a healthy response", async () => {
    mockFetch(200, { status: "ok", version: "0.1.0" });
    await expect(fetchLiveness()).resolves.toEqual({ status: "ok", version: "0.1.0" });
  });

  it("rejects a malformed payload rather than passing it through", async () => {
    mockFetch(200, { status: "fine" });
    await expect(fetchLiveness()).rejects.toThrow();
  });

  it("raises ApiError on transport-level failure statuses", async () => {
    mockFetch(500, {});
    await expect(fetchLiveness()).rejects.toBeInstanceOf(ApiError);
  });
});

describe("fetchReadiness", () => {
  it("treats 503 as a valid not_ready answer, not an error", async () => {
    const body = {
      status: "not_ready",
      version: "0.1.0",
      components: [
        { name: "postgres", status: "unavailable", detail: "ConnectionRefusedError" },
        { name: "redis", status: "ok" },
      ],
    };
    mockFetch(503, body);
    const result = await fetchReadiness();
    expect(result.status).toBe("not_ready");
    expect(result.components).toHaveLength(2);
  });
});

describe("readinessSchema", () => {
  it("rejects unknown component statuses", () => {
    const parsed = readinessSchema.safeParse({
      status: "ready",
      version: "0.1.0",
      components: [{ name: "postgres", status: "degraded" }],
    });
    expect(parsed.success).toBe(false);
  });
});
