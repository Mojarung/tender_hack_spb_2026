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
  attributes?: ProductAttributes | null;
  delivery?: DeliveryInfo | null;
  seller: string | null;
  rating: number | null;
  reviews: ProductReview[];
  reviews_count: number | null;
  fetched_at: string;
  cached: boolean;
}

export interface ProductAttributes {
  category?: string | null;
  brand?: string | null;
  model?: string | null;
  color?: string | null;
  storage_gb?: number | null;
  ram_gb?: number | null;
  season?: string | null;
  size?: string | null;
  paper_format?: string | null;
  density_gm2?: number | null;
  sheets_count?: number | null;
  confidence: number;
  raw: Record<string, string>;
  extra: Record<string, string | number | boolean>;
}

export interface DeliveryInfo {
  city?: string | null;
  region_id?: string | null;
  region_source?: string | null;
  warehouse_id?: string | null;
  distance_marketplace?: number | null;
  eta_min_hours?: number | null;
  eta_max_hours?: number | null;
  stock?: number | null;
  delivery_text?: string | null;
  confidence: number;
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
  attributes?: ProductAttributes | null;
  alternates?: string[];
}

export interface RankedOffer {
  offer: ProductOffer;
  score: number;
  rank: number;
  deal_score?: number;
  relevance_score?: number;
  relevance_percent?: number;
  rerank_score?: number | null;
  selection_reasons?: string[];
  match_signals?: string[];
  mismatch_signals?: string[];
  unknown_signals?: string[];
}

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

export interface ImageQueryResponse {
  query: string;
  confidence: number;
  category?: string | null;
  brand?: string | null;
  model?: string | null;
  color?: string | null;
  attributes: Record<string, string | number | boolean | null>;
  alternatives: string[];
  took_ms: number;
  cached: boolean;
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
