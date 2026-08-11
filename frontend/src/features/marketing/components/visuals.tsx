import { Fragment } from "react";

import { Section } from "@/components/ui/section";

/**
 * The home screen's four illustrations.
 *
 * These are mock product surfaces rather than drawings: what Frugal sells is a
 * verdict you can take apart, and the only honest way to show that is to show
 * one. Every figure below is illustrative and none of it comes from an API, so
 * each panel is `aria-hidden` — the copy beside it already says everything a
 * screen reader needs, and reading out invented numbers would be worse.
 *
 * All four are monotone. The saturated palette is reserved for real data and
 * status, never for chrome, and a fabricated chart is not real data.
 *
 * Each is a bordered Section wrapping a `surface-raised` block: a bordered
 * Section may never contain another one (docs/05-ui-wireframes.md section 2.1).
 */

const FRAME = "space-y-4";
const INNER = "rounded-inner bg-surface-raised p-4";

/* --- 1. the flagship: a verdict and the factors under it ------------------ */

const FACTORS = [
  { label: "Emergency fund after purchase", value: "0.8 months", delta: "−24.0" },
  { label: "Forecast trough", value: "₹11,200", delta: "−8.4" },
  { label: "Savings rate", value: "51.4%", delta: "+13.5" },
  { label: "Debt-to-income", value: "18%", delta: "+11.2" },
];

export function AdvisorVisual() {
  return (
    <Section variant="bordered" aria-hidden className={FRAME}>
      <div className="flex items-center justify-between gap-3">
        <span className="type-eyebrow text-ink-muted">Should I buy it?</span>
        <span className="type-meta text-ink-muted">Confidence 77%</span>
      </div>

      <div className={`${INNER} space-y-1`}>
        <div className="flex flex-wrap items-baseline justify-between gap-2">
          <span className="type-title">Wait</span>
          <span className="tabular type-title">
            48
            <span className="type-body text-ink-muted">/100</span>
          </span>
        </div>
        <p className="type-body text-ink-secondary">
          You could afford this by 15 November 2026.
        </p>
      </div>

      <dl>
        {FACTORS.map((factor) => (
          <div
            key={factor.label}
            className="grid grid-cols-[1fr_auto_auto] items-baseline gap-x-4 border-t border-hairline py-2 first:border-t-0"
          >
            <dt className="truncate type-body text-ink-secondary">{factor.label}</dt>
            <dd className="tabular type-body">{factor.value}</dd>
            <dd className="tabular w-14 text-right type-body text-ink-muted">{factor.delta}</dd>
          </div>
        ))}
      </dl>
    </Section>
  );
}

/* --- 2. what the purchase would actually cost you -------------------------- */

const IMPACT = [
  { label: "Balance trough", before: "₹64,800", after: "₹11,200" },
  { label: "Emergency fund", before: "3.2 months", after: "0.8 months" },
  { label: "Savings rate", before: "51.4%", after: "38.1%" },
  { label: "Laptop fund ETA", before: "Mar 2027", after: "Oct 2027" },
];

export function SimulationVisual() {
  return (
    <Section variant="bordered" aria-hidden className={FRAME}>
      <div className="flex items-center justify-between gap-3">
        <span className="type-eyebrow text-ink-muted">Purchase impact</span>
        <span className="type-meta text-ink-muted">₹1,34,900</span>
      </div>

      {/* One grid rather than a grid per row: `auto` columns are measured
       * within their own grid, so a row-per-grid layout aligns only if every
       * numeric column is pinned to a fixed width — which is what made this
       * truncate the labels at 375px. Here the columns size themselves to the
       * widest cell and the label keeps the rest. */}
      <div className={`${INNER} grid grid-cols-[1fr_auto_auto] gap-x-4`}>
        <span className="type-eyebrow text-ink-muted">Measure</span>
        <span className="text-right type-eyebrow text-ink-muted">Now</span>
        <span className="text-right type-eyebrow text-ink-muted">After</span>

        {IMPACT.map((row) => (
          <Fragment key={row.label}>
            <span className="border-t border-hairline py-2 type-body text-ink-secondary">
              {row.label}
            </span>
            <span className="tabular border-t border-hairline py-2 text-right type-body text-ink-muted">
              {row.before}
            </span>
            <span className="tabular border-t border-hairline py-2 text-right type-body">
              {row.after}
            </span>
          </Fragment>
        ))}
      </div>

      <p className="type-meta text-ink-muted">
        Opportunity cost in the units you think in, not a percentage.
      </p>
    </Section>
  );
}

/* --- 3. a score that decomposes ------------------------------------------- */

const SUB_METRICS = [
  { label: "Savings rate", percent: 82 },
  { label: "Emergency fund", percent: 41 },
  { label: "Debt-to-income", percent: 76 },
  { label: "Budget discipline", percent: 64 },
];

