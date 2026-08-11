import type { ComponentProps, ReactNode } from "react";
import { useId } from "react";

import { cn } from "@/lib/utils";

/**
 * A labelled checkbox with the whole row as its hit target.
 *
 * The native input is kept and styled with `accent-color` rather than hidden
 * behind a custom box: an appearance-none checkbox has to re-implement the
 * checked, indeterminate, disabled and forced-colors states, and gets the last
 * one wrong nearly every time.
 *
 * The row is the target rather than the 16px box, which is what makes this clear
 * the 44px minimum (NFR-6) without drawing a 44px square.
 */
export function Checkbox({
  label,
  description,
  className,
  id: providedId,
  ...props
}: Omit<ComponentProps<"input">, "type"> & {
  label: ReactNode;
  /** Sits under the label, quieter. For what the setting actually does. */
  description?: ReactNode;
}) {
  const generatedId = useId();
  const id = providedId ?? generatedId;
  const descriptionId = `${id}-description`;

  return (
    <div className={cn("flex items-start gap-3 py-2", className)}>
      <input
        id={id}
        type="checkbox"
        aria-describedby={description ? descriptionId : undefined}
        className="mt-0.5 size-4 shrink-0 cursor-pointer accent-series-1 disabled:cursor-not-allowed disabled:opacity-50"
        {...props}
      />
      <div className="min-w-0 space-y-0.5">
        <label htmlFor={id} className="block cursor-pointer type-body font-medium">
          {label}
        </label>
        {description && (
          <p id={descriptionId} className="type-meta text-ink-muted">
            {description}
          </p>
        )}
      </div>
    </div>
  );
}
