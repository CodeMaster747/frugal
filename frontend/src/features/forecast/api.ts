import type { Explanation } from "@/components/explanation-panel";
import { ApiError, apiFetch } from "@/lib/api/client";

/** Money and confidences are strings on the wire (ADR-003). */
export interface SeriesPoint {
  date: string;
  p10: string;
  p50: string;
  p90: string;
}

export interface Forecast {
  horizon_days: number;
  method: "recurring_projection" | "ewma_seasonal" | "prophet";
  observation_days: number;
  confidence: string;
  projected_balance_end: { amount: string; currency: string };
  trough: { amount: string; on: string } | null;
  shortfall_dates: string[];
  series: SeriesPoint[];
  explanation: Explanation;
  /** A better tier is being computed in the worker; this result is provisional. */
  refining: boolean;
}

export interface RecurringPattern {
  merchant: string;
  kind: "income" | "expense";
  cadence: string;
  amount: string;
  monthly_equivalent: string;
  occurrences: number;
  next_due_on: string;
  confidence: string;
  amount_variance: string;
}

/**
 * `null` when the API declines for want of history.
 *
 * A 503 here is a real answer, not a failure — the caller renders the reason
 * rather than an error state, so it is returned as data rather than thrown.
 */
export interface Declined {
  declined: true;
  caveats: string[];
  observation_days: number;
}

export async function getForecast(horizonDays = 90): Promise<Forecast | Declined> {
  try {
    return await apiFetch<Forecast>(`/api/v1/forecast?horizon_days=${horizonDays}`);
  } catch (error) {
    const declined = asInsufficientData(error);
    if (declined) return declined;
    throw error;
  }
}

export const getRecurring = () => apiFetch<RecurringPattern[]>("/api/v1/forecast/recurring");

export interface ScenarioEvent {
  on: string;
  amount: string;
  label?: string;
}

export async function runScenario(
  horizonDays: number,
  events: ScenarioEvent[],
): Promise<Forecast | Declined> {
  try {
    return await apiFetch<Forecast>("/api/v1/forecast/scenario", {
      method: "POST",
      body: JSON.stringify({ horizon_days: horizonDays, events }),
    });
  } catch (error) {
    const declined = asInsufficientData(error);
    if (declined) return declined;
    throw error;
  }
}

export function isDeclined(result: Forecast | Declined): result is Declined {
  return "declined" in result;
}

/**
 * Recognise the 503 the forecast endpoints return when history is too thin.
 *
 * `ApiError.isInsufficientData` exists for exactly this: an engine declining is
 * a normal outcome, and the body carries the reasons.
 */
function asInsufficientData(error: unknown): Declined | null {
  if (!(error instanceof ApiError) || !error.isInsufficientData) return null;

  return {
    declined: true,
    caveats: error.caveats,
    observation_days: Number(error.details[0]?.issue ?? 0),
  };
}
