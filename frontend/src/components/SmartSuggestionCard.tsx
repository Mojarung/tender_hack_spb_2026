"use client";

import React from "react";
import { motion, AnimatePresence } from "framer-motion";
import { Sparkles, ArrowRight, CornerDownRight } from "lucide-react";
import type { QueryClarification, ClarificationOption } from "@/lib/types";

interface SmartSuggestionCardProps {
  clarification: QueryClarification | null;
  onSelect: (option: ClarificationOption) => void;
}

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
        initial={{ opacity: 0, y: -20, height: 0 }}
        animate={{ opacity: 1, y: 0, height: "auto" }}
        exit={{ opacity: 0, y: -20, height: 0 }}
        transition={{ type: "spring", stiffness: 300, damping: 25 }}
        className="w-full overflow-hidden mb-6"
      >
        <div className="relative p-6 rounded-2xl bg-white border border-zinc-200 shadow-md">
          {/* Heading */}
          <div className="flex items-center gap-2.5 mb-3 relative z-10">
            <div className="p-1.5 rounded-lg bg-zinc-100 text-zinc-600">
              <Sparkles className="w-4 h-4" />
            </div>
            <h3 className="text-sm font-semibold tracking-tight text-zinc-900">
              Умный поиск PricePulse
            </h3>
            <span className="text-[10px] font-medium px-2 py-0.5 rounded-full bg-zinc-100 text-zinc-600">
              Анализ ИИ
            </span>
          </div>

          {/* Reason text */}
          {clarification.reason && (
            <p className="text-sm text-zinc-600 mb-5 pl-1 relative z-10 leading-relaxed font-medium">
              {clarification.reason}
            </p>
          )}

          {/* Clarification Options Grid */}
          <div className="grid grid-cols-1 md:grid-cols-3 gap-3.5 relative z-10">
            {clarification.options.map((option, idx) => {
              const isRawSearch = idx === clarification.options.length - 1;
              const cleanLabel = option.label
                .replace(/[\u{1F300}-\u{1F6FF}\u{1F900}-\u{1F9FF}\u{2600}-\u{27BF}\u{1F600}-\u{1F64F}\u{1F680}-\u{1F6FF}\u{2600}-\u{26FF}\u{2700}-\u{27BF}\u{1F300}-\u{1F5FF}\u{1FA00}-\u{1FAFF}]/gu, "")
                .trim();
              return (
                <motion.button
                  key={idx}
                  whileHover={{ scale: 1.01, y: -1 }}
                  whileTap={{ scale: 0.99 }}
                  onClick={() => onSelect(option)}
                  className={`flex flex-col text-left p-4 rounded-xl border transition-all duration-200 cursor-pointer ${
                    isRawSearch
                      ? "bg-zinc-50 hover:bg-zinc-100 border-zinc-200 hover:border-zinc-300 text-zinc-600"
                      : "bg-white hover:bg-zinc-50 border-zinc-200 hover:border-zinc-300 text-zinc-900 shadow-sm hover:shadow"
                  }`}
                >
                  <div className="flex items-center gap-2 mb-2">
                    <span className="text-xs font-semibold text-zinc-800 truncate">
                      {cleanLabel}
                    </span>
                  </div>

                  <p className="text-xs text-zinc-500 font-normal leading-relaxed mb-4 grow line-clamp-2">
                    {option.text}
                  </p>

                  <div className="flex items-center gap-1.5 text-[10px] font-bold tracking-wide text-zinc-600 mt-auto uppercase">
                    {isRawSearch ? (
                      <>
                        <span className="text-zinc-500">Продолжить</span>
                        <ArrowRight className="w-3 h-3 text-zinc-400 transition-transform group-hover:translate-x-0.5" />
                      </>
                    ) : (
                      <>
                        <CornerDownRight className="w-3.5 h-3.5 shrink-0" />
                        <span className="truncate">Выбрать: {option.query}</span>
                      </>
                    )}
                  </div>
                </motion.button>
              );
            })}
          </div>
        </div>
      </motion.div>
    </AnimatePresence>
  );
};
