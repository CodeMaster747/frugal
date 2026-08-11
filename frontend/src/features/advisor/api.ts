import type { Explanation } from "@/components/explanation-panel";
import { apiFetch } from "@/lib/api/client";

/** Money and scores are strings on the wire (ADR-003). */
export interface Offer {
  external_id: string;
  name: string;
  category: string;
  price: string;
  currency: string;
  brand: string | null;
  seller: string;
}

export type Verdict = "buy_now" | "buy_on_emi" | "wait" | "not_recommended";

export interface Snapshot {
  liquid_savings: string;
  emergency_fund_months: string;
  health_score: string | null;
  savings_rate: string | null;
}

export interface EmiOption {
  tenure_months: number;
  monthly: string;
  total_payable: string;
  total_interest: string;
  annual_rate: string;
  new_debt_ratio: string;
  interest_share: string;
  /** False when this plan would push debt service past the lending ceiling. */
  is_serviceable: boolean;
}

export interface Alternative {
  external_id: string;
  name: string;
  price: string;
  affordability_score: string;
  verdict_if_chosen: Verdict;
}

export interface Constraint {
  code: string;
  caps_at: Verdict;
  message: string;
}

export interface Advice {
  id: string | null;
  product_query: string;
  price: string;
  currency: string;
  verdict: Verdict;
  affordability_score: string;
  confidence: string;
  rubric_version: string;
  affordable_from: string | null;
  /** What the score alone said, before any constraint capped it. */
  score_verdict: Verdict;
  constraints: Constraint[];
  simulation: {
    before: Snapshot;
    after: Snapshot;
    goal_impact: { goal: string; delay_days: number; priority: number }[];
    forecast_trough_after: string | null;
    health_score_after_is_estimated: boolean;
  };
  emi_options: EmiOption[];
  alternatives: Alternative[];
  explanation: Explanation;
}

export interface EvaluationSummary {
  id: string;
  product_query: string;
  price: string;
  currency: string;
  verdict: Verdict;
  affordability_score: string;
  affordable_from: string | null;
  created_at: string;
}

export const searchProducts = (q: string) =>
  apiFetch<Offer[]>(`/api/v1/advisor/products/search?q=${encodeURIComponent(q)}`);

export const evaluatePurchase = (input: {
  product_query: string;
  price: string;
  external_id?: string | null;
  consider_emi?: boolean;
}) =>
  apiFetch<Advice>("/api/v1/advisor/evaluate", {
    method: "POST",
    body: JSON.stringify({ consider_emi: true, ...input }),
  });

export const listEvaluations = () =>
  apiFetch<EvaluationSummary[]>("/api/v1/advisor/evaluations");

export interface AdvisorRubric {
  version: string;
  total_weight: string;
  score_thresholds: Record<string, string>;
  factors: {
    key: string;
    name: string;
    weight: string;
    higher_is_better: boolean;
    bands: { at_least?: string; at_most?: string; points: string; label: string }[];
  }[];
  hard_constraints: { code: string; rule: string; caps_at: string }[];
}

export const getAdvisorRubric = () => apiFetch<AdvisorRubric>("/api/v1/advisor/rubric");
