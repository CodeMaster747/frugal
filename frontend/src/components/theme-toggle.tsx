"use client";

import { Monitor, Moon, Sun } from "lucide-react";
import { useTheme } from "next-themes";

import { useIsHydrated } from "@/hooks/use-is-hydrated";
import { cn } from "@/lib/utils";

const OPTIONS = [
  { value: "light", label: "Light", Icon: Sun },
  { value: "dark", label: "Dark", Icon: Moon },
  { value: "system", label: "System", Icon: Monitor },
] as const;

/**
 * Three-way theme control.
 *
 * "System" is a distinct choice rather than an absence of one, so a user who
 * wants to follow their OS can say so explicitly.
 */
export function ThemeToggle({ className }: { className?: string }) {
  const { theme, setTheme } = useTheme();

  // The server cannot know the resolved theme, so rendering the active state
  // before hydration would produce a mismatch and a flash of the wrong icon.
  const hydrated = useIsHydrated();

  return (
    <div
      role="radiogroup"
      aria-label="Colour theme"
      className={cn(
        "inline-flex gap-0.5 rounded-control border border-hairline bg-surface p-0.5",
        className,
      )}
    >
      {OPTIONS.map(({ value, label, Icon }) => {
        const active = hydrated && theme === value;
        return (
          <button
            key={value}
            type="button"
            role="radio"
            aria-checked={active}
            aria-label={label}
            onClick={() => setTheme(value)}
            className={cn(
              // rounded-inner, not an arbitrary 4px: the container is 6px with
              // 2px of padding, so the nested corner has to be 6 - 2 to stay
              // concentric with it.
              "inline-flex size-8 items-center justify-center rounded-inner transition-colors",
              "hover:bg-page focus-visible:outline-2",
              active ? "bg-page text-ink" : "text-ink-muted hover:text-ink",
            )}
          >
            <Icon className="size-4" aria-hidden />
          </button>
        );
      })}
    </div>
  );
}
