"use client";

import {
  Area,
  AreaChart,
  Bar,
  BarChart,
  CartesianGrid,
  ComposedChart,
  Line,
  LineChart,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

import { SERIES_VAR, type SeriesSlot } from "./chart-container";
import { formatMoney } from "@/lib/format";

/**
 * Chart marks.
 *
 * Shared rules, applied here once rather than per chart:
 *
 * - **One y-axis, never two.** Two measures of different scale become two
 *   charts or an indexed series. A dual axis lets the author place the crossing
 *   point wherever they like, which is why it is the most misread chart there
 *   is.
 * - Thin marks, recessive grid, 4px rounded ends anchored to the baseline.
 * - A 2px surface gap between adjacent fills, so bars read as separate.
 * - Selective labels: values live in the tooltip and the data table, not
 *   stamped on every point.
 */

const AXIS = { stroke: "var(--ink-muted)", fontSize: 11 };
const GRID = "var(--gridline)";

/** Compact axis ticks: "₹1.2L" beats "₹120,000.00" in 40px of gutter. */
const compact = (value: number) => formatMoney(String(value), "INR", { compact: true });

function ChartTooltip({
  active,
  payload,
  label,
  formatter,
}: {
  active?: boolean;
  payload?: { name?: string; value?: number; color?: string; dataKey?: string }[];
  label?: string;
  formatter?: (value: number) => string;
}) {
  if (!active || !payload?.length) return null;

  return (
    // The one shadow in the app, and it stays. A tooltip genuinely floats above
    // the chart it describes; everything else gets its depth from a surface step.
    <div className="rounded-control border border-hairline bg-surface-raised px-3 py-2 type-meta shadow-sm">
      <p className="mb-1 font-medium text-ink-secondary">{label}</p>
      <ul className="space-y-0.5">
        {payload.map((entry) => (
          <li key={entry.dataKey} className="flex items-center gap-2">
            <span
              className="size-2 shrink-0 rounded-swatch"
              style={{ backgroundColor: entry.color }}
              aria-hidden
            />
            <span className="text-ink-secondary">{entry.name}</span>
            <span className="tabular ml-auto font-medium">
              {formatter ? formatter(entry.value ?? 0) : entry.value}
            </span>
          </li>
        ))}
      </ul>
    </div>
  );
}

export interface SeriesSpec {
  key: string;
  label: string;
  slot: SeriesSlot;
}

/** Grouped bars — for telling two distinct series apart (income vs expense). */
export function GroupedBars<Row extends Record<string, unknown>>({
  data,
  xKey,
  series,
  height = 220,
}: {
  data: Row[];
  xKey: string;
  series: SeriesSpec[];
  height?: number;
}) {
  return (
    <ResponsiveContainer width="100%" height={height}>
      <BarChart data={data} margin={{ top: 4, right: 4, bottom: 0, left: -12 }} barGap={2}>
        <CartesianGrid stroke={GRID} vertical={false} />
        <XAxis dataKey={xKey} tickLine={false} axisLine={false} tick={AXIS} />
        <YAxis
          tickFormatter={compact}
          tickLine={false}
          axisLine={false}
          tick={AXIS}
          width={56}
        />
        <Tooltip
          cursor={{ fill: "var(--gridline)", opacity: 0.35 }}
          content={<ChartTooltip formatter={(v) => formatMoney(String(v))} />}
        />
        {series.map((s) => (
          <Bar
            key={s.key}
            dataKey={s.key}
            name={s.label}
            fill={SERIES_VAR[s.slot]}
            radius={[4, 4, 0, 0]}
            maxBarSize={28}
          />
        ))}
      </BarChart>
    </ResponsiveContainer>
  );
}

/** A single trend line. One series needs no legend — the title names it. */
export function TrendLine<Row extends Record<string, unknown>>({
  data,
  xKey,
  yKey,
  slot = 1,
  height = 200,
  formatter,
  fitDomain = false,
}: {
  data: Row[];
  xKey: string;
  yKey: string;
  slot?: SeriesSlot;
  height?: number;
  formatter?: (value: number) => string;
  /**
   * Fit the y-axis to the data instead of anchoring at zero.
   *
   * Opt-in, because a zero baseline is the honest default for anything
   * measured *from* zero — spending, income, a count. It is the wrong default
   * for a **price**: ₹70,000 to ₹87,000 drawn from an origin of zero is a
   * near-flat line in the top fifth of the chart, and the movement is the whole
   * question being asked. Net worth and price history opt in; bar charts never
   * should.
   */
  fitDomain?: boolean;
}) {
  return (
    <ResponsiveContainer width="100%" height={height}>
      <LineChart data={data} margin={{ top: 4, right: 8, bottom: 0, left: -12 }}>
        <CartesianGrid stroke={GRID} vertical={false} />
        <XAxis dataKey={xKey} tickLine={false} axisLine={false} tick={AXIS} />
        <YAxis
          domain={fitDomain ? ["dataMin", "dataMax"] : undefined}
          tickFormatter={(v) => (formatter ? formatter(v) : compact(v))}
          tickLine={false}
          axisLine={false}
          tick={AXIS}
          width={56}
        />
        <Tooltip
          content={<ChartTooltip formatter={formatter ?? ((v) => formatMoney(String(v)))} />}
        />
        <Line
          type="monotone"
          dataKey={yKey}
          stroke={SERIES_VAR[slot]}
          strokeWidth={2}
          // ≥8px hit target on hover; no dot at rest, so the line stays thin.
          dot={false}
          activeDot={{ r: 4, strokeWidth: 2, stroke: "var(--surface)" }}
          connectNulls={false}
        />
      </LineChart>
    </ResponsiveContainer>
  );
}

/** Sparkline for a stat tile: shape only, no axes, no labels. */
export function Sparkline<Row extends Record<string, unknown>>({
  data,
  yKey,
  slot = 1,
}: {
  data: Row[];
  yKey: string;
  slot?: SeriesSlot;
}) {
  return (
    <ResponsiveContainer width="100%" height={36}>
      <AreaChart data={data} margin={{ top: 2, right: 0, bottom: 0, left: 0 }}>
        <Area
          type="monotone"
          dataKey={yKey}
          stroke={SERIES_VAR[slot]}
          strokeWidth={1.5}
          // The fill is depth, not a second series, so it stays faint.
          fill={SERIES_VAR[slot]}
          fillOpacity={0.12}
          dot={false}
        />
      </AreaChart>
    </ResponsiveContainer>
  );
}

/**
 * A single ratio against a limit.
 *
 * A meter, not a two-slice pie: the reader is comparing one length to another,
 * which is the one thing position-on-a-common-scale does best.
 */
export function Meter({
  value,
  limit,
  status = "good",
}: {
  value: number;
  limit: number;
  status?: "good" | "warning" | "over";
}) {
  const pct = limit > 0 ? Math.min((value / limit) * 100, 100) : 0;
  const color =
    status === "over"
      ? "var(--status-critical)"
      : status === "warning"
        ? "var(--status-warning)"
        : "var(--series-1)";

  return (
    <div
      className="h-2 w-full overflow-hidden rounded-full bg-gridline"
      role="meter"
      aria-valuenow={Math.round(pct)}
      aria-valuemin={0}
      aria-valuemax={100}
    >
      <div
        className="h-full rounded-full"
        style={{ width: `${pct}%`, backgroundColor: color }}
      />
    </div>
  );
}

/**
 * Horizontal stacked bar for part-to-whole.
 *
 * Horizontal because category names are long, and a 2px surface gap between
 * segments so adjacent fills read as separate rather than blending.
 */
export function StackedShare({
  segments,
}: {
  segments: { label: string; pct: number; slot: SeriesSlot }[];
}) {
  return (
    <div className="flex h-3 w-full gap-[2px] overflow-hidden rounded-full bg-gridline">
      {segments.map((s) => (
        <span
          key={s.label}
          style={{ width: `${s.pct}%`, backgroundColor: SERIES_VAR[s.slot] }}
          title={`${s.label} ${s.pct.toFixed(1)}%`}
        />
      ))}
    </div>
  );
}

/**
 * A projection with its confidence band.
 *
 * **The band is one hue at low opacity, not a second series.** Drawing p10 and
 * p90 as their own lines would put three lines on the chart and imply three
 * predictions; there is one prediction and a range around it. Categorical hues
 * are reserved for things that are actually different entities, and uncertainty
 * about one entity is not one of them.
 *
 * The median is solid, the projection's start is marked, and the band fades —
 * so the chart reads as "this, roughly" rather than "this, precisely".
 */
export function ForecastBand<Row extends Record<string, unknown>>({
  data,
  xKey,
  slot = 1,
  height = 260,
  formatter,
  zeroLine = false,
  domain,
}: {
  data: Row[];
  xKey: string;
  slot?: SeriesSlot;
  height?: number;
  formatter?: (value: number) => string;
  /** Draw a baseline at zero — worth it when a shortfall is possible. */
  zeroLine?: boolean;
  /** Explicit y bounds. See the note on the axis below. */
  domain?: [number, number];
}) {
  return (
    <ResponsiveContainer width="100%" height={height}>
      <ComposedChart data={data} margin={{ top: 4, right: 8, bottom: 0, left: -12 }}>
        <CartesianGrid stroke={GRID} vertical={false} />
        <XAxis dataKey={xKey} tickLine={false} axisLine={false} tick={AXIS} minTickGap={40} />
        <YAxis
          // Bounds computed by the caller from p10/p90, not left to Recharts.
          //
          // A ₹5,00,000 balance with a ₹40,000 band drawn from a zero origin
          // renders the band as a hairline, and the chart then reads as a
          // confident single line -- the opposite of what it means. `"auto"`
          // does not help: the band is drawn as stacked areas, and a stack's
          // domain always includes zero. So the caller measures the series and
          // passes real bounds.
          //
          // A non-zero origin is a considered trade. It exaggerates the slope,
          // which on a bar chart would be indefensible; on a balance projection
          // whose entire point is the width of the uncertainty band, hiding the
          // band to protect the origin is the worse lie. A zero reference line
          // is drawn whenever a shortfall is possible, which is the case where
          // the origin actually carries meaning.
          domain={domain ?? ["auto", "auto"]}
          allowDataOverflow={Boolean(domain)}
          tickFormatter={(v) => (formatter ? formatter(v) : compact(v))}
          tickLine={false}
          axisLine={false}
          tick={AXIS}
          width={56}
        />
        <Tooltip
          content={<ChartTooltip formatter={formatter ?? ((v) => formatMoney(String(v)))} />}
        />
        {zeroLine && (
          <ReferenceLine y={0} stroke="var(--status-critical)" strokeDasharray="3 3" />
        )}

        {/* Stacked pair: the lower bound is transparent, the visible fill is the
            span between p10 and p90. Recharts has no native band, and this is
            the standard way to get one without a second visible series. */}
        <Area
          type="monotone"
          dataKey="bandBase"
          stackId="band"
          stroke="none"
          fill="none"
          isAnimationActive={false}
        />
        <Area
          type="monotone"
          dataKey="bandSpan"
          stackId="band"
          stroke="none"
          fill={SERIES_VAR[slot]}
          fillOpacity={0.12}
          isAnimationActive={false}
        />

        <Line
          type="monotone"
          dataKey="p50"
          stroke={SERIES_VAR[slot]}
          strokeWidth={2}
          dot={false}
          activeDot={{ r: 4, strokeWidth: 2, stroke: "var(--surface)" }}
          isAnimationActive={false}
        />
      </ComposedChart>
    </ResponsiveContainer>
  );
}
