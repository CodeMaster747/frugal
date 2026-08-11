import type { Explanation } from "@/components/explanation-panel";
import { apiFetch } from "@/lib/api/client";

/** Money is a string on the wire (ADR-003). */
export interface TemplateInput {
  name: string;
  label: string;
  default: string;
}

export interface ScenarioTemplate {
  key: string;
  name: string;
  description: string;
  inputs: TemplateInput[];
}

export type Outlook = "comfortable" | "tight" | "unsustainable";

export interface Snapshot {
  liquid_reserves: string;
  monthly_surplus: string;
  emergency_fund_months: string;
}

export interface SeriesPoint {
  month: number;
  on: string;
  reserves: string;
  monthly_surplus: string;
}

export interface ScenarioResult {
  name: string;
  outlook: Outlook;
  before: Snapshot;
  after: Snapshot;
  months_until_shortfall: number | null;
  trough_months_of_cover: string;
  series: SeriesPoint[];
  explanation: Explanation;
}

export interface ScenarioInput {
  name?: string;
  template?: string;
  values?: Record<string, string>;
  horizon_months?: number;
}

export const getTemplates = () =>
  apiFetch<{ templates: ScenarioTemplate[] }>("/api/v1/simulator/templates");

export const runScenario = (input: ScenarioInput) =>
  apiFetch<ScenarioResult>("/api/v1/simulator/run", {
    method: "POST",
    body: JSON.stringify(input),
  });

export const compareScenarios = (scenarios: ScenarioInput[]) =>
  apiFetch<{ results: ScenarioResult[]; safest: string | null }>("/api/v1/simulator/compare", {
    method: "POST",
    body: JSON.stringify({ scenarios }),
  });

// --- notifications ----------------------------------------------------------

export interface Notification {
  id: string;
  category: string;
  urgency: string;
  subject: string;
  body: string;
  link: string | null;
  status: string;
  delivered_at: string | null;
  read_at: string | null;
  created_at: string;
}

export interface Preferences {
  budget_enabled: boolean;
  bill_enabled: boolean;
  renewal_enabled: boolean;
  goal_milestone_enabled: boolean;
  forecast_shortfall_enabled: boolean;
  price_drop_enabled: boolean;
  digest_frequency: "immediate" | "daily" | "weekly" | "off";
  digest_hour: number;
  quiet_from: string | null;
  quiet_until: string | null;
}

export const getNotifications = () =>
  apiFetch<{ data: Notification[]; unread_count: number }>("/api/v1/notifications");

export const generateNotifications = () =>
  apiFetch<{ detected: number; created: number; suppressed_by_preference: number }>(
    "/api/v1/notifications/generate",
    { method: "POST" },
  );

export const markNotificationRead = (id: string) =>
  apiFetch<void>(`/api/v1/notifications/${id}/read`, { method: "POST" });

export const markAllNotificationsRead = () =>
  apiFetch<{ marked: number }>("/api/v1/notifications/read-all", { method: "POST" });

export const getPreferences = () => apiFetch<Preferences>("/api/v1/notifications/preferences");

export const updatePreferences = (changes: Partial<Preferences>) =>
  apiFetch<Preferences>("/api/v1/notifications/preferences", {
    method: "PATCH",
    body: JSON.stringify(changes),
  });
