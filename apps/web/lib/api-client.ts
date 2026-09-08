import { config } from "@/lib/config";

export class ApiError extends Error {
  constructor(
    public readonly status: number,
    public readonly code: string,
    public readonly detail: string,
  ) {
    super(detail);
  }
}

export async function fetchApi<T>(path: string, init: RequestInit = {}): Promise<T> {
  const headers = new Headers(init.headers);
  if (!headers.has("Accept")) {
    headers.set("Accept", "application/json");
  }

  const response = await fetch(`${config.apiBaseUrl}${path}`, {
    cache: "no-store",
    ...init,
    headers,
  });

  if (!response.ok) {
    const fallback = `EECP API returned ${response.status} for ${path}`;

    try {
      const body = (await response.json()) as { code?: unknown; detail?: unknown };
      if (typeof body.code === "string" && typeof body.detail === "string") {
        throw new ApiError(response.status, body.code, body.detail);
      }
    } catch (error) {
      if (error instanceof ApiError) {
        throw error;
      }
    }

    throw new ApiError(response.status, "API_ERROR", fallback);
  }

  if (response.status === 204) {
    return undefined as T;
  }

  return (await response.json()) as T;
}

