import { apiFetch } from "@/lib/api/client";

export interface Totals {
  income: string;
  expense: string;
  net: string;
  savings_rate: string | null;
}

export interface CategorySlice {
  category_id: string | null;
  name: string;
  slug: string;
  amount: string;
  share_pct: string;
  previous_amount: string;
  change_pct: string | null;
}

export interface SeriesPoint {
  period: string;
  income: string;
  expense: string;
  net: string;
}

export interface TrendPoint {
  period: string;
  value: string | null;
}

export interface DashboardData {
  period: { start: string; end: string };
  net_worth: string;
  liquid: string;
  totals: Totals;
  previous_totals: Totals;
  top_categories: CategorySlice[];
  cashflow: SeriesPoint[];
  net_worth_trend: TrendPoint[];
  account_count: number;
  transaction_count: number;
  /** Lets a client tell whether this reflects its own most recent write. */
  data_version: number;
}

export const getDashboard = (month?: string) =>
  apiFetch<DashboardData>(`/api/v1/analytics/dashboard${month ? `?month=${month}` : ""}`);

export const getCategories = (month?: string) =>
  apiFetch<CategorySlice[]>(`/api/v1/analytics/categories${month ? `?month=${month}` : ""}`);

export const getSavingsRate = (months = 12) =>
  apiFetch<TrendPoint[]>(`/api/v1/analytics/savings-rate?months=${months}`);
