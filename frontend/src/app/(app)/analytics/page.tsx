"use client";

import { useQuery } from "@tanstack/react-query";
import Link from "next/link";
import { useState } from "react";
import { BrandSelect, DailyChart, pct, ReputationValue, SeverityBadge } from "@/components/intel";
import { useI18n } from "@/components/providers";
import { Card, ErrorBanner, Spinner } from "@/components/ui";
import { api, type Overview } from "@/lib/api";
import type { MessageKey } from "@/lib/i18n";

const PERIODS = [7, 30, 90] as const;

function Kpi({ label, value, sub }: { label: string; value: string | number; sub?: string }) {
  return (
    <div className="card p-5">
      <p className="text-xs font-medium uppercase tracking-wide text-ink-mute">{label}</p>
      <p className="mt-2 text-3xl font-semibold tabular-nums">{value}</p>
      {sub && <p className="mt-1 text-xs text-ink-soft">{sub}</p>}
    </div>
  );
}

function share(part: number, d: { positive: number; negative: number; neutral: number; mixed: number }) {
  const n = d.positive + d.negative + d.neutral + d.mixed;
  return n ? part / n : null;
}

export default function AnalyticsPage() {
  const { t, fmtDate } = useI18n();
  const [brand, setBrand] = useState("");
  const [days, setDays] = useState<number>(30);
  const q = useQuery({
    queryKey: ["analytics", brand, days],
    queryFn: () => api.get<Overview>(`/analytics/overview?days=${days}${brand ? `&brand_id=${brand}` : ""}`),
  });
  const o = q.data;

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-2xl font-semibold">{t("intel.title")}</h1>
          <p className="mt-1 text-sm text-ink-soft">{t("intel.subtitle")}</p>
        </div>
        <div className="flex flex-wrap gap-2">
          <div className="w-48"><BrandSelect value={brand} onChange={setBrand} /></div>
          <div className="flex rounded border border-line bg-paper-raised p-0.5" role="group" aria-label={t("intel.period")}>
            {PERIODS.map((p) => (
              <button key={p} onClick={() => setDays(p)} aria-pressed={days === p}
                      className={`rounded px-3 py-1.5 text-sm ${days === p ? "bg-ink text-paper" : "text-ink-soft hover:bg-paper-sunk"}`}>
                {t("intel.days", { n: p })}
              </button>
            ))}
          </div>
        </div>
      </div>

      {q.isLoading && <Spinner />}
      <ErrorBanner error={q.error} />
      {o && (
        <>
          {o.active_crises.length > 0 && (
            <div role="alert" className="flex flex-wrap items-center justify-between gap-3 rounded border border-signal-neg/40 bg-signal-neg/5 px-5 py-4">
              <div className="space-y-1">
                {o.active_crises.map((c) => (
                  <p key={c.id} className="text-sm text-signal-neg">
                    <SeverityBadge s={c.severity} /> <strong className="ms-1">{t("intel.crisisActive", { brand: c.brand })}</strong>
                    <span className="ms-2 text-ink-soft">{t("intel.since", { d: fmtDate(c.started_at) })}</span>
                  </p>
                ))}
              </div>
              <Link href="/alerts?tab=crises" className="text-sm font-medium text-signal-neg hover:underline">{t("intel.seeCrises")}</Link>
            </div>
          )}

          <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
            <div className="card p-5">
              <p className="text-xs font-medium uppercase tracking-wide text-ink-mute">{t("intel.reputation")}</p>
              <ReputationValue r={o.reputation} previous={o.previous_reputation} />
            </div>
            <Kpi label={t("intel.mentions")} value={o.totals.total}
                 sub={t("intel.previous", { v: o.previous_totals.total })} />
            <Kpi label={t("intel.negativeShare")} value={pct(share(o.totals.negative, o.totals))}
                 sub={t("intel.previous", { v: pct(share(o.previous_totals.negative, o.previous_totals)) })} />
            <Kpi label={t("intel.sources")} value={o.totals.domains}
                 sub={o.totals.undated ? t("intel.undated", { n: o.totals.undated }) : undefined} />
          </div>

          <Card title={t("intel.evolution")}>
            {o.totals.total === 0 ? <p className="text-sm text-ink-mute">{t("intel.empty")}</p> : <DailyChart series={o.series} />}
            <p className="mt-3 text-xs text-ink-mute">{t("intel.tzNote", { tz: o.timezone })}</p>
          </Card>

          <div className="grid gap-6 lg:grid-cols-2">
            <Card title={t("intel.sov")}>
              {o.entities.filter((e) => e.entity_type !== "product").length === 0
                ? <p className="text-sm text-ink-mute">{t("intel.empty")}</p> : (
                <table className="w-full text-sm">
                  <thead><tr className="text-start text-xs text-ink-mute">
                    <th className="pb-2 text-start font-medium">{t("intel.entity")}</th>
                    <th className="pb-2 text-start font-medium">{t("intel.share")}</th>
                    <th className="pb-2 text-end font-medium">{t("intel.mentions")}</th>
                    <th className="pb-2 text-end font-medium">{t("intel.index")}</th>
                  </tr></thead>
                  <tbody>
                    {o.entities.filter((e) => e.entity_type !== "product").map((e) => (
                      <tr key={e.entity_type + e.entity_name} className="border-t border-line">
                        <td className="py-2">{e.entity_name}
                          {e.entity_type === "competitor" && <span className="ms-1 text-xs text-ink-mute">({t("intel.competitor")})</span>}
                        </td>
                        <td className="w-1/3 py-2">
                          <div className="flex items-center gap-2">
                            <div className="h-2 flex-1 rounded bg-paper-sunk">
                              <div className={`h-2 rounded ${e.entity_type === "brand" ? "bg-brand" : "bg-ink-mute"}`}
                                   style={{ width: `${Math.round((e.share_of_voice ?? 0) * 100)}%` }} />
                            </div>
                            <span className="w-12 text-end text-xs tabular-nums">{pct(e.share_of_voice)}</span>
                          </div>
                        </td>
                        <td className="py-2 text-end tabular-nums">{e.total}</td>
                        <td className="py-2 text-end tabular-nums">{e.reputation.value ?? "—"}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              )}
            </Card>

            <Card title={t("intel.topics")}>
              {o.topics.length === 0 ? <p className="text-sm text-ink-mute">{t("intel.noTopics")}</p> : (
                <ul className="divide-y divide-line text-sm">
                  {o.topics.slice(0, 10).map((x) => (
                    <li key={x.brand_id + x.topic + x.day} className="flex items-center justify-between gap-3 py-2">
                      <span><strong>{x.topic}</strong>{!brand && <span className="ms-2 text-xs text-ink-mute">{x.brand}</span>}</span>
                      <span className="text-xs text-ink-soft tabular-nums">
                        {t("intel.topicStat", { n: x.count, e: x.expected, d: x.day })}
                      </span>
                    </li>
                  ))}
                </ul>
              )}
            </Card>
          </div>

          {o.entities.some((e) => e.entity_type === "product") && (
            <Card title={t("intel.products")}>
              <ul className="divide-y divide-line text-sm">
                {o.entities.filter((e) => e.entity_type === "product").map((e) => (
                  <li key={e.entity_name} className="flex justify-between py-2">
                    <span>{e.entity_name}</span>
                    <span className="text-ink-soft tabular-nums">{e.total} · {t("sent.negative")} {e.negative} · {t("intel.index")} {e.reputation.value ?? "—"}</span>
                  </li>
                ))}
              </ul>
            </Card>
          )}

          <details className="card p-5 text-sm">
            <summary className="cursor-pointer font-semibold">{t("intel.methodTitle")}</summary>
            <div className="mt-3 space-y-2 text-ink-soft">
              {(["intel.method1", "intel.method2", "intel.method3", "intel.method4"] as MessageKey[]).map((k) => (
                <p key={k}>{t(k, { min: o.reputation.min_sample })}</p>
              ))}
            </div>
          </details>
        </>
      )}
    </div>
  );
}
