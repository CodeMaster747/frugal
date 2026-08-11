"use client";

import { useSyncExternalStore } from "react";

const subscribe = () => () => {};

/**
 * True once the client has hydrated, false during server render.
 *
 * Use this to gate anything the server cannot know -- resolved theme, locale
 * formatting, viewport size -- so the first client render matches the server's
 * markup and React does not report a hydration mismatch.
 *
 * `useSyncExternalStore` is the right tool rather than `useState` +
 * `useEffect`: the value is read from the environment, not stored, so there is
 * no cascading render and no setState inside an effect.
 */
export function useIsHydrated(): boolean {
  return useSyncExternalStore(
    subscribe,
    () => true, // client snapshot
    () => false, // server snapshot
  );
}
