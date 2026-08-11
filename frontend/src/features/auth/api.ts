import { apiFetch, setAccessToken } from "@/lib/api/client";

export interface User {
  id: string;
  email: string;
  display_name: string;
  base_currency: string;
  timezone: string;
  locale: string;
  is_demo_seeded: boolean;
  email_verified_at: string | null;
  created_at: string;
}

export interface TokenResponse {
  access_token: string;
  token_type: string;
  expires_in: number;
  user: User;
}

export interface RegisterInput {
  email: string;
  password: string;
  display_name: string;
  base_currency?: string;
  timezone?: string;
}

export interface LoginInput {
  email: string;
  password: string;
}

/**
 * Store the access token in memory only.
 *
 * Never localStorage or sessionStorage: both are readable by any successful
 * XSS payload, and an access token there is a durable credential theft. In
 * memory it dies with the tab, and the httpOnly refresh cookie restores the
 * session (NFR-2).
 */
function adopt(tokens: TokenResponse): TokenResponse {
  setAccessToken(tokens.access_token);
  return tokens;
}

export const register = (input: RegisterInput) =>
  apiFetch<TokenResponse>("/api/v1/auth/register", {
    method: "POST",
    body: JSON.stringify(input),
  }).then(adopt);

export const login = (input: LoginInput) =>
  apiFetch<TokenResponse>("/api/v1/auth/login", {
    method: "POST",
    body: JSON.stringify(input),
  }).then(adopt);

/**
 * Exchange the httpOnly refresh cookie for a new access token.
 *
 * Concurrent calls are collapsed into one. Refresh tokens rotate, so two
 * simultaneous calls would send the *same* token twice; the second arrives
 * after the first has been marked used, which the backend correctly reads as
 * replay and answers by revoking the whole family. The user would be silently
 * signed out by their own client.
 *
 * This is not hypothetical: React StrictMode double-invokes mount effects, so
 * without this the session dies on every page load in development.
 *
 * Known limitation: this collapses calls within one tab. Two tabs opening at
 * the same instant can still collide. The standard mitigation is a short
 * server-side grace window on a just-used token, which trades away some of the
 * reuse detection in FR-1.3 -- so it is a deliberate decision to defer rather
 * than an oversight.
 */
let inFlight: Promise<TokenResponse> | null = null;

export const refresh = (): Promise<TokenResponse> => {
  inFlight ??= apiFetch<TokenResponse>("/api/v1/auth/refresh", { method: "POST" })
    .then(adopt)
    .finally(() => {
      inFlight = null;
    });
  return inFlight;
};

export const logout = async () => {
  try {
    await apiFetch<void>("/api/v1/auth/logout", { method: "POST" });
  } finally {
    // Clear locally even if the request failed, so the UI never shows a
    // signed-in state the server has already revoked.
    setAccessToken(null);
  }
};

export const getMe = () => apiFetch<User>("/api/v1/auth/me");

export const updateMe = (
  input: Partial<Pick<User, "display_name" | "base_currency" | "timezone">>,
) => apiFetch<User>("/api/v1/auth/me", { method: "PATCH", body: JSON.stringify(input) });

export const deleteAccount = () =>
  apiFetch<{ status: string }>("/api/v1/auth/me", { method: "DELETE" });
