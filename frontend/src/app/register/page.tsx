"use client";

import { motion } from "framer-motion";
import { ArrowRight, Check } from "lucide-react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useMemo, useState } from "react";
import toast from "react-hot-toast";

import { AuthShell } from "@/components/AuthShell";
import { MeshGradient } from "@/components/shaders/MeshGradient";
import { api } from "@/lib/api";

const EMAIL_RE = /^[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}$/;

function passwordStrength(p: string): { level: 0 | 1 | 2 | 3; label: string } {
  if (p.length < 6) return { level: 0, label: "слишком короткий" };
  let score = 0;
  if (p.length >= 10) score++;
  if (/[A-Z]/.test(p) && /[a-z]/.test(p)) score++;
  if (/\d/.test(p) || /[^A-Za-z0-9]/.test(p)) score++;
  if (score <= 1) return { level: 1, label: "слабый" };
  if (score === 2) return { level: 2, label: "нормальный" };
  return { level: 3, label: "хороший" };
}

export default function RegisterPage() {
  const router = useRouter();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [displayName, setDisplayName] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  const emailOk = useMemo(() => EMAIL_RE.test(email.trim()), [email]);
  const strength = useMemo(() => passwordStrength(password), [password]);
  const canSubmit = !busy && emailOk && password.length >= 6;

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    if (!canSubmit) return;
    setBusy(true); setErr(null);
    try {
      await api.auth.register(email.trim(), password, displayName.trim() || undefined);
      await api.auth.login(email.trim(), password);
      toast.success("Готово! Добро пожаловать");
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
      title="Создать аккаунт"
      subtitle="Бесплатно, без подтверждения e-mail"
      background={
        <MeshGradient
          /* Warm sunset palette — distinct from /login plasma */
          bg={[0.05, 0.04, 0.16]}
          colorA={[0.95, 0.45, 0.35]}    /* coral */
          colorB={[0.55, 0.30, 0.85]}    /* violet */
          colorC={[0.95, 0.78, 0.36]}    /* amber */
          speed={0.85}
        />
      }
    >
      <form onSubmit={submit} className="space-y-4" noValidate>
        <Field
          label="Имя" value={displayName} onChange={setDisplayName}
          placeholder="Как к вам обращаться?" autoFocus optional
        />
        <Field
          label="E-mail" type="email" value={email} onChange={setEmail}
          autoComplete="email"
          ok={email.length > 0 && emailOk}
          err={email.length > 0 && !emailOk ? "введите корректный e-mail" : null}
        />
        <div>
          <Field
            label="Пароль" type="password" value={password} onChange={setPassword}
            autoComplete="new-password"
            err={password.length > 0 && password.length < 6 ? "минимум 6 символов" : null}
          />
          {password.length > 0 && (
            <div className="mt-2 flex items-center gap-2">
              <div className="flex gap-1 flex-1">
                {[0, 1, 2].map((i) => (
                  <div key={i} className={
                    "h-1 flex-1 rounded-full transition-colors " +
                    (i < strength.level
                      ? strength.level === 1 ? "bg-red-400"
                        : strength.level === 2 ? "bg-amber-400"
                          : "bg-emerald-400"
                      : "bg-white/15")
                  } />
                ))}
              </div>
              <span className="text-xs text-white/55 w-24 text-right">{strength.label}</span>
            </div>
          )}
        </div>

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
          disabled={!canSubmit}
          className="btn bg-white text-[var(--color-ink)] hover:bg-white/95 w-full justify-center disabled:opacity-50"
        >
          {busy ? <span className="spinner" /> : <>Создать аккаунт <ArrowRight className="w-4 h-4" /></>}
        </button>
      </form>

      <p className="mt-6 text-sm text-white/65 text-center">
        Уже есть аккаунт?{" "}
        <Link href="/login" className="text-white font-semibold hover:underline">
          Войти
        </Link>
      </p>
    </AuthShell>
  );
}

function Field({
  label, type = "text", value, onChange,
  autoComplete, autoFocus, placeholder, ok, err, optional,
}: {
  label: string; type?: string; value: string; onChange: (v: string) => void;
  autoComplete?: string; autoFocus?: boolean; placeholder?: string;
  ok?: boolean; err?: string | null; optional?: boolean;
}) {
  return (
    <label className="block">
      <span className="flex items-center justify-between text-xs font-semibold uppercase tracking-wider">
        <span className="text-white/70">{label}</span>
        {optional && <span className="text-white/45 normal-case font-medium">опционально</span>}
      </span>
      <div className="relative mt-1.5">
        <input
          type={type}
          value={value}
          autoComplete={autoComplete}
          autoFocus={autoFocus}
          placeholder={placeholder}
          required={!optional}
          onChange={(e) => onChange(e.target.value)}
          className={
            "w-full bg-white/10 border rounded-xl px-3 py-3 text-white placeholder-white/40 focus:outline-none focus:bg-white/15 transition-colors backdrop-blur " +
            (err ? "border-red-400/50 focus:border-red-300"
              : ok ? "border-emerald-400/50 focus:border-emerald-300"
              : "border-white/15 focus:border-white/40")
          }
        />
        {ok && <Check className="absolute right-3 top-1/2 -translate-y-1/2 w-4 h-4 text-emerald-300" />}
      </div>
      {err && <div className="mt-1 text-xs text-red-300">{err}</div>}
    </label>
  );
}
