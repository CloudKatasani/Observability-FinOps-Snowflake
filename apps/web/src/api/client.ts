import { z } from "zod";

// Every API payload is validated at the boundary with zod (BUILD_PROMPT §6);
// downstream code only ever sees parsed, typed values.

export const livenessSchema = z.object({
  status: z.literal("ok"),
  version: z.string(),
});
export type Liveness = z.infer<typeof livenessSchema>;

export const componentStatusSchema = z.object({
  name: z.string(),
  status: z.enum(["ok", "unavailable"]),
  detail: z.string().nullish(),
});

export const readinessSchema = z.object({
  status: z.enum(["ready", "not_ready"]),
  version: z.string(),
  components: z.array(componentStatusSchema),
});
export type Readiness = z.infer<typeof readinessSchema>;

export const brandingSchema = z.object({
  display_name: z.string(),
  short_name: z.string(),
  palette: z.object({
    navy: z.string(),
    primary: z.string(),
    sky: z.string(),
    coral: z.string(),
  }),
});

export const metaSchema = z.object({
  version: z.string(),
  mode: z.string(),
  tenancy: z.string(),
  branding: brandingSchema,
});
export type Meta = z.infer<typeof metaSchema>;

export class ApiError extends Error {
  constructor(
    readonly status: number,
    message: string,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

async function getJson(path: string, allowStatuses: number[] = []): Promise<unknown> {
  const response = await fetch(path, { headers: { accept: "application/json" } });
  if (!response.ok && !allowStatuses.includes(response.status)) {
    throw new ApiError(response.status, `GET ${path} failed with ${response.status}`);
  }
  return response.json();
}

export async function fetchLiveness(): Promise<Liveness> {
  return livenessSchema.parse(await getJson("/healthz"));
}

// 503 is a *valid* readiness answer (not_ready with component detail), not an error.
export async function fetchReadiness(): Promise<Readiness> {
  return readinessSchema.parse(await getJson("/readyz", [503]));
}

export async function fetchMeta(): Promise<Meta> {
  return metaSchema.parse(await getJson("/api/v1/meta"));
}
