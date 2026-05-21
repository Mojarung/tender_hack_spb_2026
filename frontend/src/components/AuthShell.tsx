"use client";

import { motion } from "framer-motion";
import { ReactNode } from "react";

interface Props {
  title: string;
  subtitle: string;
  children: ReactNode;
  /** Background slot — pass any <Shader /> component as the bg layer. */
  background: ReactNode;
  /** Tailwind class for the CSS-only fallback under the canvas. */
  fallback?: string;
}

export function AuthShell({ title, subtitle, children, background, fallback }: Props) {
  return (
    <section
      className={`relative overflow-hidden rounded-3xl isolate min-h-[640px] ${fallback ?? ""}`}
      style={{
        background: !fallback
          ? "linear-gradient(160deg, #050316 0%, #1f1147 45%, #88146a 100%)"
          : undefined,
      }}
    >
      {background}
      <div
        aria-hidden
        className="absolute inset-0 z-[1] pointer-events-none"
        style={{
          background:
            "radial-gradient(60% 80% at 50% 30%, transparent 0%, rgba(4,3,16,0.55) 100%)",
        }}
      />

      <div className="relative z-10 min-h-[640px] grid place-items-center p-6 md:p-10">
        <motion.div
          initial={{ opacity: 0, y: 14, scale: 0.98 }}
          animate={{ opacity: 1, y: 0,  scale: 1 }}
          transition={{ duration: 0.5, ease: [0.16, 1, 0.3, 1] }}
          className="w-full max-w-md rounded-2xl border border-white/15 backdrop-blur-xl p-7 text-white"
          style={{ background: "rgba(18, 14, 50, 0.55)" }}
        >
          <h1 className="text-2xl md:text-3xl font-semibold tracking-tight">{title}</h1>
          <p className="text-sm text-white/70 mt-1">{subtitle}</p>
          <div className="mt-6">{children}</div>
        </motion.div>
      </div>
    </section>
  );
}
