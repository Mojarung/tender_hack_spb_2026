"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState } from "react";

import { api } from "@/lib/api";

export default function LoginPage() {
  const router = useRouter();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setErr(null);
    try {
      await api.auth.login(email, password);
      router.push("/favorites");
    } catch (e) {
      setErr(e instanceof Error ? e.message : "не вышло");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="max-w-[420px] mx-auto card p-8">
      <h1 className="text-2xl font-semibold">Войти</h1>
      <p className="text-sm text-[var(--color-ink-400)] mt-1">
        Чтобы сохранять понравившиеся товары.
      </p>

      <form onSubmit={submit} className="mt-6 space-y-4">
        <Field label="E-mail" type="email" value={email} onChange={setEmail} />
        <Field label="Пароль" type="password" value={password} onChange={setPassword} />
        {err && <div className="text-sm text-[var(--color-error)]">{err}</div>}
        <button type="submit" className="btn-primary w-full" disabled={busy}>
          {busy ? "Входим…" : "Войти"}
        </button>
      </form>

      <p className="mt-6 text-sm text-[var(--color-ink-500)]">
        Нет аккаунта? <Link href="/register" className="text-[var(--color-brand-500)] font-semibold">Зарегистрироваться</Link>
      </p>
    </div>
  );
}

function Field({
  label, type, value, onChange,
}: { label: string; type: string; value: string; onChange: (v: string) => void }) {
  return (
    <label className="block">
      <span className="text-xs font-semibold text-[var(--color-ink-700)] uppercase tracking-wider">{label}</span>
      <input
        type={type}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        required
        className="mt-1 w-full px-3 py-3 rounded-[8px] bg-[var(--color-ink-50)] border border-[var(--color-ink-100)] focus:border-[var(--color-brand-400)] focus:outline-none"
      />
    </label>
  );
}
