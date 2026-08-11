import { apiFetch } from "@/lib/api/client";

import type { Explanation } from "@/components/explanation-panel";

/** Scores and confidences are strings on the wire, like money (ADR-003). */
export interface HealthScore {
  score: string | null;
  risk_level: "low" | "moderate" | "elevated" | "high" | null;
  confidence: string;
  rubric_version: string;
  explanation: Explanation;
}

export interface HealthSnapshot {
  snapshot_on: string;
  overall_score: string;
  risk_level: string;
  confidence: string;
  rubric_version: string;
  savings_rate_score: string;
  emergency_fund_score: string;
  debt_to_income_score: string;
  budget_discipline_score: string;
  cashflow_stability_score: string;
  growth_score: string;
}

export interface RubricBand {
  at_least?: string;
  at_most?: string;
  points: string;
  label: string;
}

export interface RubricMetric {
  key: string;
  name: string;
  weight: string;
  unit: string;
  higher_is_better: boolean;
  bands: RubricBand[];
}

export interface Rubric {
  version: string;
  total_weight: string;
  metrics: RubricMetric[];
  risk_levels: { at_least: string; level: string }[];
}

export const getHealthScore = () => apiFetch<HealthScore>("/api/v1/health-score");

export const getHealthHistory = (months = 12) =>
  apiFetch<HealthSnapshot[]>(`/api/v1/health-score/history?months=${months}`);

export const getRubric = () => apiFetch<Rubric>("/api/v1/health-score/rubric");

// --- insights ---------------------------------------------------------------

export type Severity = "info" | "warning" | "critical";

export interface Insight {
  id: string;
  insight_type: string;
  severity: Severity;
  title: string;
  body: string;
  impact_amount: string | null;
  materiality: string;
  confidence: string;
  period_start: string;
  period_end: string;
  explanation: Explanation;
  read_at: string | null;
  dismissed_at: string | null;
  subject_id: string | null;
  created_at: string;
}

export interface InsightFeed {
  data: Insight[];
  unread_count: number;
}

export const getInsights = (params: { severity?: Severity; unread?: boolean } = {}) => {
  const query = new URLSearchParams();
  if (params.severity) query.set("severity", params.severity);
  if (params.unread) query.set("unread", "true");
  const qs = query.toString();
  return apiFetch<InsightFeed>(`/api/v1/insights${qs ? `?${qs}` : ""}`);
};

export const refreshInsights = () =>
  apiFetch<{ detected: number; suppressed: number; created: number }>(
    "/api/v1/insights/refresh",
    { method: "POST" },
  );

export const markInsightRead = (id: string) =>
  apiFetch<void>(`/api/v1/insights/${id}/read`, { method: "POST" });

export const dismissInsight = (id: string) =>
  apiFetch<void>(`/api/v1/insights/${id}/dismiss`, { method: "POST" });
