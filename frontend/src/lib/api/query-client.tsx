"use client";

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { useState } from "react";

import { ApiError } from "./client";

/**
 * Query provider.
 *
 * The client is created inside state rather than at module scope so each
 * request gets its own cache -- a module-level client would leak one user's
 * cached financial data into another's request during SSR.
 */
export function QueryProvider({ children }: { children: React.ReactNode }) {
  const [client] = useState(
    () =>
      new QueryClient({
        defaultOptions: {
          queries: {
            staleTime: 30_000,
            retry: (failureCount, error) => {
              // Retrying a 4xx just repeats a rejected request. INSUFFICIENT_DATA
              // in particular is a considered answer, not a transient failure.
              if (error instanceof ApiError && error.status < 500) return false;
              return failureCount < 2;
            },
            refetchOnWindowFocus: false,
          },
        },
      }),
  );

  return <QueryClientProvider client={client}>{children}</QueryClientProvider>;
}
