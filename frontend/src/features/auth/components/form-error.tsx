import { AlertTriangle } from "lucide-react";

import { ApiError } from "@/lib/api/client";

/**
 * Turns an ApiError into something a person can act on.
 *
 * Rate limiting and a revoked session are the two cases worth phrasing
 * specifically -- "Too many requests" without a wait time, or a bare 401 after
 * a token was revoked, both leave the user with no idea what to do next.
 */
export function FormError({ error }: { error: unknown }) {
  if (!error) return null;

  let message = "Something went wrong. Please try again.";
  let requestId: string | null = null;

  if (error instanceof ApiError) {
    requestId = error.requestId;
    switch (error.code) {
      case "RATE_LIMITED":
        message = "Too many attempts. Please wait a few minutes and try again.";
        break;
      case "TOKEN_REUSE_DETECTED":
        message = "Your session was ended for security reasons. Please sign in again.";
        break;
      case "CONFLICT":
      case "UNAUTHENTICATED":
      case "VALIDATION_ERROR":
        message = error.message;
        break;
      default:
        message = error.message || message;
    }
  }

  return (
    <div
      role="alert"
      className="flex gap-2 rounded-control border border-critical/40 bg-critical/5 p-3 type-body text-critical"
    >
      <AlertTriangle className="mt-0.5 size-4 shrink-0" aria-hidden />
      <div>
        <p>{message}</p>
        {requestId && <p className="mt-1 type-meta text-ink-muted">Reference: {requestId}</p>}
      </div>
    </div>
  );
}
