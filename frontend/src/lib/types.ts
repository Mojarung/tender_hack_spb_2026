export type Source = "wb" | "ozon" | "ya_market" | "runet";

export interface ProductOffer {
  source: Source;
  name: string;
  price: string;          // decimal serialised as string
  currency: string;
  url: string;
  image: string | null;
  characteristics: Record<string, string>;
  seller: string | null;
  rating: number | null;
  fetched_at: string;
  cached: boolean;
}

export interface SourceGroup {
  source: Source;
  count: number;
  min_price: string | null;
  median_price?: string | null;
  currency: string;
  offers: ProductOffer[];
  error?: string | null;
}

export interface NormalizedQuery {
  raw: string;
  normalized: string;
  expansions: string[];
}

export interface RankedOffer {
  offer: ProductOffer;
  score: number;
  rank: number;
}

export interface SearchResponse {
  query: NormalizedQuery;
  groups: SourceGroup[];
  top_deals: RankedOffer[];
  took_ms: number;
  partial: boolean;
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

export interface SentimentBreakdown {
  total: number;
  positive: number;
  neutral: number;
  negative: number;
  positive_pct: number;
  neutral_pct: number;
  negative_pct: number;
}

export interface SentimentResponse {
  source: Source;
  item_id: number;
  available: boolean;
  feedbacks_seen: number;
  breakdown: SentimentBreakdown;
  quotes: {
    positive: SentimentQuote[];
    neutral: SentimentQuote[];
    negative: SentimentQuote[];
  };
}

export interface SentimentQuote {
  text: string;
  rating: number;
  votes_plus: number;
  created: string;
  score: number;
}

export interface PricePoint { ts: string; price: string; }

export interface PriceHistoryResponse {
  source: Source;
  item_id: string;
  count: number;
  points: PricePoint[];
}

export interface ChatResponse {
  reply: string;
  session_id: string;
  rounds: number;
  history_len: number;
  tool_calls: { name: string; result_keys: unknown }[];
}
