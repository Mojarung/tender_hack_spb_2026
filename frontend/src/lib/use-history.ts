"use client";

import { useEffect, useState } from "react";

import { history, type HistoryItem } from "./history";

/** Reactive view onto the search history — re-renders on push/remove. */
export function useHistory(): HistoryItem[] {
  const [items, setItems] = useState<HistoryItem[]>([]);

  useEffect(() => {
    setItems(history.list());
    const onChange = () => setItems(history.list());
    window.addEventListener("pp.history", onChange);
    window.addEventListener("storage", onChange);
    return () => {
      window.removeEventListener("pp.history", onChange);
      window.removeEventListener("storage", onChange);
    };
  }, []);

  return items;
}
