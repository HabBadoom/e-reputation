"use client";

import type { ButtonHTMLAttributes, InputHTMLAttributes, ReactNode } from "react";
import { ApiError } from "@/lib/api";
import { LOCALES } from "@/lib/i18n";
import { useI18n } from "./providers";

type Variant = "primary" | "ghost" | "danger";
const variants: Record<Variant, string> = {
  primary: "bg-brand text-white hover:bg-brand-strong disabled:bg-ink-mute",
  ghost: "border border-line bg-paper-raised text-ink hover:bg-paper-sunk",
  danger: "border border-signal-neg/30 text-signal-neg hover:bg-signal-neg/5",
};

export function Button({
  variant = "primary",
  className = "",
  ...p
}: ButtonHTMLAttributes<HTMLButtonElement> & { variant?: Variant }) {
  return (
    <button
      {...p}
      className={`inline-flex items-center justify-center gap-2 rounded px-3.5 py-2 text-sm font-medium transition-colors disabled:cursor-not-allowed ${variants[variant]} ${className}`}
    />
  );
}

export function Field({
  label,
  hint,
  error,
  ...p
}: InputHTMLAttributes<HTMLInputElement> & { label: string; hint?: string; error?: string }) {
  return (
    <label className="block">
      <span className="label">{label}</span>
      <input {...p} className="field" aria-invalid={!!error} />
      {hint && !error && <span className="mt-1 block text-xs text-ink-mute">{hint}</span>}
      {error && <span className="mt-1 block text-xs text-signal-neg">{error}</span>}
    </label>
  );
}

export function Card({ title, action, children, className = "" }: {
  title?: ReactNode; action?: ReactNode; children: ReactNode; className?: string;
}) {
  return (
    <section className={`card ${className}`}>
      {(title || action) && (
        <header className="flex items-center justify-between border-b border-line px-5 py-3">
          <h2 className="text-sm font-semibold">{title}</h2>
          {action}
        </header>
      )}
      <div className="p-5">{children}</div>
    </section>
  );
}

const tones = {
  neutral: "bg-paper-sunk text-ink-soft",
  brand: "bg-brand-tint text-brand-strong",
  warn: "bg-signal-warn/10 text-signal-warn",
  neg: "bg-signal-neg/10 text-signal-neg",
} as const;

export function Badge({ tone = "neutral", children }: { tone?: keyof typeof tones; children: ReactNode }) {
  return <span className={`inline-flex rounded px-1.5 py-0.5 text-xs font-medium ${tones[tone]}`}>{children}</span>;
}

export function ErrorBanner({ error }: { error: unknown }) {
  const { t } = useI18n();
  if (!error) return null;
  const msg = error instanceof ApiError ? error.message : t("common.error");
  const rid = error instanceof ApiError ? error.requestId : undefined;
  return (
    <div role="alert" className="rounded border border-signal-neg/30 bg-signal-neg/5 px-3 py-2 text-sm text-signal-neg">
      {msg}
      {rid && <span className="ms-2 font-mono text-xs opacity-60">#{rid.slice(0, 8)}</span>}
    </div>
  );
}

export function fieldErrors(error: unknown): Record<string, string> {
  if (!(error instanceof ApiError) || error.code !== "validation_error") return {};
  const out: Record<string, string> = {};
  for (const d of (error.details as { loc: (string | number)[]; msg: string }[]) ?? []) {
    out[String(d.loc[d.loc.length - 1])] = d.msg;
  }
  return out;
}

export function LangSwitcher() {
  const { locale, setLocale, t } = useI18n();
  return (
    <div className="flex gap-1 text-xs" role="group" aria-label="Language">
      {LOCALES.map((l) => (
        <button
          key={l}
          onClick={() => setLocale(l)}
          aria-pressed={l === locale}
          className={`rounded px-2 py-1 ${l === locale ? "bg-ink text-paper" : "text-ink-soft hover:bg-paper-sunk"}`}
        >
          {t(`lang.${l}`)}
        </button>
      ))}
    </div>
  );
}

export function Spinner() {
  const { t } = useI18n();
  return <p className="py-8 text-center text-sm text-ink-mute">{t("common.loading")}</p>;
}
