"use client";

import { motion } from "framer-motion";
import { ArrowRight, Check } from "lucide-react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useMemo, useState } from "react";
import toast from "react-hot-toast";

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
      // Auto-login right after registration.
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
    <div className="min-h-[60vh] grid place-items-center">
      <motion.div
        initial={{ opacity: 0, y: 12 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ duration: 0.4, ease: [0.16, 1, 0.3, 1] }}
        className="card p-8 w-full max-w-md"
      >
        <h1 className="text-2xl font-semibold tracking-tight">Создать аккаунт</h1>
        <p className="text-sm text-[var(--color-ink-4)] mt-1">Бесплатно, без подтверждения e-mail.</p>

        <form onSubmit={submit} className="mt-6 space-y-4" noValidate>
          <Field
            label="Имя"
            value={displayName}
            onChange={setDisplayName}
            placeholder="Как к вам обращаться?"
            autoFocus
            optional
          />
          <div>
            <Field
              label="E-mail"
              type="email"
              value={email}
              onChange={setEmail}
              autoComplete="email"
              ok={email.length > 0 && emailOk}
              err={email.length > 0 && !emailOk ? "введите корректный e-mail" : null}
            />
          </div>

          <div>
            <Field
              label="Пароль"
              type="password"
              value={password}
              onChange={setPassword}
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
                        ? strength.level === 1 ? "bg-[var(--color-bad)]"
                          : strength.level === 2 ? "bg-[var(--color-warn)]"
                            : "bg-[var(--color-good)]"
                        : "bg-[var(--color-line)]")
                    } />
                  ))}
                </div>
                <span className="text-xs text-[var(--color-ink-4)] w-24 text-right">{strength.label}</span>
              </div>
            )}
          </div>

          {err && (
            <motion.div
              initial={{ opacity: 0, height: 0 }}
              animate={{ opacity: 1, height: "auto" }}
              className="text-sm p-3 rounded-lg bg-red-50 text-red-700 border border-red-100"
            >
              {err}
            </motion.div>
          )}

          <button type="submit" disabled={!canSubmit} className="btn btn-primary w-full justify-center">
            {busy ? <span className="spinner spinner-white" /> : <>Создать аккаунт <ArrowRight className="w-4 h-4" /></>}
          </button>
        </form>

        <p className="mt-6 text-sm text-[var(--color-ink-3)] text-center">
          Уже есть аккаунт?{" "}
          <Link href="/login" className="text-[var(--color-ink)] font-semibold hover:text-[var(--color-accent)] transition-colors">
            Войти
          </Link>
        </p>
      </motion.div>
    </div>
  );
}

function Field({
  label, type = "text", value, onChange,
  autoComplete, autoFocus, placeholder, ok, err, optional,
}: {
  label: string;
  type?: string;
  value: string;
  onChange: (v: string) => void;
  autoComplete?: string;
  autoFocus?: boolean;
  placeholder?: string;
  ok?: boolean;
  err?: string | null;
  optional?: boolean;
}) {
  return (
    <label className="block">
      <span className="flex items-center justify-between text-xs font-semibold uppercase tracking-wider">
        <span className="text-[var(--color-ink-3)]">{label}</span>
        {optional && <span className="text-[var(--color-ink-4)] normal-case font-medium">опционально</span>}
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
            "input " +
            (err ? "!border-[var(--color-bad)]" : ok ? "!border-[var(--color-good)]" : "")
          }
        />
        {ok && <Check className="absolute right-3 top-1/2 -translate-y-1/2 w-4 h-4 text-[var(--color-good)]" />}
      </div>
      {err && <div className="mt-1 text-xs text-[var(--color-bad)]">{err}</div>}
    </label>
  );
}
