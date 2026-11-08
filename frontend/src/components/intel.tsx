"use client";

// Shared pieces for analytics, alerts and reports: severity badge, localized alert text,
// a dependency-free stacked bar chart, brand picker.
import { useQuery } from "@tanstack/react-query";
import { api, type Alert, type Brand, type DayPoint, type Page, type Reputation, type Severity } from "@/lib/api";
import { hasMessage, type MessageKey } from "@/lib/i18n";
import { useI18n } from "./providers";

export const SEVERITIES: Severity[] = ["INFO", "LOW", "MEDIUM", "HIGH", "CRITICAL"];
const SEV_CLASS: Record<Severity, string> = {
  CRITICAL: "bg-[#8f1d1d] text-white",
  HIGH: "bg-signal-neg text-white",
  MEDIUM: "bg-signal-warn/15 text-signal-warn",
  LOW: "bg-signal-info/10 text-signal-info",
  INFO: "bg-paper-sunk text-ink-soft",
};

export function SeverityBadge({ s }: { s: Severity }) {
  const { t } = useI18n();
  return <span className={`inline-flex rounded px-1.5 py-0.5 text-xs font-semibold ${SEV_CLASS[s]}`}>{t(`sev.${s}` as MessageKey)}</span>;
}

/** Alert title/body in the viewer's language, rebuilt from the measured facts in `data`.
 *  Falls back to the text stored at creation (organization language). */
export function useAlertText() {
  const { t } = useI18n();
  return (a: Alert): { title: string; body: string } => {
    const tk = `alert.${a.kind}.title`;
    const bk = `alert.${a.kind}.body`;
    if (!hasMessage(tk) || !hasMessage(bk)) return { title: a.title, body: a.body };
    const vars: Record<string, string | number> = {};
    for (const [k, v] of Object.entries(a.data)) {
      if (typeof v === "string" || typeof v === "number") vars[k] = v;
    }
    if (typeof vars.severity === "string") vars.severity = t(`sev.${vars.severity}` as MessageKey);
    return { title: t(tk, vars), body: t(bk, vars) };
  };
}

export function useBrands() {
  return useQuery({ queryKey: ["brands", "all"], queryFn: () => api.get<Page<Brand>>("/brands?limit=100"), staleTime: 60_000 });
}

export function BrandSelect({ value, onChange, allLabel }: { value: string; onChange: (v: string) => void; allLabel?: string }) {
  const { t } = useI18n();
  const brands = useBrands().data?.items ?? [];
  return (
    <select className="field" value={value} onChange={(e) => onChange(e.target.value)} aria-label={t("intel.brand")}>
      <option value="">{allLabel ?? t("intel.allBrands")}</option>
      {brands.map((b) => <option key={b.id} value={b.id}>{b.name}</option>)}
    </select>
  );
}

const COLORS = { negative: "#b4372f", positive: "#1f7a4d", neutral: "#8a9a96", other: "#d9d6cc" };

/** Stacked daily bars (negative at the base). Drawn left-to-right in every language: time axes
 *  are not mirrored in Arabic reports either. */
export function DailyChart({ series }: { series: DayPoint[] }) {
  const { t } = useI18n();
  if (!series.length) return null;
  const W = 720, H = 200, L = 30, B = 22, T = 6;
  const w = W - L - 4, h = H - B - T;
  const peak = Math.max(1, ...series.map((d) => d.total));
  const step = w / series.length;
  const bar = Math.max(1, step * 0.72);
  const n = series.length;
  const ticks = Array.from(new Set([0, Math.floor(n / 2), n - 1]));
  return (
    <figure>
      <svg viewBox={`0 0 ${W} ${H}`} className="h-auto w-full" role="img" aria-label={t("intel.chartLabel")} direction="ltr">
        {[0, 0.5, 1].map((f) => (
          <g key={f}>
            <line x1={L} x2={W - 4} y1={T + h - h * f} y2={T + h - h * f} stroke="#e6e3da" />
            <text x={L - 5} y={T + h - h * f + 4} fontSize="10" fill="#6b7d79" textAnchor="end">{Math.round(peak * f)}</text>
          </g>
        ))}
        {series.map((d, i) => {
          const x = L + i * step + (step - bar) / 2;
          let y = T + h;
          const other = d.total - d.positive - d.negative - d.neutral - d.mixed;
          const parts: [keyof typeof COLORS, number][] = [
            ["negative", d.negative], ["positive", d.positive], ["neutral", d.neutral + d.mixed], ["other", other]];
          return (
            <g key={d.day}>
              <title>{`${d.day} — ${t("intel.total")} ${d.total} · ${t("sent.negative")} ${d.negative} · ${t("sent.positive")} ${d.positive}`}</title>
              {parts.filter(([, v]) => v > 0).map(([k, v]) => {
                const bh = (h * v) / peak;
                y -= bh;
                return <rect key={k} x={x} y={y} width={bar} height={bh} fill={COLORS[k]} />;
              })}
            </g>
          );
        })}
        {ticks.map((i) => (
          <text key={i} x={i === 0 ? L : i === n - 1 ? W - 4 : L + i * step + step / 2} y={H - 6} fontSize="10" fill="#6b7d79"
                textAnchor={i === 0 ? "start" : i === n - 1 ? "end" : "middle"}>{series[i].day}</text>
        ))}
      </svg>
      <figcaption className="mt-2 flex flex-wrap gap-x-4 gap-y-1 text-xs text-ink-soft">
        {(["negative", "positive", "neutral", "other"] as const).map((k) => (
          <span key={k} className="inline-flex items-center gap-1.5">
            <i className="inline-block h-2.5 w-2.5 rounded-sm" style={{ background: COLORS[k] }} />{t(`intel.legend.${k}`)}
          </span>
        ))}
      </figcaption>
    </figure>
  );
}

/** Reputation index with an honest empty state below the minimum sample. */
export function ReputationValue({ r, previous }: { r: Reputation; previous?: Reputation }) {
  const { t } = useI18n();
  if (r.value === null) {
    return (
      <div>
        <p className="mt-2 text-3xl font-semibold text-ink-mute">—</p>
        <p className="mt-1 text-xs text-ink-soft">{t("intel.notEnough", { n: r.sample, min: r.min_sample })}</p>
      </div>
    );
  }
  const tone = r.value >= 60 ? "text-signal-pos" : r.value < 40 ? "text-signal-neg" : "text-ink";
  return (
    <div>
      <p className={`mt-2 text-3xl font-semibold tabular-nums ${tone}`}>{r.value}<span className="text-base text-ink-mute"> /100</span></p>
      <p className="mt-1 text-xs text-ink-soft">
        {t("intel.sample", { n: r.sample })}
        {previous && previous.value !== null && <> · {t("intel.previous", { v: previous.value })}</>}
      </p>
    </div>
  );
}

export function pct(v: number | null | undefined): string {
  return v === null || v === undefined ? "—" : `${Math.round(v * 1000) / 10} %`;
}
