import { API_BASE_URL, apiFetch } from "@/lib/api/client";

/**
 * Amounts are strings on the wire (ADR-003) and stay strings here.
 *
 * Parsing them into `number` would reintroduce the IEEE-754 error the
 * NUMERIC(18,2) column exists to prevent. Formatting for display is the only
 * thing that touches them.
 */
export type Money = string;

export interface Account {
  id: string;
  name: string;
  type: "bank" | "cash" | "credit_card" | "wallet" | "loan" | "investment";
  currency: string;
  opening_balance: Money;
  current_balance: Money;
  credit_limit: Money | null;
  is_liquid: boolean;
  institution: string | null;
  archived_at: string | null;
  created_at: string;
}

export interface Category {
  id: string;
  name: string;
  slug: string;
  kind: "income" | "expense" | "transfer";
  parent_id: string | null;
  icon: string | null;
  color: string | null;
  is_system: boolean;
  sort_order: number;
}

export interface Transaction {
  id: string;
  account_id: string;
  kind: "income" | "expense" | "transfer";
  amount: Money;
  currency: string;
  occurred_on: string;
  merchant_raw: string | null;
  merchant_normalized: string | null;
  description: string | null;
  category: { id: string; name: string; slug: string; icon: string | null } | null;
  category_confidence: string | null;
  /** Which model or rule set produced the category, when a machine did. */
  categorizer_version: string | null;
  transfer_pair_id: string | null;
  source: string;
  is_reviewed: boolean;
  created_at: string;
}

export interface Page<T> {
  data: T[];
  pagination: { next_cursor: string | null; has_more: boolean; limit: number };
}

export interface Budget {
  id: string;
  category: { id: string; name: string; slug: string } | null;
  period_start: string;
  amount_limit: Money;
  currency: string;
  spent: Money;
  remaining: Money;
  pace: "on_track" | "ahead" | "over";
}

export interface Goal {
  id: string;
  name: string;
  target_amount: Money;
  current_amount: Money;
  currency: string;
  target_date: string | null;
  priority: number;
  status: string;
  progress_pct: string;
}

export interface BulkResult {
  index: number;
  status: "created" | "duplicate" | "error";
  id: string | null;
  error: string | null;
}

export interface BulkResponse {
  created: number;
  duplicates: number;
  errors: number;
  results: BulkResult[];
}

export interface ImportAnalysis {
  import_id: string;
  columns: string[];
  detected_mapping: Record<string, string | null> | null;
  confidence: string;
  row_count: number;
  preview: {
    index: number;
    occurred_on: string | null;
    amount: Money | null;
    kind: string | null;
    merchant: string | null;
    is_duplicate: boolean;
    error: string | null;
  }[];
  warnings: string[];
  duplicate_estimate: number;
}

// --- accounts --------------------------------------------------------------

export const listAccounts = () => apiFetch<Account[]>("/api/v1/accounts");

export const createAccount = (input: {
  name: string;
  type: Account["type"];
  opening_balance?: string;
  credit_limit?: string;
  is_liquid?: boolean;
}) => apiFetch<Account>("/api/v1/accounts", { method: "POST", body: JSON.stringify(input) });

// --- categories ------------------------------------------------------------

export const listCategories = () => apiFetch<Category[]>("/api/v1/categories");

// --- transactions ----------------------------------------------------------

export interface TransactionQuery {
  cursor?: string | null;
  limit?: number;
  account_id?: string;
  category_id?: string;
  kind?: string;
  q?: string;
  uncategorized_only?: boolean;
  /** The review queue: machine-categorised, not yet confirmed by a person. */
  needs_review?: boolean;
}

export function listTransactions(query: TransactionQuery = {}) {
  const params = new URLSearchParams();
  for (const [key, value] of Object.entries(query)) {
    if (value !== undefined && value !== null && value !== "" && value !== false) {
      params.set(key, String(value));
    }
  }
  const qs = params.toString();
  return apiFetch<Page<Transaction>>(`/api/v1/transactions${qs ? `?${qs}` : ""}`);
}

export interface TransactionInput {
  account_id: string;
  kind: "income" | "expense" | "transfer";
  amount: string;
  occurred_on: string;
  category_id?: string | null;
  merchant_raw?: string | null;
  to_account_id?: string | null;
  allow_duplicate?: boolean;
}

export const createTransaction = (input: TransactionInput, idempotencyKey?: string) =>
  apiFetch<Transaction>("/api/v1/transactions", {
    method: "POST",
    body: JSON.stringify(input),
    headers: idempotencyKey ? { "Idempotency-Key": idempotencyKey } : undefined,
  });

export const updateTransaction = (
  id: string,
  input: Partial<{
    amount: string;
    category_id: string | null;
    merchant_raw: string;
    is_reviewed: boolean;
  }>,
) =>
  apiFetch<Transaction>(`/api/v1/transactions/${id}`, {
    method: "PATCH",
    body: JSON.stringify(input),
  });

/**
 * Accept a suggestion as-is.
 *
 * Distinct from a recategorisation: the category does not change, but the row
 * leaves the review queue and the label becomes training data. Confirming is
 * the common outcome, so it has to be one click.
 */
export const confirmCategory = (id: string) => updateTransaction(id, { is_reviewed: true });

export const deleteTransaction = (id: string) =>
  apiFetch<void>(`/api/v1/transactions/${id}`, { method: "DELETE" });

// --- import ----------------------------------------------------------------

/**
 * Multipart upload, so this bypasses `apiFetch` -- which sets a JSON
 * content-type that would break the boundary header the browser must generate.
 */
async function upload<T>(path: string, file: File, token: string | null): Promise<T> {
  const form = new FormData();
  form.append("file", file);

  const response = await fetch(`${API_BASE_URL}${path}`, {
    method: "POST",
    body: form,
    credentials: "include",
    headers: token ? { Authorization: `Bearer ${token}` } : undefined,
  });

  if (!response.ok) {
    const body = await response.json().catch(() => null);
    throw new Error(body?.error?.message ?? `Upload failed (${response.status})`);
  }
  return (await response.json()) as T;
}

export const analyzeCsv = (file: File, accountId: string, token: string | null) =>
  upload<ImportAnalysis>(`/api/v1/imports/csv/analyze?account_id=${accountId}`, file, token);

export const commitCsv = (
  file: File,
  accountId: string,
  mapping: {
    date: string;
    merchant?: string;
    debit?: string;
    credit?: string;
    amount?: string;
  },
  token: string | null,
) => {
  const params = new URLSearchParams({ account_id: accountId, "mapping.date": mapping.date });
  if (mapping.merchant) params.set("mapping.merchant", mapping.merchant);
  if (mapping.debit) params.set("mapping.debit", mapping.debit);
  if (mapping.credit) params.set("mapping.credit", mapping.credit);
  if (mapping.amount) params.set("mapping.amount", mapping.amount);

  return upload<BulkResponse>(`/api/v1/imports/csv/commit?${params}`, file, token);
};

export const seedDemoData = () =>
  apiFetch<{ status: string; accounts: number; transactions: number; months: number }>(
    "/api/v1/imports/demo-seed",
    { method: "POST" },
  );

// --- budgets & goals -------------------------------------------------------

export const listBudgets = () => apiFetch<Budget[]>("/api/v1/budgets");
export const listGoals = () => apiFetch<Goal[]>("/api/v1/goals");
