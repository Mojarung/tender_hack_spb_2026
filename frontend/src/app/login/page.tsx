"use client";

import { motion } from "framer-motion";
import { ArrowRight } from "lucide-react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState } from "react";
import toast from "react-hot-toast";

import { AuthShell } from "@/components/AuthShell";
import { PlasmaShader } from "@/components/shaders/PlasmaShader";
import { api } from "@/lib/api";

export default function LoginPage() {
  const router = useRouter();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    if (busy) return;
    setBusy(true); setErr(null);
    try {
      await api.auth.login(email.trim(), password);
      toast.success("С возвращением!");
      router.push("/favorites");
    } catch (e) {
      const msg = e instanceof Error ? e.message : "не вышло";
      setErr(msg);
      toast.error(msg.slice(0, 80));
    } finally {
      setBusy(false);
    }
  }

  return (
    <AuthShell
      title="Вход"
      subtitle="Чтобы сохранять понравившиеся товары"
      background={
        <PlasmaShader
          /* Iridescent purple-cyan plasma */
          a={[0.30, 0.30, 0.45]}
          b={[0.50, 0.45, 0.55]}
          c={[1.00, 1.00, 1.00]}
          d={[0.10, 0.20, 0.60]}
          speed={0.40}
        />
      }
    >
      <form onSubmit={submit} className="space-y-4" noValidate>
        <Field label="E-mail" type="email" value={email} onChange={setEmail} autoComplete="email" autoFocus />
        <Field label="Пароль" type="password" value={password} onChange={setPassword} autoComplete="current-password" />

        {err && (
          <motion.div
            initial={{ opacity: 0, height: 0 }}
            animate={{ opacity: 1, height: "auto" }}
            className="text-sm p-3 rounded-lg bg-red-500/15 text-red-200 border border-red-400/30"
          >
            {err}
          </motion.div>
        )}

        <button
          type="submit"
          disabled={busy || !email || !password}
          className="btn bg-white text-[var(--color-ink)] hover:bg-white/95 w-full justify-center disabled:opacity-50"
        >
          {busy ? <span className="spinner" /> : <>Войти <ArrowRight className="w-4 h-4" /></>}
        </button>
      </form>

      <p className="mt-6 text-sm text-white/65 text-center">
        Нет аккаунта?{" "}
        <Link href="/register" className="text-white font-semibold hover:underline">
          Создать
        </Link>
      </p>
    </AuthShell>
  );
}

function Field({
  label, type = "text", value, onChange, autoComplete, autoFocus,
}: {
  label: string; type?: string; value: string; onChange: (v: string) => void;
  autoComplete?: string; autoFocus?: boolean;
}) {
  return (
    <label className="block">
      <span className="text-xs font-semibold text-white/70 uppercase tracking-wider">{label}</span>
      <input
        type={type}
        value={value}
        autoComplete={autoComplete}
        autoFocus={autoFocus}
        onChange={(e) => onChange(e.target.value)}
        required
        className="mt-1.5 w-full bg-white/10 border border-white/15 rounded-xl px-3 py-3 text-white placeholder-white/40 focus:outline-none focus:border-white/40 focus:bg-white/15 transition-colors backdrop-blur"
      />
    </label>
  );
}