export function HealthVisual() {
  return (
    <Section variant="bordered" aria-hidden className={FRAME}>
      <span className="type-eyebrow text-ink-muted">Financial health</span>

      <div className={`${INNER} space-y-1`}>
        <div className="flex items-baseline gap-2">
          <span className="tabular type-display md:type-hero">68</span>
          <span className="type-body text-ink-muted">/ 100</span>
        </div>
        <p className="type-body text-ink-secondary">
          Six sub-metrics, each with its own weight and contribution.
        </p>
      </div>

      <dl className="space-y-3">
        {SUB_METRICS.map((metric) => (
          <div key={metric.label} className="space-y-1.5">
            <div className="flex items-baseline justify-between gap-3">
              <dt className="type-body text-ink-secondary">{metric.label}</dt>
              <dd className="tabular type-meta text-ink-muted">{metric.percent}</dd>
            </div>
            {/* Track and fill, not a coloured bar: the magnitude is the whole
             * message and ink carries it at any contrast. */}
            <div className="h-1 rounded-swatch bg-gridline">
              <div
                className="h-1 rounded-swatch bg-ink"
                style={{ width: `${metric.percent}%` }}
              />
            </div>
          </div>
        ))}
      </dl>
    </Section>
  );
}

/* --- 4. a forecast that admits how sure it is ----------------------------- */

export function ForecastVisual() {
  return (
    <Section variant="bordered" aria-hidden className={FRAME}>
      <div className="flex items-center justify-between gap-3">
        <span className="type-eyebrow text-ink-muted">Projected balance</span>
        <span className="type-meta text-ink-muted">EWMA + seasonal-naive</span>
      </div>

      <div className={INNER}>
        <svg viewBox="0 0 320 132" className="h-auto w-full" role="presentation">
          {/* Confidence band: it opens only past today, because there is no
           * uncertainty about what already happened. */}
          <path
            d="M164 74 L216 70 L268 38 L312 20 L312 74 L268 80 L216 100 L164 74 Z"
            className="fill-current text-gridline"
          />
          {/* The user's floor. Crossing it is the shortfall the forecast exists
           * to catch. */}
          <line
            x1="8"
            y1="112"
            x2="312"
            y2="112"
            className="stroke-current text-baseline"
            strokeWidth="1"
            strokeDasharray="2 4"
          />
          {/* Today. */}
          <line
            x1="164"
            y1="12"
            x2="164"
            y2="120"
            className="stroke-current text-gridline"
            strokeWidth="1"
          />
          {/* History: solid. */}
          <path
            d="M8 88 L60 82 L112 96 L164 74"
            fill="none"
            className="stroke-current text-ink"
            strokeWidth="2"
            strokeLinejoin="round"
            strokeLinecap="round"
          />
          {/* Projection: dashed, so the two are never read as the same claim. */}
          <path
            d="M164 74 L216 84 L268 58 L312 46"
            fill="none"
            className="stroke-current text-ink-muted"
            strokeWidth="2"
            strokeDasharray="4 4"
            strokeLinejoin="round"
            strokeLinecap="round"
          />
        </svg>

        <div className="mt-2 flex justify-between type-meta text-ink-muted">
          <span>Today</span>
          <span>30 d</span>
          <span>60 d</span>
          <span>90 d</span>
        </div>
      </div>

      <p className="type-meta text-ink-muted">
        Method and data window are named on every response, never implied.
      </p>
    </Section>
  );
}

/* --- 5. getting data in --------------------------------------------------- */

const EXTRACTED = [
  { field: "Merchant", value: "Blue Tokai", confidence: "0.97", flagged: false },
  { field: "Total", value: "₹1,240.00", confidence: "0.94", flagged: false },
  { field: "Date", value: "12 March 2026", confidence: "0.61", flagged: true },
];

export function ImportVisual() {
  return (
    <Section variant="bordered" aria-hidden className={FRAME}>
      <div className="flex items-center justify-between gap-3">
        <span className="type-eyebrow text-ink-muted">Receipt extraction</span>
        <span className="type-meta text-ink-muted">Needs review</span>
      </div>

      {/* One grid, for the same reason as the impact table above: a grid per
       * row measures its `auto` columns independently, so the value column
       * would start at a different x on every line. */}
      <div className={`${INNER} grid grid-cols-[auto_1fr_auto] gap-x-4`}>
        <span className="type-eyebrow text-ink-muted">Field</span>
        <span className="type-eyebrow text-ink-muted">Value</span>
        <span className="text-right type-eyebrow text-ink-muted">Conf.</span>

        {EXTRACTED.map((row) => (
          <Fragment key={row.field}>
            <span className="border-t border-hairline py-2 type-body text-ink-secondary">
              {row.field}
            </span>
            <span className="truncate border-t border-hairline py-2 type-body">
              {row.value}
            </span>
            {/* A flagged field says so with a mark, not a hue. Status is never
             * colour alone (NFR-6), and here there is no colour at all. */}
            <span className="tabular border-t border-hairline py-2 text-right type-body text-ink-muted">
              {row.confidence}
              {row.flagged && " ⚑"}
            </span>
          </Fragment>
        ))}
      </div>

      <p className="type-meta text-ink-muted">
        Nothing below threshold is committed on its own. It waits for you.
      </p>
    </Section>
  );
}
