"use client";

import { AnimatePresence, motion } from "framer-motion";
import { Bell, ExternalLink, Eye, Trash2, X } from "lucide-react";
import Link from "next/link";
import { useEffect, useRef, useState } from "react";
import toast from "react-hot-toast";

import { api } from "@/lib/api";
import { formatPrice } from "@/lib/format";
import type { PriceAlert, PriceWatch } from "@/lib/types";

const POLL_MS = 30_000;

function formatDelta(pct: number): string {
  const sign = pct > 0 ? "↑" : pct < 0 ? "↓" : "=";
  return `${sign} ${Math.abs(pct).toFixed(1)}%`;
}

function relative(ts: string | null): string {
  if (!ts) return "никогда";
  const diff = Date.now() - Date.parse(ts);
  if (Number.isNaN(diff)) return ts;
  const m = Math.floor(diff / 60_000);
  if (m < 1) return "только что";
  if (m < 60) return `${m} мин назад`;
  const h = Math.floor(m / 60);
  if (h < 24) return `${h} ч назад`;
  return `${Math.floor(h / 24)} дн назад`;
}

/** Header-mounted bell: shows unread alert count, opens a panel listing
 *  recent alerts + active watches. Polls /alerts/count every 30 s and on
 *  the `pp.watch.refresh` window event so newly-added watches surface
 *  immediately without a full page reload. */
