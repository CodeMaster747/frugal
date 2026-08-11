/**
 * API client.
 *
 * One error path: every non-2xx response from the backend carries the same
 * envelope (docs/04-api-design.md 2.3), so callers handle ApiError and nothing
 * else. Access tokens live in memory only -- never localStorage, which any
 * successful XSS payload can read (NFR-2).
 */

export const API_BASE_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

export type ErrorCode =
  | "VALIDATION_ERROR"
  | "UNAUTHENTICATED"
  | "TOKEN_REUSE_DETECTED"
  | "FORBIDDEN"
  | "NOT_FOUND"
  | "CONFLICT"
  | "UNPROCESSABLE"
  | "RATE_LIMITED"
  | "INTERNAL_ERROR"
  | "INSUFFICIENT_DATA";

export interface ErrorDetail {
  field: string | null;
  issue: string;
  value: string | null;
}

export interface ErrorEnvelope {
  error: {
    code: ErrorCode;
    message: string;
    details: ErrorDetail[];
    request_id: string | null;
    docs_url: string | null;
    /**
     * Present when an engine declined rather than fabricating a result.
     * Declining is an answer, so it carries its reasons like any other.
     */
    caveats?: string[];
  };
}

export class ApiError extends Error {
  constructor(
    readonly status: number,
    readonly code: ErrorCode,
    message: string,
    readonly details: ErrorDetail[] = [],
    readonly requestId: string | null = null,
    readonly caveats: string[] = [],
  ) {
    super(message);
    this.name = "ApiError";
  }

  /** True when an engine declined rather than fabricating a result. */
  get isInsufficientData() {
    return this.code === "INSUFFICIENT_DATA";
  }
}

/** Access token lives in a module closure -- gone when the tab closes. */
let accessToken: string | null = null;

export function setAccessToken(token: string | null) {
  accessToken = token;
}

/** For multipart uploads, which must build their own request. */
export function getAccessToken() {
  return accessToken;
}

/**
 * Refresh hook, registered by the auth feature.
 *
 * Inverted rather than imported directly: the auth API module imports this
 * client, so calling into it from here would be a cycle.
 */
type RefreshHandler = () => Promise<void>;
let refreshHandler: RefreshHandler | null = null;
let inFlightRefresh: Promise<void> | null = null;

export function registerRefreshHandler(handler: RefreshHandler | null) {
  refreshHandler = handler;
}

/** Collapse concurrent refreshes into one.
 *
 * Several requests can 401 at once when a token expires. Without this each
 * would rotate the refresh token separately, and the losers would present an
 * already-used token -- which the backend correctly treats as replay and
 * responds to by revoking the whole family. Self-inflicted logout.
 */
async function refreshOnce(): Promise<void> {
  inFlightRefresh ??= (async () => {
    try {
      await refreshHandler?.();
    } finally {
      inFlightRefresh = null;
    }
  })();
  return inFlightRefresh;
}

async function send(path: string, init: RequestInit): Promise<Response> {
  const headers = new Headers(init.headers);
  if (!(init.body instanceof FormData)) headers.set("Content-Type", "application/json");
  if (accessToken) headers.set("Authorization", `Bearer ${accessToken}`);

  return fetch(`${API_BASE_URL}${path}`, {
    ...init,
    headers,
    // Required for the httpOnly refresh cookie to travel on auth routes.
    credentials: "include",
  });
}

export async function apiFetch<T>(path: string, init: RequestInit = {}): Promise<T> {
  let response = await send(path, init);

  // An expired access token is routine, not a failure: refresh once and retry.
  // Auth endpoints are excluded to avoid recursing through /refresh itself.
  const isAuthRoute = path.startsWith("/api/v1/auth/");
  if (response.status === 401 && refreshHandler && !isAuthRoute) {
    try {
      await refreshOnce();
      response = await send(path, init);
    } catch {
      // Refresh failed; fall through and surface the original 401.
    }
  }

  if (!response.ok) {
    let envelope: ErrorEnvelope | null = null;
    try {
      envelope = (await response.json()) as ErrorEnvelope;
    } catch {
      // A non-JSON body means the failure happened before the app -- a proxy
      // or gateway. Fall through to a generic error.
    }

    throw new ApiError(
      response.status,
      envelope?.error.code ?? "INTERNAL_ERROR",
      envelope?.error.message ?? `Request failed with status ${response.status}`,
      envelope?.error.details ?? [],
      envelope?.error.request_id ?? response.headers.get("X-Request-ID"),
      envelope?.error.caveats ?? [],
    );
  }

  if (response.status === 204) return undefined as T;
  return (await response.json()) as T;
}

// --- system ----------------------------------------------------------------

export interface ReadinessResponse {
  status: "ready" | "degraded";
  dependencies: { database: boolean; redis: boolean };
}

export const getReadiness = () => apiFetch<ReadinessResponse>("/health/ready");
