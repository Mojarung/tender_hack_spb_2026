"use client";

import { AnimatePresence, motion } from "framer-motion";
import React from "react";

import type { ClarificationOption, QueryClarification } from "@/lib/types";

interface SmartSuggestionCardProps {
  clarification: QueryClarification | null;
  onSelect: (option: ClarificationOption) => void;
}

/** Minimal one-line clarifier: question text + a row of small option
 *  chips. No header, no badge, no icon — the question itself is the
 *  signal. Shown BEFORE the search runs so the user picks an
 *  interpretation first and we don't waste a multi-source scrape. */
export const SmartSuggestionCard: React.FC<SmartSuggestionCardProps> = ({
  clarification,
  onSelect,
}) => {
  if (!clarification || !clarification.is_ambiguous || clarification.options.length === 0) {
    return null;
  }
  return (
    <AnimatePresence>
      <motion.div
        initial={{ opacity: 0, y: -8 }}
        animate={{ opacity: 1, y: 0 }}
        exit={{ opacity: 0, y: -8 }}
        transition={{ duration: 0.2, ease: [0.16, 1, 0.3, 1] }}
        className="mb-5"
      >
        <div className="rounded-xl border border-zinc-200 bg-white px-4 py-3 flex flex-wrap items-center gap-2">
          <span className="text-sm text-zinc-700">
            {clarification.reason ?? "Что именно вы ищете?"}
          </span>
          <div className="flex flex-wrap gap-1.5 ml-auto">
            {clarification.options.map((option, idx) => {
              const isRawSearch = idx === clarification.options.length - 1;
              const cleanLabel = option.label
                .replace(/[\u{1F300}-\u{1FAFF}\u{2600}-\u{27BF}]/gu, "")
                .trim();
              return (
                <button
                  key={idx}
                  type="button"
                  onClick={() => onSelect(option)}
                  title={option.text}
                  className={
                    "text-xs font-medium px-3 py-1.5 rounded-full transition-colors " +
                    (isRawSearch
                      ? "bg-zinc-100 hover:bg-zinc-200 text-zinc-600"
                      : "bg-[var(--color-accent)] hover:bg-[var(--color-accent-2)] text-white")
                  }
                >
                  {cleanLabel}
                </button>
              );
            })}
          </div>
        </div>
      </motion.div>
    </AnimatePresence>
  );
};
