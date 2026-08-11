import type { ComponentProps } from "react";
import { useId } from "react";

import { cn } from "@/lib/utils";

/**
 * A labelled native select, wired the same way `Field` is.
 *
 * Native rather than a custom listbox: the platform control gets keyboard
 * support, typeahead, and a usable picker on touch for free, and none of the six
 * call sites needs multi-select, search, or rich option content. A Radix Select
 * here would be more markup for less behaviour.
 *
 * The `sm` size exists for the in-table category picker, which sits in a dense
 * row where a 44px control would set the row height.
 */
export function Select({
  label,
  hideLabel,
  error,
  hint,
  size = "md",
  className,
  id: providedId,
  children,
  ...props
}: Omit<ComponentProps<"select">, "size"> & {
  label: string;
  /** Keeps the label for assistive tech but drops it visually — for controls
   *  inside a table row, where the column header already names the field. */
  hideLabel?: boolean;
  error?: string;
  hint?: string;
  size?: "md" | "sm";
}) {
  const generatedId = useId();
  const id = providedId ?? generatedId;
  const errorId = `${id}-error`;
  const hintId = `${id}-hint`;
  const describedBy = [error && errorId, hint && !error && hintId].filter(Boolean).join(" ");

  return (
    <div className={cn(!hideLabel && "space-y-2")}>
      <label htmlFor={id} className={cn(hideLabel ? "sr-only" : "block type-body font-medium")}>
        {label}
      </label>
      <select
        id={id}
        aria-invalid={error ? true : undefined}
        aria-describedby={describedBy || undefined}
        className={cn(SELECT_BASE, SELECT_SIZE[size], error && "border-critical", className)}
        {...props}
      >
        {children}
      </select>
      {hint && !error && (
        <p id={hintId} className="type-meta text-ink-muted">
          {hint}
        </p>
      )}
      {error && (
        <p id={errorId} role="alert" className="type-meta text-critical">
          {error}
        </p>
      )}
    </div>
  );
}

/**
 * Transparent, not a surface colour.
 *
 * A control filled with `bg-surface` disappears inside a bordered Section, and
 * one filled with `bg-page` disappears on the page. Letting the border do the
 * work makes the field read correctly wherever it is placed. `border-gridline`
 * rather than `border-hairline` because a form control has to look operable --
 * hairline is for separating blocks, not for outlining something you can type in.
 */
const SELECT_BASE =
  "type-body w-full rounded-control border border-gridline bg-transparent text-ink transition-colors hover:border-baseline focus-visible:border-baseline";

const SELECT_SIZE: Record<"md" | "sm", string> = {
  // 44px: the minimum touch target (NFR-6).
  md: "h-11 px-3",
  sm: "type-meta h-9 px-2",
};

export { SELECT_BASE, SELECT_SIZE };
