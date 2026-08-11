import type { ComponentProps, ReactNode } from "react";

import { cn } from "@/lib/utils";

/**
 * "Nothing here yet."
 *
 * The same dashed block was written ten times across nine files with four
 * different paddings. A dashed border rather than a solid one is the whole point:
 * it says the container is waiting for content, where a solid border would
 * present the absence as if it were content.
 *
 * Deliberately left-aligned and modestly padded. A centred, generously spaced
 * empty state draws more attention to the emptiness than to the way out of it.
 */
export function Empty({
  icon,
  action,
  className,
  children,
  ...props
}: ComponentProps<"div"> & {
  /** Optional lucide glyph. A prop rather than something the caller inlines into
   *  `children`, because Tailwind's preflight sets `svg { display: block }` — an
   *  icon dropped straight into the paragraph takes a line to itself. */
  icon?: ReactNode;
  /** The way out — usually the button that creates the missing thing. */
  action?: ReactNode;
}) {
  return (
    <div
      className={cn(
        "rounded-card border border-dashed border-hairline p-6",
        action && "flex flex-wrap items-center justify-between gap-4",
        className,
      )}
      {...props}
    >
      <div className="flex min-w-0 items-start gap-2.5">
        {icon && (
          <span className="mt-0.5 shrink-0 text-ink-muted [&_svg]:size-4" aria-hidden>
            {icon}
          </span>
        )}
        <p className="type-body text-ink-secondary">{children}</p>
      </div>
      {action && <div className="flex shrink-0 items-center gap-2">{action}</div>}
    </div>
  );
}
