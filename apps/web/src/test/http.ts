import { vi } from "vitest";

export interface StubbedResponse {
  status?: number;
  body: unknown;
}

export type RouteTable = Record<string, StubbedResponse | ((body: unknown) => StubbedResponse)>;

/**
 * Stub `fetch` with a prefix-matched route table. Longest prefix wins, so a
 * specific tile route can sit beside the generic metrics route.
 */
export function stubFetch(routes: RouteTable): void {
  const prefixes = Object.keys(routes).sort((a, b) => b.length - a.length);

  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = typeof input === "string" ? input : String(input);
      const prefix = prefixes.find((candidate) => url.startsWith(candidate));
      if (!prefix) throw new Error(`Unexpected fetch: ${url}`);

      const route = routes[prefix];
      const parsedBody = init?.body ? JSON.parse(String(init.body)) : undefined;
      const resolved = typeof route === "function" ? route(parsedBody) : route;
      const status = resolved.status ?? 200;

      return {
        ok: status >= 200 && status < 300,
        status,
        json: async () => resolved.body,
      };
    }),
  );
}
