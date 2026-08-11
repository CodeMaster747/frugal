"use client";

import { Table2 } from "lucide-react";
import { useId, useState } from "react";

import { cn } from "@/lib/utils";

/**
 * Every chart in Frugal renders through this.
 *
 * It is not a styling wrapper. The palette validation produced a contrast
 * warning on the aqua and yellow slots in light mode (2.74:1 and 2.11:1,
 * against a 3:1 floor), and the relief rule says any chart using them must
 * ship visible direct labels or a table view. Rather than trusting each chart
 * author to remember, the obligation lives here: a chart declaring those slots
 * without either relief is a type error.
 *
 * The same wrapper also carries the two things every chart owes a
 * non-sighted user -- a text summary and a data table -- so neither can be
 * forgotten one chart at a time.
 */

/** Categorical slots, in fixed assignment order. Never cycled. */
export type SeriesSlot = 1 | 2 | 3 | 4;

export const SERIES_VAR: Record<SeriesSlot, string> = {
  1: "var(--series-1)",
  2: "var(--series-2)",
  3: "var(--series-3)",
  4: "var(--series-4)",
};

/** Slots below 3:1 contrast on the light surface. */
const RELIEF_REQUIRED: ReadonlySet<SeriesSlot> = new Set<SeriesSlot>([3, 4]);

export interface TableColumn<Row> {
  header: string;
  numeric?: boolean;
  cell: (row: Row) => React.ReactNode;
}

type ReliefProps =
  | { directLabels: true; tableView?: never }
  | { directLabels?: false; tableView: React.ReactNode };

type BaseProps<Row> = {
  title: string;
  /** Read by screen readers in place of the visual. Say what the chart shows. */
  summary: string;
  slots: SeriesSlot[];
  children: React.ReactNode;
  caveat?: string;
  action?: React.ReactNode;
  /** Rows behind the chart. Rendered as the built-in accessible table. */
  rows?: Row[];
  columns?: TableColumn<Row>[];
  className?: string;
};

export type ChartContainerProps<Row> = BaseProps<Row> &
  (ReliefProps | { directLabels?: boolean; tableView?: React.ReactNode });

export function ChartContainer<Row>({
  title,
  summary,
  slots,
  children,
  caveat,
  action,
  rows,
  columns,
  directLabels,
  tableView,
  className,
}: ChartContainerProps<Row>) {
  const id = useId();
  const [showTable, setShowTable] = useState(false);

  const hasTable = Boolean(tableView) || Boolean(rows && columns);
  const needsRelief = slots.some((s) => RELIEF_REQUIRED.has(s));

  if (process.env.NODE_ENV !== "production" && needsRelief && !directLabels && !hasTable) {
    // Loud in development rather than a silently inaccessible chart in
    // production. See the palette validation in docs/05-ui-wireframes.md.
    console.error(
      `Chart "${title}" uses a low-contrast slot in light mode without direct labels or a table view.`,
    );
  }

  return (
    // A chart is a discrete object, so this is one of the few places that earns
    // a bordered surface. Padding matches `Section variant="bordered"` exactly --
    // a chart and a content block side by side must line up.
    <figure className={cn("rounded-card border border-hairline bg-surface p-6", className)}>
      <figcaption className="mb-4 flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0 space-y-0.5">
          <h3 className="type-section">{title}</h3>
          {caveat && <p className="type-meta text-ink-muted">{caveat}</p>}
        </div>
        <div className="flex shrink-0 items-center gap-2">
          {action}
          {hasTable && (
            <button
              type="button"
              onClick={() => setShowTable((v) => !v)}
              aria-expanded={showTable}
              aria-controls={`${id}-table`}
              className="inline-flex h-8 shrink-0 items-center gap-1.5 rounded-control border border-gridline px-2 type-meta text-ink-secondary transition-colors hover:border-baseline hover:bg-surface-raised hover:text-ink"
            >
              <Table2 className="size-3.5 shrink-0" aria-hidden />
              {showTable ? "Hide data" : "View data"}
            </button>
          )}
        </div>
      </figcaption>

      {/* The visual is hidden from assistive tech; the summary below carries
          the same information in words. */}
      <div aria-hidden className="overflow-x-auto">
        {children}
      </div>

      <p className="sr-only">{summary}</p>

      {hasTable && (
        <div id={`${id}-table`} hidden={!showTable} className="mt-4 overflow-x-auto">
          {tableView ?? <DataTable rows={rows!} columns={columns!} caption={summary} />}
        </div>
      )}
    </figure>
  );
}

function DataTable<Row>({
  rows,
  columns,
  caption,
}: {
  rows: Row[];
  columns: TableColumn<Row>[];
  caption: string;
}) {
  return (
    <table className="w-full text-left type-body">
      <caption className="sr-only">{caption}</caption>
      <thead className="type-eyebrow text-ink-muted">
        <tr>
          {columns.map((c) => (
            <th
              key={c.header}
              scope="col"
              className={cn("py-2 pr-4 font-medium", c.numeric && "text-right")}
              data-numeric={c.numeric ? "" : undefined}
            >
              {c.header}
            </th>
          ))}
        </tr>
      </thead>
      <tbody className="divide-y divide-hairline">
        {rows.map((row, i) => (
          <tr key={i}>
            {columns.map((c) => (
              <td
                key={c.header}
                className={cn("py-2 pr-4", c.numeric && "tabular text-right")}
                data-numeric={c.numeric ? "" : undefined}
              >
                {c.cell(row)}
              </td>
            ))}
          </tr>
        ))}
      </tbody>
    </table>
  );
}

/**
 * Legend for two or more series.
 *
 * Always present when there is more than one series, so identity is never
 * carried by colour alone. A single series needs none -- the title names it.
 */
export function Legend({ items }: { items: { label: string; slot: SeriesSlot }[] }) {
  if (items.length < 2) return null;

  return (
    <ul className="mt-4 flex flex-wrap gap-4">
      {items.map(({ label, slot }) => (
        <li key={label} className="flex items-center gap-1.5 type-meta text-ink-secondary">
          <span
            className="size-2.5 shrink-0 rounded-swatch"
            style={{ backgroundColor: SERIES_VAR[slot] }}
            aria-hidden
          />
          {/* Text stays in ink tokens; the swatch beside it carries identity. */}
          {label}
        </li>
      ))}
    </ul>
  );
}