export function WatchBell() {
  const [open, setOpen] = useState(false);
  const [unread, setUnread] = useState(0);
  const [alerts, setAlerts] = useState<PriceAlert[] | null>(null);
  const [watches, setWatches] = useState<PriceWatch[] | null>(null);
  const [busy, setBusy] = useState(false);
  const wrap = useRef<HTMLDivElement | null>(null);

  async function refreshCount() {
    try { setUnread((await api.watches.unreadCount()).unread); } catch { /* offline */ }
  }
  async function refreshLists() {
    try {
      setBusy(true);
      const [a, w] = await Promise.all([
        api.watches.alerts({ limit: 25 }),
        api.watches.list(),
      ]);
      setAlerts(a); setWatches(w);
    } catch (e) {
      toast.error(e instanceof Error ? e.message.slice(0, 80) : "ошибка загрузки");
    } finally { setBusy(false); }
  }

  // Initial poll + interval + cross-component refresh signal.
  useEffect(() => {
    refreshCount();
    const id = window.setInterval(refreshCount, POLL_MS);
    const handler = () => refreshCount();
    window.addEventListener("pp.watch.refresh", handler);
    return () => {
      window.clearInterval(id);
      window.removeEventListener("pp.watch.refresh", handler);
    };
  }, []);

  // Pull full lists every time the panel opens (fresh snapshots beat stale state).
  useEffect(() => { if (open) refreshLists(); }, [open]);

  // Close on outside click.
  useEffect(() => {
    function onDoc(e: MouseEvent) {
      if (!wrap.current?.contains(e.target as Node)) setOpen(false);
    }
    document.addEventListener("mousedown", onDoc);
    return () => document.removeEventListener("mousedown", onDoc);
  }, []);

  async function markAllRead() {
    try { await api.watches.markAllRead(); setUnread(0); await refreshLists(); }
    catch (e) { toast.error(e instanceof Error ? e.message.slice(0, 80) : "не вышло"); }
  }
  async function removeWatch(id: number) {
    try { await api.watches.remove(id); await refreshLists(); }
    catch (e) { toast.error(e instanceof Error ? e.message.slice(0, 80) : "не вышло"); }
  }
  async function readAlert(id: number) {
    try { await api.watches.markRead(id); await refreshLists(); await refreshCount(); }
    catch { /* idempotent */ }
  }

  return (
    <div ref={wrap} className="relative">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className="btn btn-ghost !p-2 rounded-full relative"
        aria-label="Уведомления по ценам"
        title="Уведомления о ценах"
      >
        <Bell className="w-4 h-4" />
        {unread > 0 && (
          <span className="absolute -top-0.5 -right-0.5 min-w-[16px] h-[16px] px-1 grid place-items-center rounded-full text-[10px] font-semibold bg-[var(--color-bad)] text-white">
            {unread > 99 ? "99+" : unread}
          </span>
        )}
      </button>

      <AnimatePresence>
        {open && (
          <motion.div
            initial={{ opacity: 0, y: -6, scale: 0.97 }}
            animate={{ opacity: 1, y: 0, scale: 1 }}
            exit={{ opacity: 0, y: -6, scale: 0.97 }}
            transition={{ duration: 0.18, ease: [0.16, 1, 0.3, 1] }}
            className="absolute top-full right-0 mt-2 w-[400px] max-w-[calc(100vw-2rem)] card flex flex-col overflow-hidden z-50"
            style={{ maxHeight: "70vh", boxShadow: "0 18px 48px rgba(11,13,18,0.16)" }}
          >
            <div className="flex items-center justify-between gap-3 px-4 py-3 border-b border-[var(--color-border)]">
              <div>
                <div className="text-sm font-semibold leading-tight">Слежение за ценами</div>
                <div className="text-[11px] text-[var(--color-ink-4)]">
                  Уведомления приходят, когда цена меняется на ≥ порога
                </div>
              </div>
              {unread > 0 && (
                <button
                  type="button"
                  onClick={markAllRead}
                  className="text-[11px] text-[var(--color-accent)] hover:underline shrink-0"
                >
                  Прочесть всё
                </button>
              )}
            </div>

            <div className="flex-1 overflow-y-auto">
              {/* Alerts feed */}
              <section className="px-4 py-3">
                <div className="text-[11px] uppercase tracking-wider font-semibold text-[var(--color-ink-4)] mb-2">
                  Изменения цены
                </div>
                {alerts === null && busy ? (
                  <div className="text-xs text-[var(--color-ink-4)] italic">загружаем…</div>
                ) : alerts && alerts.length > 0 ? (
                  <ul className="space-y-2">
                    {alerts.map((a) => {
                      const dropped = a.diff_pct < 0;
                      return (
                        <li
                          key={a.id}
                          className={
                            (a.read_at ? "opacity-60 " : "") +
                            "p-2.5 rounded-lg border border-[var(--color-border)] hover:bg-[var(--color-surface-2)] transition-colors"
                          }
                        >
                          <div className="flex items-start justify-between gap-2">
                            <Link
                              href={`/search?q=${encodeURIComponent(a.query)}`}
                              onClick={() => { readAlert(a.id); setOpen(false); }}
                              className="text-sm font-medium text-[var(--color-ink)] hover:text-[var(--color-accent)] truncate flex-1"
                              title={a.query}
                            >
                              {a.query}
                            </Link>
                            <span
                              className="text-xs font-semibold tabular-nums shrink-0 px-1.5 py-0.5 rounded-md"
                              style={{
                                color: dropped ? "var(--color-good)" : "var(--color-bad)",
                                background: dropped
                                  ? "color-mix(in srgb, var(--color-good) 12%, transparent)"
                                  : "color-mix(in srgb, var(--color-bad) 12%, transparent)",
                              }}
                            >
                              {formatDelta(a.diff_pct)}
                            </span>
                          </div>
                          <div className="mt-1 text-xs text-[var(--color-ink-3)] flex items-center gap-2 tabular-nums">
                            <span className="line-through text-[var(--color-ink-4)]">
                              {formatPrice(a.prev_price)}
                            </span>
                            <span>→</span>
                            <span className="font-semibold text-[var(--color-ink)]">
                              {formatPrice(a.new_price)}
                            </span>
                            <span className="text-[var(--color-ink-4)]">· {relative(a.created_at)}</span>
                          </div>
                          {a.offer_url && a.offer_name && (
                            <a
                              href={a.offer_url}
                              target="_blank"
                              rel="noopener noreferrer"
                              onClick={() => readAlert(a.id)}
                              className="mt-1 text-xs text-[var(--color-accent)] inline-flex items-center gap-1 truncate"
                            >
                              {a.offer_name.slice(0, 60)}{a.offer_name.length > 60 ? "…" : ""}
                              <ExternalLink className="w-3 h-3 shrink-0" />
                            </a>
                          )}
                        </li>
                      );
                    })}
                  </ul>
                ) : (
                  <div className="text-xs text-[var(--color-ink-4)] italic">
                    Пока изменений нет — добавьте товар в слежение через кнопку «Следить» в поиске.
                  </div>
                )}
              </section>

              {/* Active watches */}
              {watches && watches.length > 0 && (
                <section className="px-4 py-3 border-t border-[var(--color-border)]">
                  <div className="text-[11px] uppercase tracking-wider font-semibold text-[var(--color-ink-4)] mb-2">
                    Активные ({watches.length})
                  </div>
                  <ul className="space-y-1.5">
                    {watches.map((w) => (
                      <li
                        key={w.id}
                        className="flex items-center gap-2 text-xs group"
                      >
                        <Eye className="w-3 h-3 text-[var(--color-ink-4)] shrink-0" />
                        <Link
                          href={`/search?q=${encodeURIComponent(w.query)}&region_id=${w.region_id}`}
                          onClick={() => setOpen(false)}
                          className="flex-1 truncate text-[var(--color-ink-2)] hover:text-[var(--color-accent)]"
                          title={w.query}
                        >
                          {w.query}
                        </Link>
                        <span className="text-[var(--color-ink-4)] shrink-0 tabular-nums">
                          {w.last_best_price ? formatPrice(w.last_best_price) : "—"}
                        </span>
                        <button
                          type="button"
                          onClick={() => removeWatch(w.id)}
                          className="opacity-0 group-hover:opacity-100 p-1 -m-1 rounded hover:bg-[var(--color-surface-2)] transition-all"
                          aria-label="Удалить из слежения"
                          title="Удалить из слежения"
                        >
                          <Trash2 className="w-3 h-3 text-[var(--color-ink-4)] hover:text-[var(--color-bad)]" />
                        </button>
                      </li>
                    ))}
                  </ul>
                </section>
              )}
            </div>

            <div className="px-4 py-2 border-t border-[var(--color-border)] flex items-center justify-between text-[10px] text-[var(--color-ink-4)]">
              <span>Проверка раз в {watches?.[0]?.interval_min ?? 15} мин</span>
              <button
                type="button"
                onClick={() => setOpen(false)}
                className="hover:text-[var(--color-ink-2)] inline-flex items-center gap-1"
              >
                Закрыть <X className="w-3 h-3" />
              </button>
            </div>
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  );
}
