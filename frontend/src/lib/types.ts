export type Source = "wb" | "ozon" | "ya_market" | "runet" | "google";

export const SOURCE_LABEL: Record<Source, string> = {
  wb: "Wildberries",
  ozon: "Ozon",
  ya_market: "Я.Маркет",
  runet: "Рунет",
  google: "Google",
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

export interface ChatResponse {
  reply: string;
  session_id: string;
  rounds: number;
  history_len: number;
  tool_calls: { name: string; result_keys: unknown }[];
}
