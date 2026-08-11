"use client";

import { useEffect, useRef, useState, useSyncExternalStore } from "react";

import { cn } from "@/lib/utils";

/**
 * Scroll-triggered fade and rise, for the public pages only.
 *
 * No animation library: the app has none, and one curve with one duration is
 * the whole motion policy (docs/05-ui-wireframes.md section 2.1). An
 * IntersectionObserver and two class states cover this without a dependency.
 *
 * Three things are deliberate:
 *
 * - `duration-500` overrides the 150ms default. That value is tuned for hover
 *   feedback, where the response has to feel instant; a reveal that fast reads
 *   as a flicker rather than as motion. The curve is still `ease-standard`.
 * - The transition is property-scoped. `transition-all` does not appear
 *   anywhere in this codebase and must not start here.
 * - Reduced motion is handled here rather than left to the global kill switch
 *   in globals.css. That switch collapses the duration, but the element would
 *   still start hidden and snap into place; skipping the reveal entirely starts
 *   it visible.
 *
 * Nothing above the fold should use this. A reveal on the hero means a blank
 * first frame, because the initial state is painted before the observer exists.
 */
export function Reveal({
  as = "div",
  delay = 0,
  className,
  children,
}: {
  as?: "div" | "section";
  /** Milliseconds, for staggering a row of cards. */
  delay?: number;
  className?: string;
  children: React.ReactNode;
}) {
  const enabled = useRevealEnabled();
  const ref = useRef<HTMLDivElement>(null);
  const [shown, setShown] = useState(false);

  useEffect(() => {
    if (!enabled) return;
    const el = ref.current;
    if (!el) return;

    const observer = new IntersectionObserver(
      (entries) => {
        if (!entries.some((entry) => entry.isIntersecting)) return;
        setShown(true);
        observer.disconnect();
      },
      // Threshold 0 with the viewport's bottom pulled in: a section fires once
      // its top passes 88% of the screen. A fractional threshold would never
      // resolve for a block taller than the viewport.
      { threshold: 0, rootMargin: "0px 0px -12% 0px" },
    );

    observer.observe(el);
    return () => observer.disconnect();
  }, [enabled]);

  // React renders whichever tag string it is handed; the cast only tells TS to
  // check the props against one member of the union.
  const Element = as as "div";
  const visible = shown || !enabled;

  return (
    <Element
      ref={ref}
      // `data-reveal` is what the <noscript> rule in the marketing layout
      // targets, so the page is still readable with scripting off.
      data-reveal=""
      style={delay ? { transitionDelay: `${delay}ms` } : undefined}
      className={cn(
        "transition-[opacity,transform] duration-500 ease-standard",
        visible ? "translate-y-0 opacity-100" : "translate-y-3 opacity-0",
        className,
      )}
    >
      {children}
    </Element>
  );
}

const MOTION_QUERY = "(prefers-reduced-motion: reduce)";

function subscribe(onChange: () => void) {
  const query = window.matchMedia(MOTION_QUERY);
  query.addEventListener("change", onChange);
  return () => query.removeEventListener("change", onChange);
}

/**
 * Whether this environment should reveal on scroll at all.
 *
 * Both halves of the question are reads from the environment rather than state,
 * so this is `useSyncExternalStore` and not `useState` + `useEffect` — the same
 * reasoning as `useIsHydrated`, and the reason there is no setState in an
 * effect body above. The server assumes yes, which is what the initial
 * hidden markup encodes; a client that disagrees re-renders once, visible.
 */
function useRevealEnabled(): boolean {
  return useSyncExternalStore(
    subscribe,
    () =>
      typeof IntersectionObserver !== "undefined" && !window.matchMedia(MOTION_QUERY).matches,
    () => true,
  );
}
