"use client";

/**
 * Renders any engine's `Explanation` (ADR-002).
 *
 * **This component must never know which engine produced its input.** No
 * switching on factor names, no special cases for health versus forecasting
 * versus the advisor. That constraint is what makes adding a factor to a rubric
 * a backend-only change, and it is the entire reason the contract is typed.
 *
 * If a future engine needs something this cannot render, the fix is to extend
 * the contract for everyone — not to branch here.
 *
 * Three deliberate choices:
 *
 * - **Contributions are drawn to scale.** A weight of 0.25 rendered as text
 *   beside a weight of 0.05 tells the user nothing about which mattered; a bar
 *   proportional to the contribution tells them instantly.
 * - **Direction is never colour alone.** Each factor carries an arrow glyph and
 *   a text label, so the decomposition survives a colourblind reader, greyscale
 *   printing, and forced-colors mode.
 * - **Caveats are not collapsed behind a disclosure.** They are the honest part
 *   of the output. A caveat nobody reads is a caveat that is not doing its job.
 */

import { ArrowDownRight, ArrowRight, ArrowUpRight, Info } from "lucide-react";

export type Direction = "positive" | "negative" | "neutral";

export interface Factor {
  name: string;
  value: string;
  raw_value: string;
  weight: string;
  contribution: string;
  direction: Direction;
  explanation: string;
}

export interface Explanation {
  verdict: string | null;
  score: string | null;
  confidence: string;
  method: string;
  data_window: { start: string; end: string; observation_days: number };
  factors: Factor[];
  caveats: string[];
  computed_at: string;
}

const DIRECTION = {
  positive: { Icon: ArrowUpRight, tone: "text-good", label: "helping" },
  negative: { Icon: ArrowDownRight, tone: "text-critical", label: "holding you back" },
  neutral: { Icon: ArrowRight, tone: "text-ink-muted", label: "neutral" },
} as const;

/** "0.812" -> "81%". A three-decimal confidence implies precision nobody has. */
function formatConfidence(value: string): string {
  return `${Math.round(Number(value) * 100)}%`;
}

function formatMethod(method: string): string {
  return method.replace(/_/g, " ");
}

export function ExplanationPanel({
  explanation,
  className = "",
}: {
  explanation: Explanation;
  className?: string;
}) {
  const { factors, caveats, confidence, method, data_window: window } = explanation;

  // "12,144.05 pts · weight 0.5" is nonsense on an insight: points and weights
  // are how a *rubric* decomposes a score, and an insight has no score. Where
  // there is nothing to contribute to, the factor's own value is the whole
  // story and the annotation is dropped rather than mislabelled.
  const scored = explanation.score !== null;

  // Scale bars against the largest contribution rather than against the score:
  // a factor contributing 25 of 87 should read as substantial, and dividing by
  // the total would render every bar as a sliver.
  const largest = factors.reduce(
    (max, f) => Math.max(max, Math.abs(Number(f.contribution))),
    0,
  );

  return (
    <section className={`space-y-4 ${className}`} data-testid="explanation-panel">
      <header className="flex flex-wrap items-baseline justify-between gap-2 type-meta text-ink-muted">
        <span>
          Based on {window.observation_days} days of history ({window.start} to {window.end})
        </span>
        <span className="tabular">
          {formatConfidence(confidence)} confidence · {formatMethod(method)}
        </span>
      </header>

      {factors.length > 0 && (
        <ol className="space-y-3" data-testid="factor-list">
          {factors.map((factor) => {
            const { Icon, tone, label } = DIRECTION[factor.direction];
            const magnitude = Math.abs(Number(factor.contribution));
            const share = largest > 0 ? (magnitude / largest) * 100 : 0;

            return (
              <li key={factor.name} className="space-y-1.5" data-testid="factor">
                <div className="flex flex-wrap items-baseline gap-x-2 gap-y-0.5">
                  <Icon className={`size-3.5 shrink-0 ${tone}`} aria-hidden />
                  <span className="type-body font-medium">{factor.name}</span>
                  <span className="tabular type-body text-ink-secondary">{factor.value}</span>
                  {/* The direction in words, so colour is never the only carrier. */}
                  <span className="sr-only">, {label}</span>
                  {scored && (
                    <span className="tabular ml-auto type-meta text-ink-muted">
                      {factor.contribution} pts · weight {factor.weight}
                    </span>
                  )}
                </div>

                {/* Decorative: every number in the bar is already in the text
                    above it, so a screen reader gains nothing from repeating it. */}
                {scored && (
                  <div
                    className="h-1 w-full overflow-hidden rounded-full bg-gridline"
                    aria-hidden
                  >
                    <div
                      className={`h-full rounded-full ${
                        factor.direction === "negative" ? "bg-critical" : "bg-series-1"
                      }`}
                      style={{ width: `${share}%` }}
                    />
                  </div>
                )}

                <p className="type-meta text-ink-secondary">{factor.explanation}</p>
              </li>
            );
          })}
        </ol>
      )}

      {caveats.length > 0 && (
        // No border. This panel is nearly always rendered inside a bordered
        // Section, and a bordered box inside a bordered box reads as a seam --
        // the raised surface alone is enough to separate it.
        <div className="space-y-1.5 rounded-card bg-surface-raised p-4" data-testid="caveats">
          <p className="flex items-center gap-1.5 type-meta font-medium text-ink-secondary">
            <Info className="size-3.5 shrink-0" aria-hidden />
            What this does not account for
          </p>
          <ul className="list-disc space-y-1 pl-4 type-meta text-ink-muted">
            {caveats.map((caveat) => (
              <li key={caveat}>{caveat}</li>
            ))}
          </ul>
        </div>
      )}

      {factors.length === 0 && caveats.length === 0 && (
        <p className="type-body text-ink-muted">No decomposition available.</p>
      )}
    </section>
  );
}
