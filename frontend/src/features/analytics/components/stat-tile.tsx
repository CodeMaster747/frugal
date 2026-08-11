"use client";

import { Sparkline } from "@/components/charts/primitives";
import type { SeriesSlot } from "@/components/charts/chart-container";
import { cn } from "@/lib/utils";

/**
 * A headline number with its change and, optionally, its shape over time.
 *
 * A stat tile rather than a one-bar chart: a single current value has no
 * comparison to make, so a bar encodes nothing the number does not already say.
 */
export function StatTile({
  label,
  value,
  delta,
  deltaLabel,
  trend,
  trendKey,
  slot = 1,
  caveat,
}: {
  label: string;
  value: string;
  delta?: number | null;
  deltaLabel?: string;
  trend?: Record<string, unknown>[];
  trendKey?: string;
  slot?: SeriesSlot;
  caveat?: string;
}) {
  const direction = delta == null ? "flat" : delta > 0 ? "up" : delta < 0 ? "down" : "flat";

  return (
    <div className="flex flex-col gap-2 rounded-card border border-hairline bg-surface p-4">
      <p className="type-eyebrow text-ink-muted">{label}</p>
      {/* Proportional figures on a standalone number; tabular is for columns.
       * `type-title` rather than a one-off size: the headline number on a tile
       * and the page's own h1 are the same step in the scale. */}
      <p className="type-title">{value}</p>

      {delta != null && (
        <p
          className={cn(
            "flex items-center gap-1 type-meta",
            direction === "up" && "text-delta-up",
            direction === "down" && "text-serious",
            direction === "flat" && "text-ink-muted",
          )}
        >
          {/* An arrow and a number, so direction is never colour alone. */}
          <span aria-hidden>{direction === "up" ? "▲" : direction === "down" ? "▼" : "▬"}</span>
          <span className="sr-only">
            {direction === "up" ? "up" : direction === "down" ? "down" : "unchanged"}
          </span>
          {Math.abs(delta).toFixed(1)}%{deltaLabel ? ` ${deltaLabel}` : ""}
        </p>
      )}

      {caveat && <p className="type-meta text-ink-muted">{caveat}</p>}

      {trend && trendKey && trend.length > 1 && (
        <div aria-hidden className="mt-auto pt-1">
          <Sparkline data={trend} yKey={trendKey} slot={slot} />
        </div>
      )}
    </div>
  );
}
