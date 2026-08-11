import { apiFetch } from "@/lib/api/client";

/** Money and scores are strings on the wire (ADR-003). */
export interface WishlistItem {
  id: string;
  product_id: string;
  name: string;
  category: string;
  price_when_added: string;
  current_price: string | null;
  change_since_added: string | null;
  lowest_recorded: string | null;
  lowest_recorded_on: string | null;
  is_at_lowest: boolean;
  target_price: string | null;
  notes: string | null;
  purchased_on: string | null;
  created_at: string;
}

export interface HistoryPoint {
  date: string;
  price: string;
  sellers: number;
}

export interface ReliabilitySignal {
  key: string;
  name: string;
  value: string;
  weight: string;
  contribution: string;
  detail: string;
}

export interface Reliability {
  score: string;
  band: string;
  confidence: "high" | "moderate" | "low";
  rubric_version: string;
  signals: ReliabilitySignal[];
  caveats: string[];
}

export interface Offer {
  seller_name: string;
  price: string;
  in_stock: boolean;
  return_window_days: number | null;
  warranty_months: number | null;
  fulfillment_type: string | null;
  observed_at: string;
  reliability: Reliability;
}

export interface ProductDetail {
  product_id: string;
  name: string;
  category: string;
  current_best: string | null;
  lowest_recorded: string | null;
  lowest_recorded_on: string | null;
  market_median: string | null;
  history: HistoryPoint[];
  offers: Offer[];
}

export interface PriceAlert {
  id: string;
  product_id: string;
  previous_price: string;
  new_price: string;
  drop_fraction: string;
  seller_name: string;
  is_lowest_recorded: boolean;
  read_at: string | null;
  created_at: string;
}

export const getWishlist = () => apiFetch<WishlistItem[]>("/api/v1/market/wishlist");

export const addToWishlist = (input: {
  external_id: string;
  target_price?: string | null;
  notes?: string | null;
}) =>
  apiFetch<WishlistItem>("/api/v1/market/wishlist", {
    method: "POST",
    body: JSON.stringify(input),
  });

export const removeFromWishlist = (id: string) =>
  apiFetch<void>(`/api/v1/market/wishlist/${id}`, { method: "DELETE" });

export const getProductDetail = (productId: string, days = 90) =>
  apiFetch<ProductDetail>(`/api/v1/market/products/${productId}?days=${days}`);

export const getAlerts = () => apiFetch<PriceAlert[]>("/api/v1/market/alerts");

export const checkAlerts = () =>
  apiFetch<PriceAlert[]>("/api/v1/market/alerts/check", { method: "POST" });

export const markAlertRead = (id: string) =>
  apiFetch<void>(`/api/v1/market/alerts/${id}/read`, { method: "POST" });

export interface ReliabilityRubric {
  version: string;
  total_weight: string;
  what_this_is: string;
  what_this_is_not: string;
  missing_signals: string;
  signals: {
    key: string;
    name: string;
    weight: string;
    higher_is_better: boolean;
    bands: { at_least?: string; at_most?: string; points: string; label: string }[];
  }[];
  bands: { at_least: string; label: string }[];
}

export const getReliabilityRubric = () =>
  apiFetch<ReliabilityRubric>("/api/v1/market/reliability/rubric");
