"use client";

import { useRouter } from "next/navigation";
import { useEffect } from "react";

import { useAuth } from "@/features/auth/auth-provider";

/**
 * Client-side route guard.
 *
 * This is a UX affordance, not a security control: the real boundary is the
 * API, which rejects every unauthenticated request regardless of what the
 * browser renders. Session restoration is asynchronous (it exchanges the
 * httpOnly cookie for an access token), so "restoring" is a distinct state --
 * treating it as signed-out would bounce a returning user to /login on every
 * page load.
 */
export function RequireAuth({ children }: { children: React.ReactNode }) {
  const { status } = useAuth();
  const router = useRouter();

  useEffect(() => {
    if (status === "anonymous") router.replace("/login");
  }, [status, router]);

  if (status === "restoring") {
    return (
      <div className="flex flex-1 items-center justify-center p-10" aria-busy="true">
        <span className="type-body text-ink-muted">Restoring your session…</span>
      </div>
    );
  }

  if (status === "anonymous") return null;

  return <>{children}</>;
}
