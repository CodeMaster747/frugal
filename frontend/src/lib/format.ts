/**
 * Display formatting.
 *
 * Amounts arrive as strings and are only converted to a number *here*, at the
 * moment of rendering. Nothing upstream does arithmetic on them, so the
 * IEEE-754 error that ADR-003 exists to prevent never touches stored data.
 */

const CURRENCY_LOCALE: Record<string, string> = { INR: "en-IN", USD: "en-US", EUR: "de-DE" };

export function formatMoney(
  amount: string,
  currency = "INR",
  options: { compact?: boolean } = {},
) {
  const value = Number(amount);
  if (!Number.isFinite(value)) return amount;

  return new Intl.NumberFormat(CURRENCY_LOCALE[currency] ?? "en-IN", {
    style: "currency",
    currency,
    notation: options.compact ? "compact" : "standard",
    maximumFractionDigits: options.compact ? 1 : 2,
    minimumFractionDigits: options.compact ? 0 : 2,
  }).format(value);
}

/**
 * Format a calendar date for display.
 *
 * Accepts either a plain `YYYY-MM-DD` or a full ISO timestamp. The earlier
 * version appended `T00:00:00` unconditionally, so passing a timestamp built
 * `...T01:23:45ZT00:00:00`, produced an Invalid Date, and threw — which React
 * turns into a blank page with "This page couldn't load". A date helper that
 * can take down a screen is worth making total.
 *
 * A timestamp is truncated to its date part rather than converted: these are
 * calendar dates in the user's terms ("tracking since 6 Aug"), and shifting one
 * across midnight by a timezone would be wrong in a way nobody would report.
 */
export function formatDate(iso: string | null | undefined, style: "short" | "long" = "short") {
  if (!iso) return "—";

  const datePart = iso.slice(0, 10);
  const date = new Date(`${datePart}T00:00:00`);
  if (Number.isNaN(date.getTime())) return "—";

  return new Intl.DateTimeFormat("en-IN", {
    day: "numeric",
    month: style === "short" ? "short" : "long",
    year: style === "short" ? undefined : "numeric",
  }).format(date);
}

export function todayISO() {
  // Local calendar date, not UTC: the date a person means when they say
  // "today" is the one on their own wall clock.
  const now = new Date();
  const offset = now.getTimezoneOffset() * 60_000;
  return new Date(now.getTime() - offset).toISOString().slice(0, 10);
}
