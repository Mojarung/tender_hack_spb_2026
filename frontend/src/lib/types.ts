export type Source = "wb" | "ozon" | "ya_market" | "runet";

export const SOURCE_LABEL: Record<Source, string> = {
  wb: "Wildberries",
  ozon: "Ozon",
  ya_market: "Я.Маркет",
  runet: "Рунет",
};

export interface ProductReview {
  author: string | null;
  score: number | null;
  text: string;
  published_at?: string | null;
  photos?: string[];
}

export interface ProductOffer {
  source: Source;
  name: string;
  price: string;
  currency: string;
  url: string;
  image: string | null;
  // Full product gallery (modal carousel). `image` is the cover thumbnail.
  // Other sources may return [] until they implement enrichment.
  images: string[];
  characteristics: Record<string, string>;
  seller: string | null;
  rating: number | null;
  reviews: ProductReview[];
  reviews_count: number | null;
  fetched_at: string;
  cached: boolean;
  // Similarity to the user's query (0-100), computed in the
  // orchestrator. null when the offer came via a path that skipped
  // scoring (e.g. some tests). Frontend renders it as a small
  // "X% совпадение" pill and can sort by it.
  relevance?: number | null;
}

export interface SourceGroup {
  source: Source;
  count: number;
  min_price: string | null;
  avg_price?: string | null;
  median_price?: string | null;
  currency: string;
  offers: ProductOffer[];
  error?: string | null;
}

export interface NormalizedQuery {
  raw: string;
  normalized: string;
  expansions: string[];
  alternates?: string[];
}
export interface RankedOffer { offer: ProductOffer; score: number; rank: number; }

export interface ClarificationOption {
  label: string;
  text: string;
  query: string;
}

export interface QueryClarification {
  is_ambiguous: boolean;
  reason: string | null;
  options: ClarificationOption[];
}

export interface SearchResponse {
  query: NormalizedQuery;
  groups: SourceGroup[];
  top_deals: RankedOffer[];
  took_ms: number;
  partial: boolean;
  clarification?: QueryClarification | null;
}

export interface Favorite {
  id: number;
  source: Source;
  item_id: string;
  name: string;
  price: string;
  currency: string;
  url: string;
  image: string | null;
  added_at: string;
}

export interface User {
  id: string;
  email: string;
  is_active: boolean;
  is_superuser: boolean;
  is_verified: boolean;
  display_name: string | null;
}

export interface PriceWatch {
  id: number;
  query: string;
  interval_min: number;
  threshold_pct: number;
  region_id: number;
  active: boolean;
  last_best_price: string | null;
  last_best_source: string | null;
  last_best_url: string | null;
  last_best_name: string | null;
  last_check_at: string | null;
  last_error: string | null;
  created_at: string;
}

export interface PriceAlertTopOffer {
  source: string;
  price: string;
  name: string;
  url: string;
}

export interface PriceAlert {
  id: number;
  watch_id: number;
  query: string;
  prev_price: string;
  new_price: string;
  diff_pct: number;
  offer_source: string | null;
  offer_url: string | null;
  offer_name: string | null;
  top_offers: PriceAlertTopOffer[] | null;
  created_at: string;
  read_at: string | null;
}

export interface ChatResponse {
  reply: string;
  session_id: string;
  rounds: number;
  history_len: number;
  tool_calls: { name: string; result_keys: unknown }[];
}
