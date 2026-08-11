import type { ComponentProps } from "react";
import { useId } from "react";

import { cn } from "@/lib/utils";

/**
 * A labelled input with inline validation messaging.
 *
 * The error is wired via `aria-describedby` and announced with `role="alert"`,
 * so a screen-reader user learns what went wrong -- colour and position alone
 * would not convey it (NFR-6).
 */
export function Field({
  label,
  error,
  hint,
  className,
  id: providedId,
  ...props
}: ComponentProps<"input"> & { label: string; error?: string; hint?: string }) {
  const generatedId = useId();
  const id = providedId ?? generatedId;
  const errorId = `${id}-error`;
  const hintId = `${id}-hint`;

  return (
    <div className="space-y-2">
      {/* The label is ink at medium weight; the placeholder is muted and regular.
       * Two levels apart, so nobody reads a placeholder as a filled value. */}
      <label htmlFor={id} className="block type-body font-medium">
        {label}
      </label>
      <input
        id={id}
        aria-invalid={error ? true : undefined}
        aria-describedby={cn(error && errorId, hint && hintId) || undefined}
        className={cn(
          // Transparent, with the border doing the work: an input filled with
          // `bg-surface` vanishes inside a bordered Section, and one filled with
          // `bg-page` vanishes on the page. `border-gridline` rather than
          // hairline because a control has to look operable.
          "h-11 w-full rounded-control border border-gridline bg-transparent px-3 type-body text-ink transition-colors",
          "placeholder:text-ink-muted",
          // Focus shifts the border rather than adding a glow. The 2px
          // :focus-visible outline in globals.css is the accessible signal and
          // stays; this is only what makes the control feel responsive.
          "hover:border-baseline focus-visible:border-baseline",
          error && "border-critical",
          className,
        )}
        {...props}
      />
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
