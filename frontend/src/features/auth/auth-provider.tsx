"use client";

import { useRouter } from "next/navigation";
import { createContext, useCallback, useContext, useEffect, useRef, useState } from "react";

import { ApiError, registerRefreshHandler, setAccessToken } from "@/lib/api/client";

import * as authApi from "./api";
import type { LoginInput, RegisterInput, TokenResponse, User } from "./api";

type Status = "restoring" | "authenticated" | "anonymous";

interface AuthContextValue {
  user: User | null;
  status: Status;
  login: (input: LoginInput) => Promise<void>;
  register: (input: RegisterInput) => Promise<void>;
  logout: () => Promise<void>;
  setUser: (user: User) => void;
}

const AuthContext = createContext<AuthContextValue | null>(null);

/** Refresh this long before the access token expires. */
const REFRESH_MARGIN_MS = 60_000;
const MIN_REFRESH_DELAY_MS = 10_000;

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const router = useRouter();
  const [user, setUserState] = useState<User | null>(null);
  const [status, setStatus] = useState<Status>("restoring");

  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  // Holds the latest silentRefresh. Scheduling reads through this ref so the
  // timer callback and the function it calls do not depend on each other --
  // otherwise the two useCallbacks form a cycle that cannot be memoized.
  const refreshRef = useRef<(() => Promise<void>) | null>(null);

  const clearTimer = useCallback(() => {
    if (timer.current) clearTimeout(timer.current);
    timer.current = null;
  }, []);

  /**
   * Schedule a silent refresh shortly before expiry.
   *
   * Because the access token lives only in memory, this is what keeps a
   * session alive across its 15-minute lifetime without ever writing a
   * credential to storage.
   */
  const scheduleRefresh = useCallback(
    (expiresIn: number) => {
      clearTimer();
      const delay = Math.max(expiresIn * 1000 - REFRESH_MARGIN_MS, MIN_REFRESH_DELAY_MS);
      timer.current = setTimeout(() => void refreshRef.current?.(), delay);
    },
    [clearTimer],
  );

  const adopt = useCallback(
    (tokens: TokenResponse) => {
      setUserState(tokens.user);
      setStatus("authenticated");
      scheduleRefresh(tokens.expires_in);
    },
    [scheduleRefresh],
  );

  const signOutLocally = useCallback(() => {
    clearTimer();
    setAccessToken(null);
    setUserState(null);
    setStatus("anonymous");
  }, [clearTimer]);

  const silentRefresh = useCallback(async () => {
    try {
      adopt(await authApi.refresh());
    } catch (error) {
      signOutLocally();
      // A revoked family means the session is gone for good. Say so, rather
      // than dropping the user on a bare login page wondering what happened.
      if (error instanceof ApiError && error.code === "TOKEN_REUSE_DETECTED") {
        router.push("/login?reason=session_revoked");
      }
    }
  }, [adopt, router, signOutLocally]);

  useEffect(() => {
    refreshRef.current = silentRefresh;
    // Lets the API client refresh-and-retry a request that 401s on an expired
    // access token, without importing this module (which would be a cycle).
    registerRefreshHandler(silentRefresh);
    return () => registerRefreshHandler(null);
  }, [silentRefresh]);

  // On mount, try to restore a session from the httpOnly refresh cookie. A 401
  // here is the ordinary "not signed in" case, not an error.
  useEffect(() => {
    let cancelled = false;

    void (async () => {
      try {
        const tokens = await authApi.refresh();
        if (!cancelled) adopt(tokens);
      } catch {
        if (!cancelled) {
          setUserState(null);
          setStatus("anonymous");
        }
      }
    })();

    return () => {
      cancelled = true;
    };
  }, [adopt]);

  // Clear any pending timer when the provider unmounts.
  useEffect(() => clearTimer, [clearTimer]);

  const value: AuthContextValue = {
    user,
    status,
    setUser: setUserState,
    login: async (input) => adopt(await authApi.login(input)),
    register: async (input) => adopt(await authApi.register(input)),
    logout: async () => {
      await authApi.logout();
      signOutLocally();
      router.push("/login");
    },
  };

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthContextValue {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth must be used inside <AuthProvider>");
  return ctx;
}
