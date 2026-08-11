import type { ComponentProps, ReactNode } from "react";

import { cn } from "@/lib/utils";

/**
 * The one container in the app.
 *
 * Before this existed, `rounded-card border border-hairline
 * bg-surface p-5` was copy-pasted 19 times across 20 subtly different variants,
 * and card padding had six values with no rule about which surface got which.
 * Every grouped block now goes through here.
 *
 * Two variants, and the choice is semantic rather than visual:
 *
 * - `plain` — a heading, a hairline rule, content. For page-level groupings,
 *   where a border would be drawing a box around something that is not an
 *   object. This is the default because most sections are this.
 * - `bordered` — a real surface. Only for blocks that *are* a discrete object:
 *   charts, forms, tabular data, feed items.
 *
 * A `bordered` Section must never contain another `bordered` Section. Nested
 * borders read as a seam; use `bg-surface-raised` for the inner block instead.
 */

type Padding = "default" | "compact" | "flush";

export interface SectionProps extends Omit<ComponentProps<"section">, "title"> {
  title?: ReactNode;
  /** Sits under the title, quieter. For the caveat or scope of the section. */
  description?: ReactNode;
  /** Trailing control on the header row — a button, a toggle, a link. */
  action?: ReactNode;
  variant?: "plain" | "bordered";
  /** `flush` lets children run edge to edge — for divided lists and tables. */
  padding?: Padding;
  /** `3` when the Section is already inside one that owns the `h2`. */
  headingLevel?: 2 | 3;
  /** `form` where the block is itself the form, to avoid a pointless wrapper. */
  as?: "section" | "div" | "form";
}

const BODY_PADDING: Record<Padding, string> = {
  default: "p-6",
  compact: "p-4",
  flush: "",
};

export function Section({
  title,
  description,
  action,
  variant = "plain",
  padding = "default",
  headingLevel = 2,
  as = "section",
  className,
  children,
  ...props
}: SectionProps) {
  // React renders whichever tag string it is handed. The cast only tells TS to
  // check the spread against one member of the union -- all three accept the
  // props in `SectionProps`, but a union of intrinsic elements has incompatible
  // `ref` types and cannot be spread into directly.
  const Element = as as "section";
  const Heading = headingLevel === 2 ? "h2" : "h3";
  const bordered = variant === "bordered";
  const flush = padding === "flush";
  const hasHeader = Boolean(title || action);

  return (
    <Element
      className={cn(
        bordered && "rounded-card border border-hairline bg-surface",
        bordered && !flush && BODY_PADDING[padding],
        className,
      )}
      {...props}
    >
      {hasHeader && (
        <div
          className={cn(
            "flex flex-wrap items-start justify-between gap-3",
            // A plain section has no border of its own, so the rule under its
            // heading is what separates it from what came before. A bordered
            // one is already enclosed and only needs space -- unless its body
            // is flush, in which case the rule marks where the body begins.
            !bordered && "border-b border-hairline pb-3",
            bordered && !flush && "mb-4",
            bordered && flush && "border-b border-hairline px-4 pt-4 pb-3",
          )}
        >
          <div className="min-w-0 space-y-0.5">
            {title && <Heading className="type-section">{title}</Heading>}
            {description && <p className="type-meta text-ink-muted">{description}</p>}
          </div>
          {action && <div className="flex shrink-0 items-center gap-2">{action}</div>}
        </div>
      )}

      {/* Only a plain section with a heading needs a body wrapper, to clear the
       * rule above it. Every other combination puts its spacing on the
       * container, so wrapping would just add a node. */}
      {!bordered && hasHeader ? <div className="pt-4">{children}</div> : children}
    </Element>
  );
}
