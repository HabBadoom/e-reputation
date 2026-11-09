import type { Locale } from "./i18n";

const intl = (l: Locale) => (l === "ar" ? "ar-DZ" : l === "fr" ? "fr-DZ" : "en-GB");

/** Amounts are stored in minor units (centimes). */
export function fmtMoney(minor: number, currency: string, locale: Locale): string {
  try {
    return new Intl.NumberFormat(intl(locale), { style: "currency", currency, minimumFractionDigits: 2 }).format(minor / 100);
  } catch {
    return `${(minor / 100).toFixed(2)} ${currency}`;
  }
}

export function fmtDay(iso: string | null, locale: Locale): string {
  if (!iso) return "—";
  return new Intl.DateTimeFormat(intl(locale), { dateStyle: "medium" }).format(new Date(iso));
}

/** "123,45" or "123.45" typed by an administrator → minor units; null if invalid. */
export function parseMoney(s: string): number | null {
  const v = Number(s.replace(/\s/g, "").replace(",", "."));
  return Number.isFinite(v) && v >= 0 ? Math.round(v * 100) : null;
}

export const USAGE_ROWS = [
  "max_mentions_per_month", "max_ai_calls_per_month", "max_reports_per_month", "max_brands",
  "max_monitoring_projects", "max_members", "max_webhooks", "max_browser_missions_per_day",
  "max_ai_tasks_per_day",
] as const;

export const invoiceTone = (s: "draft" | "issued" | "paid" | "void") =>
  s === "paid" ? "brand" : s === "issued" ? "warn" : "neutral";
