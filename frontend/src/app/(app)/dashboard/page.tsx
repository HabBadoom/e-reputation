"use client";

import { useQuery } from "@tanstack/react-query";
import Link from "next/link";
import { useI18n } from "@/components/providers";
import { Button, Card, ErrorBanner, Spinner } from "@/components/ui";
import { api, type Capability, type Dashboard } from "@/lib/api";
import { hasMessage } from "@/lib/i18n";
import { useCan } from "@/lib/session";

function Stat({ label, value, sub }: { label: string; value: number; sub?: string }) {
  return (
    <div className="card p-5">
      <p className="text-xs font-medium uppercase tracking-wide text-ink-mute">{label}</p>
      <p className="mt-2 text-3xl font-semibold tabular-nums">{value}</p>
      {sub && <p className="mt-1 text-xs text-ink-soft">{sub}</p>}
    </div>
  );
}

function Unavailable({ label, cap }: { label: string; cap: Capability }) {
  const { t } = useI18n();
  return (
    <div className="rounded border border-dashed border-line p-5">
      <p className="text-xs font-medium uppercase tracking-wide text-ink-mute">{label}</p>
      <p className="mt-2 text-sm font-medium text-ink-soft">{t("dash.notYet")}</p>
      {cap.reason && <p className="mt-1 text-xs text-ink-mute">{cap.reason}</p>}
    </div>
  );
}

export default function DashboardPage() {
  const { t, fmtDate } = useI18n();
  const canWrite = useCan("brands:write");
  const q = useQuery({ queryKey: ["dashboard"], queryFn: () => api.get<Dashboard>("/dashboard/summary") });
  if (q.isLoading) return <Spinner />;
  if (q.error || !q.data) return <ErrorBanner error={q.error} />;
  const d = q.data;

  return (
    <div className="space-y-6">
      <h1 className="text-2xl font-semibold">{t("dash.title")}</h1>
      {d.brands_total === 0 && (
        <div className="flex flex-wrap items-center justify-between gap-3 rounded border border-brand/30 bg-brand-tint px-5 py-4">
          <p className="text-sm text-brand-strong">{t("dash.getStarted")}</p>
          {canWrite && <Link href="/brands"><Button>{t("brands.new")}</Button></Link>}
        </div>
      )}
      <div className="grid gap-4 sm:grid-cols-3">
        <Stat label={t("dash.brands")} value={d.brands_total} sub={`${d.brands_active} ${t("dash.active")}`} />
        <Stat label={t("dash.projects")} value={d.projects_total} sub={`${d.projects_active} ${t("dash.active")}`} />
        <Stat label={t("dash.members")} value={d.members_total} />
        <Stat label={t("dash.products")} value={d.products_total} />
        <Stat label={t("dash.competitors")} value={d.competitors_total} />
        <Stat label={t("dash.aliases")} value={d.aliases_total} />
      </div>
      {d.last_run && (
        <Card title={t("dash.lastRun")}>
          <p className="text-sm">
            <Link href="/monitoring" className="font-medium hover:underline">{d.last_run.project_name}</Link>
            {" · "}{t(`monitoring.run.${d.last_run.status}`)}
            {d.last_run.finished_at && <span className="text-ink-mute"> · {fmtDate(d.last_run.finished_at)}</span>}
          </p>
          {d.last_run.reason === "no_active_connectors" && (
            <p className="mt-1 text-xs text-ink-soft">{t("monitoring.noConnectors")}</p>
          )}
        </Card>
      )}
      <div className="grid gap-4 sm:grid-cols-3">
        <Stat label={t("dash.mentionsTotal")} value={d.mention_stats.total}
              sub={`${d.mention_stats.last_7_days} ${t("dash.last7")}`} />
        <Stat label={t("dash.sourcesCovered")} value={d.source_stats.monitored}
              sub={t("dash.ofRegistered", { n: d.source_stats.registered })} />
        {d.sentiment.available
          ? <Stat label={t("dash.sentiment")} value={d.sentiment_stats.analysed}
                  sub={t("dash.analysedOf", { n: d.sentiment_stats.analysed })} />
          : <Unavailable label={t("dash.sentiment")} cap={d.sentiment} />}
      </div>
      {d.sentiment_stats.about_brand > 0 && (
        <Card title={t("dash.sentimentTitle")}>
          <div className="flex h-3 overflow-hidden rounded">
            {d.sentiment_stats.distribution.map((x) => (
              <div key={x.key} title={`${x.key}: ${x.count}`}
                   className={{ positive: "bg-signal-pos", negative: "bg-signal-neg", mixed: "bg-signal-warn",
                     neutral: "bg-ink-mute", UNCERTAIN: "bg-line" }[x.key] ?? "bg-line"}
                   style={{ width: `${(x.count / d.sentiment_stats.about_brand) * 100}%` }} />
            ))}
          </div>
          <ul className="mt-3 flex flex-wrap gap-4 text-sm">
            {d.sentiment_stats.distribution.map((x) => (
              <li key={x.key}>{hasMessage(`sent.${x.key}`) ? t(`sent.${x.key}` as "sent.positive") : x.key}: <span className="tabular-nums font-medium">{x.count}</span>
                <span className="text-ink-mute"> ({Math.round((x.count / d.sentiment_stats.about_brand) * 100)} %)</span></li>
            ))}
          </ul>
          {!d.sentiment_stats.validated && <p className="mt-3 text-xs text-signal-warn">{t("dash.notValidatedNote")}</p>}
        </Card>
      )}
      {d.mention_stats.total > 0 && (
        <div className="grid gap-4 md:grid-cols-2">
          <Card title={t("dash.topDomains")} action={<Link href="/mentions" className="text-sm text-brand hover:underline">{t("nav.mentions")}</Link>}>
            <ul className="space-y-2 text-sm">
              {d.mention_stats.top_domains.map((x) => {
                const max = d.mention_stats.top_domains[0]?.count || 1;
                return (
                  <li key={x.key}>
                    <div className="mb-0.5 flex justify-between"><span dir="ltr">{x.key}</span><span className="tabular-nums text-ink-soft">{x.count}</span></div>
                    <div className="h-1.5 rounded bg-paper-sunk"><div className="h-1.5 rounded bg-brand" style={{ width: `${(x.count / max) * 100}%` }} /></div>
                  </li>
                );
              })}
            </ul>
          </Card>
          <Card title={t("dash.verified")}>
            <p className="text-3xl font-semibold tabular-nums">{d.mention_stats.verified_on_page}</p>
            <p className="mt-1 text-xs text-ink-soft">/ {d.mention_stats.total} · {d.mention_stats.reposts} {t("mentions.repost").toLowerCase()}</p>
            <ul className="mt-3 flex flex-wrap gap-2 text-xs">
              {d.mention_stats.by_method.map((x) => (
                <li key={x.key} className="rounded bg-paper-sunk px-2 py-1">{t(`method.${x.key}` as "method.rss")}: {x.count}</li>
              ))}
            </ul>
          </Card>
        </div>
      )}
      {d.recent_activity.length > 0 && (
        <Card title={t("dash.activity")}>
          <ul className="divide-y divide-line text-sm">
            {d.recent_activity.map((a, i) => (
              <li key={i} className="flex justify-between gap-4 py-2">
                <span><code className="font-mono text-xs">{a.action}</code>
                  <span className="ms-2 text-ink-soft" dir="ltr">{a.actor_email}</span></span>
                <span className="shrink-0 text-ink-mute">{fmtDate(a.created_at)}</span>
              </li>
            ))}
          </ul>
        </Card>
      )}
    </div>
  );
}
