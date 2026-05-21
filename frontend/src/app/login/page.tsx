"use client";

import { motion } from "framer-motion";
import { ArrowRight } from "lucide-react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState } from "react";
import toast from "react-hot-toast";

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
    <div className="min-h-[60vh] grid place-items-center pt-8">
      <motion.div
        initial={{ opacity: 0, y: 12 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ duration: 0.4, ease: [0.16, 1, 0.3, 1] }}
        className="card p-8 w-full max-w-md backdrop-blur"
        style={{ background: "rgba(255,255,255,0.92)" }}
      >
        <h1 className="text-2xl font-semibold tracking-tight">Вход</h1>
        <p className="text-sm text-[var(--color-ink-4)] mt-1">Чтобы сохранять понравившиеся товары.</p>

        <form onSubmit={submit} className="mt-6 space-y-4" noValidate>
          <Field label="E-mail" type="email" value={email} onChange={setEmail} autoComplete="email" autoFocus />
          <Field label="Пароль" type="password" value={password} onChange={setPassword} autoComplete="current-password" />

          {err && (
            <motion.div
              initial={{ opacity: 0, height: 0 }}
              animate={{ opacity: 1, height: "auto" }}
              className="text-sm p-3 rounded-lg bg-red-50 text-red-700 border border-red-100"
            >
              {err}
            </motion.div>
          )}

          <button type="submit" disabled={busy || !email || !password} className="btn btn-primary w-full justify-center">
            {busy ? <span className="spinner spinner-white" /> : <>Войти <ArrowRight className="w-4 h-4" /></>}
          </button>
        </form>

        <p className="mt-6 text-sm text-[var(--color-ink-3)] text-center">
          Нет аккаунта?{" "}
          <Link href="/register" className="text-[var(--color-ink)] font-semibold hover:text-[var(--color-accent)] transition-colors">
            Создать
          </Link>
        </p>
      </motion.div>
    </div>
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
      <span className="text-xs font-semibold text-[var(--color-ink-3)] uppercase tracking-wider">{label}</span>
      <input
        type={type}
        value={value}
        autoComplete={autoComplete}
        autoFocus={autoFocus}
        onChange={(e) => onChange(e.target.value)}
        required
        className="input mt-1.5"
      />
    </label>
  );
}
