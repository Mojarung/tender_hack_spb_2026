/** localStorage-backed search history.
 *
 *  Lightweight, no React context. Components subscribe via the
 *  `pp.history` window event we emit on every mutation.
 */

const KEY = "pp.search.history";
const LIMIT = 12;

export interface HistoryItem { q: string; ts: number; }

function read(): HistoryItem[] {
  if (typeof window === "undefined") return [];
  try {
    const raw = window.localStorage.getItem(KEY);
    if (!raw) return [];
    const arr = JSON.parse(raw);
    return Array.isArray(arr) ? arr.filter((x) => x && typeof x.q === "string") : [];
  } catch {
    return [];
  }
}

function write(items: HistoryItem[]) {
  if (typeof window === "undefined") return;
  window.localStorage.setItem(KEY, JSON.stringify(items));
  window.dispatchEvent(new Event("pp.history"));
}

export const history = {
  list(): HistoryItem[] {
    return read();
  },

  push(q: string) {
    const trimmed = q.trim();
    if (!trimmed) return;
    const items = read().filter(
      (h) => h.q.toLowerCase() !== trimmed.toLowerCase(),
    );
    items.unshift({ q: trimmed, ts: Date.now() });
    write(items.slice(0, LIMIT));
  },

  remove(q: string) {
    write(read().filter((h) => h.q !== q));
  },

  clear() {
    write([]);
  },
};
