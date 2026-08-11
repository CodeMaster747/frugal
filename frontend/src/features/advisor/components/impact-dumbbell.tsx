"use client";

/**
 * Before/after for each measure the purchase moves.
 *
 * A dumbbell rather than paired bars: the question is *how far a value moved*,
 * and a dumbbell encodes that as the one thing the eye is best at judging —
 * length between two points. Two bars side by side make the reader compare
 * heights and infer the difference, which is the same information presented as
 * arithmetic homework.
 *
 * Direction is never colour alone. Each row states the delta in words beside
 * the visual, and the accessible label carries the whole comparison.
 */

import { formatMoney } from "@/lib/format";

interface Row {
  label: string;
  before: number;
  after: number;
  format: (value: number) => string;
  /** Whether a *decrease* is bad. Savings rate falling is bad; debt is not. */
  lowerIsWorse?: boolean;
}

export function ImpactDumbbell({
  before,
  after,
  estimatedHealth,
}: {
  before: {
    liquid_savings: string;
    emergency_fund_months: string;
    health_score: string | null;
  };
  after: {
    liquid_savings: string;
    emergency_fund_months: string;
    health_score: string | null;
  };
  estimatedHealth?: boolean;
}) {
  const rows: Row[] = [
    {
      label: "Liquid savings",
      before: Number(before.liquid_savings),
      after: Number(after.liquid_savings),
      format: (v) => formatMoney(String(v), "INR"),
      lowerIsWorse: true,
    },
    {
      label: "Emergency fund",
      before: Number(before.emergency_fund_months),
      after: Number(after.emergency_fund_months),
      format: (v) => `${v.toFixed(1)} months`,
      lowerIsWorse: true,
    },
  ];

  if (before.health_score !== null && after.health_score !== null) {
    rows.push({
      label: estimatedHealth ? "Health score (estimated)" : "Health score",
      before: Number(before.health_score),
      after: Number(after.health_score),
      format: (v) => `${Math.round(v)}/100`,
      lowerIsWorse: true,
    });
  }

  return (
    <ul className="space-y-4" data-testid="impact-dumbbell">
      {rows.map((row) => {
        const span = Math.max(row.before, row.after, 1);
        const beforePct = (row.before / span) * 100;
        const afterPct = (Math.max(row.after, 0) / span) * 100;
        const worse = row.lowerIsWorse ? row.after < row.before : row.after > row.before;

        return (
          <li key={row.label} className="space-y-1.5">
            <div className="flex flex-wrap items-baseline justify-between gap-2">
              <span className="type-body font-medium">{row.label}</span>
              <span className="tabular type-body">
                <span className="text-ink-muted">{row.format(row.before)}</span>
                <span className="mx-1.5 text-ink-muted" aria-hidden>
                  →
                </span>
                <span className={worse ? "text-critical" : "text-good"}>
                  {row.format(row.after)}
                </span>
              </span>
            </div>

            {/* Decorative: the numbers above and the label below say everything
                this shows. */}
            <div className="relative h-4" aria-hidden>
              <div className="absolute inset-x-0 top-1/2 h-px -translate-y-1/2 bg-gridline" />
              <div
                className={`absolute top-1/2 h-1 -translate-y-1/2 rounded-full ${
                  worse ? "bg-critical/30" : "bg-good/30"
                }`}
                style={{
                  left: `${Math.min(beforePct, afterPct)}%`,
                  width: `${Math.abs(beforePct - afterPct)}%`,
                }}
              />
              <span
                className="absolute top-1/2 size-2.5 -translate-x-1/2 -translate-y-1/2 rounded-full border-2 border-surface bg-ink-muted"
                style={{ left: `${beforePct}%` }}
              />
              <span
                className={`absolute top-1/2 size-3 -translate-x-1/2 -translate-y-1/2 rounded-full border-2 border-surface ${
                  worse ? "bg-critical" : "bg-good"
                }`}
                style={{ left: `${afterPct}%` }}
              />
            </div>

            <p className="sr-only">
              {row.label} goes from {row.format(row.before)} to {row.format(row.after)},{" "}
              {worse ? "a worsening" : "an improvement"}.
            </p>
          </li>
        );
      })}
    </ul>
  );
}
