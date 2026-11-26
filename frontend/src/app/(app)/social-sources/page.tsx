"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { useI18n } from "@/components/providers";
import { Badge, Button, Card, ErrorBanner, Spinner } from "@/components/ui";
import {
  api, type Coverage, type MonitoringProject, type SocialKind, type SocialOverview, type SocialSource,
  type SourceSyncInfo,
} from "@/lib/api";
import type { MessageKey } from "@/lib/i18n";
import { useCan } from "@/lib/session";

const KINDS: { kind: SocialKind; input: "url" | "query" }[] = [
  { kind: "facebook_page", input: "url" }, { kind: "facebook_group", input: "url" },
  { kind: "youtube_channel", input: "url" }, { kind: "youtube_search", input: "query" },
  { kind: "tiktok_profile", input: "query" }, { kind: "tiktok_hashtag", input: "query" },
  { kind: "tiktok_search", input: "query" }, { kind: "linkedin_company", input: "url" },
  { kind: "reddit_community", input: "query" }, { kind: "reddit_search", input: "query" },
];
const COVERAGE_TONE: Record<Coverage, "brand" | "neutral" | "warn" | "neg"> = {
  AVAILABLE: "brand", PARTIALLY_AVAILABLE: "neutral", PENDING_AUTHORIZATION: "warn",
  RESTRICTED: "warn", UNAVAILABLE: "neg", CONNECTOR_ERROR: "neg",
};
const SYNC_TONE: Record<SourceSyncInfo["status"], "brand" | "neutral" | "warn" | "neg"> = {
  queued: "neutral", running: "neutral", succeeded: "brand", partial: "warn", failed: "neg", skipped: "warn",
};

function Providers({ o }: { o: SocialOverview }) {
  const { t } = useI18n();
  return (
    <Card title={t("social.providers")}>
      <ul className="grid gap-3 md:grid-cols-2">
        {o.providers.map((p) => (
          <li key={p.key} className="rounded border border-line p-3 text-sm">
            <p className="mb-1 font-medium">{t(`social.provider.${p.key}`)}</p>
            <p className="mb-2 flex flex-wrap gap-1">
              <Badge tone={p.configured ? "brand" : "neutral"}>{t(p.configured ? "connectors.configured" : "connectors.notConfigured")}</Badge>
              <Badge tone={p.review_status === "approved" ? "brand" : p.review_status === "rejected" ? "neg" : "warn"}>
                {t(`connectors.review.${p.review_status}` as MessageKey)}
              </Badge>
            </p>
            <p className="text-xs text-ink-soft">{t(`social.providerNote.${p.key}`)}</p>
            {!p.allowed && p.note && <p className="mt-1 text-xs text-signal-warn" dir="ltr">{p.note}</p>}
          </li>
        ))}
      </ul>
    </Card>
  );
}

function Usage({ o }: { o: SocialOverview }) {
  const { t } = useI18n();
  return (
    <Card title={t("social.usage")}>
      <ul className="grid gap-x-8 gap-y-3 md:grid-cols-2">
        {o.usage.map((u) => {
          const pct = u.limit ? Math.min(100, Math.round((u.used / u.limit) * 100)) : 0;
          return (
            <li key={u.metric}>
              <div className="mb-1 flex justify-between gap-3 text-sm">
                <span>{t(`social.metric.${u.metric}`)}</span>
                <span className="tabular-nums text-ink-soft">
                  {u.used.toLocaleString()} / {u.limit === null ? t("plan.unlimited") : u.limit.toLocaleString()}
                </span>
              </div>
              {u.limit !== null && (
                <div className="h-1.5 rounded bg-paper-sunk">
                  <div className={`h-1.5 rounded ${pct >= 100 ? "bg-signal-neg" : pct >= 80 ? "bg-signal-warn" : "bg-brand"}`}
                       style={{ width: `${u.limit === 0 ? 100 : pct}%` }} />
                </div>
              )}
            </li>
          );
        })}
      </ul>
      <p className="mt-3 text-xs text-ink-mute">{t("social.usageNote")}</p>
    </Card>
  );
}

function AddSocialSource({ projects }: { projects: MonitoringProject[] }) {
  const { t } = useI18n();
  const qc = useQueryClient();
  const [f, setF] = useState({ project_id: "", kind: "facebook_group" as SocialKind, value: "", name: "", match_mode: "terms" as "terms" | "brand" });
  useEffect(() => { if (!f.project_id && projects[0]) setF((x) => ({ ...x, project_id: projects[0].id })); }, [projects, f.project_id]);
  const input = KINDS.find((k) => k.kind === f.kind)!.input;
  const add = useMutation({
    mutationFn: () => api.post<SocialSource>("/social-sources", {
      project_id: f.project_id, kind: f.kind, name: f.name, match_mode: f.match_mode,
      ...(input === "url" ? { url: f.value } : { query: f.value }),
    }),
    onSuccess: () => { setF({ ...f, value: "", name: "" }); qc.invalidateQueries({ queryKey: ["social"] }); },
  });
  if (projects.length === 0) return <Card title={t("social.add")}><p className="text-sm text-ink-mute">{t("social.needProject")}</p></Card>;
  return (
    <Card title={t("social.add")}>
      <form className="space-y-3" onSubmit={(e) => { e.preventDefault(); add.mutate(); }}>
        <div className="grid gap-3 md:grid-cols-3">
          <label className="block"><span className="label">{t("social.project")}</span>
            <select className="field" value={f.project_id} onChange={(e) => setF({ ...f, project_id: e.target.value })}>
              {projects.map((p) => <option key={p.id} value={p.id}>{p.name} — {p.brand_name}</option>)}
            </select>
          </label>
          <label className="block"><span className="label">{t("social.kind")}</span>
            <select className="field" value={f.kind} onChange={(e) => setF({ ...f, kind: e.target.value as SocialKind, value: "" })}>
              {KINDS.map((k) => <option key={k.kind} value={k.kind}>{t(`social.kind.${k.kind}`)}</option>)}
            </select>
          </label>
          <label className="block"><span className="label">{t(input === "url" ? "social.url" : "social.query")}</span>
            <input className="field" required value={f.value} dir="ltr" placeholder={t(`social.placeholder.${f.kind}`)}
                   onChange={(e) => setF({ ...f, value: e.target.value })} />
          </label>
        </div>
        <fieldset className="space-y-1 text-sm">
          <legend className="label">{t("social.matchMode")}</legend>
          {(["terms", "brand"] as const).map((m) => (
            <label key={m} className="flex items-start gap-2">
              <input type="radio" name="match_mode" className="mt-1" checked={f.match_mode === m} onChange={() => setF({ ...f, match_mode: m })} />
              <span>{t(`social.match.${m}`)}</span>
            </label>
          ))}
        </fieldset>
        <div className="flex flex-wrap items-end gap-3">
          <label className="block"><span className="label">{t("social.name")}</span>
            <input className="field !w-64" value={f.name} onChange={(e) => setF({ ...f, name: e.target.value })} />
          </label>
          <Button type="submit" disabled={add.isPending || !f.value.trim()}>{t("common.add")}</Button>
        </div>
        <p className="text-xs text-ink-mute">{t(`social.hint.${f.kind}`)}</p>
        <ErrorBanner error={add.error} />
      </form>
    </Card>
  );
}

function SyncHistory({ id }: { id: string }) {
  const { t, fmtDate } = useI18n();
  const q = useQuery({ queryKey: ["social", "syncs", id], queryFn: () => api.get<SourceSyncInfo[]>(`/social-sources/${id}/syncs`) });
  if (!q.data) return q.error ? <ErrorBanner error={q.error} /> : <Spinner />;
  if (q.data.length === 0) return <p className="text-sm text-ink-mute">{t("social.noSync")}</p>;
  return (
    <div className="-mx-5 overflow-x-auto">
      <table className="w-full text-xs">
        <thead className="text-ink-mute">
          <tr className="border-b border-line">
            {["date", "stage", "status", "fetched", "new", "known", "billed", "cost"].map((h) => (
              <th key={h} className="px-5 py-1.5 text-start font-medium">{t(`social.col.${h}` as MessageKey)}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {q.data.map((s) => (
            <tr key={s.id} className="border-b border-line align-top last:border-0">
              <td className="px-5 py-1.5">{fmtDate(s.created_at)}<br /><span className="text-ink-mute">{t(`social.trigger.${s.trigger}`)}</span></td>
              <td className="px-5 py-1.5">{t(`social.stage.${s.stage}`)}</td>
              <td className="px-5 py-1.5">
                <Badge tone={SYNC_TONE[s.status]}>{t(`social.status.${s.status}`)}</Badge>
                {s.error && <p className="mt-1 max-w-xs text-ink-soft" dir="ltr">{s.error}</p>}
              </td>
              <td className="px-5 py-1.5 tabular-nums">
                {s.items_fetched}{s.items_unreadable > 0 && <span className="text-signal-warn"> ({t("social.unreadable", { n: s.items_unreadable })})</span>}
              </td>
              <td className="px-5 py-1.5 tabular-nums">{s.mentions_new}</td>
              <td className="px-5 py-1.5 tabular-nums">{s.mentions_known}</td>
              <td className="px-5 py-1.5 tabular-nums">{s.provider === "youtube" ? t("social.units", { n: s.api_units }) : s.results_billed}</td>
              <td className="px-5 py-1.5 tabular-nums" dir="ltr">{s.provider_cost_usd === null ? "—" : `$${s.provider_cost_usd.toFixed(3)}`}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function SourceRow({ s, projectName, canWrite }: { s: SocialSource; projectName: string; canWrite: boolean }) {
  const { t, fmtDate } = useI18n();
  const qc = useQueryClient();
  const [open, setOpen] = useState(false);
  const refresh = () => { qc.invalidateQueries({ queryKey: ["social"] }); };
  const sync = useMutation({ mutationFn: () => api.post(`/social-sources/${s.id}/sync`), onSuccess: () => { setOpen(true); setTimeout(refresh, 4000); refresh(); } });
  const toggle = useMutation({ mutationFn: () => api.patch(`/social-sources/${s.id}`, { enabled: !s.enabled }), onSuccess: refresh });
  const del = useMutation({ mutationFn: () => api.del(`/social-sources/${s.id}`), onSuccess: refresh });
  const label = s.name || (s.query ? s.query : s.url.replace(/^https:\/\/(www\.)?/, ""));
  return (
    <li className="py-4">
      <div className="flex flex-wrap items-center gap-2 text-sm">
        <Badge>{t(`social.kind.${s.kind}`)}</Badge>
        <a href={s.url} target="_blank" rel="noopener noreferrer nofollow" className="font-medium hover:underline" dir="auto">{label}</a>
        <Badge tone={COVERAGE_TONE[s.coverage_status]}>{t(`coverage.${s.coverage_status}`)}</Badge>
        {!s.enabled && <Badge tone="neutral">{t("social.disabled")}</Badge>}
        <span className="ms-auto flex flex-wrap gap-3 text-xs">
          {canWrite && <button className="text-brand hover:underline disabled:text-ink-mute" disabled={!s.enabled || sync.isPending} onClick={() => sync.mutate()}>{t("social.syncNow")}</button>}
          <button className="text-brand hover:underline" onClick={() => setOpen(!open)}>{t(open ? "monitoring.hide" : "social.history")}</button>
          {canWrite && <button className="text-brand hover:underline" onClick={() => toggle.mutate()}>{t(s.enabled ? "social.disable" : "social.enable")}</button>}
          {canWrite && <button className="text-signal-neg hover:underline" onClick={() => { if (confirm(t("social.deleteConfirm"))) del.mutate(); }}>{t("common.delete")}</button>}
        </span>
      </div>
      <p className="mt-1 text-xs text-ink-mute">
        {projectName} · {t(`social.match.short.${s.match_mode}`)} · {t("social.mentionsCount", { n: s.mentions_count })}
        {" · "}{s.last_synced_at ? t("social.lastSync", { date: fmtDate(s.last_synced_at) }) : t("social.neverSynced")}
        {s.enabled && s.next_sync_at && <> · {t("social.nextSync", { date: fmtDate(s.next_sync_at) })}</>}
        {s.pending_comment_posts > 0 && <> · {t("social.pendingComments", { n: s.pending_comment_posts })}</>}
      </p>
      {s.last_error && <p className="mt-1 text-xs text-signal-warn" dir="ltr">{s.last_error}</p>}
      <ErrorBanner error={sync.error ?? toggle.error ?? del.error} />
      {open && <div className="mt-3"><SyncHistory id={s.id} /></div>}
    </li>
  );
}

export default function SocialSourcesPage() {
  const { t } = useI18n();
  const canWrite = useCan("sources:write");
  const [project, setProject] = useState("");
  const projects = useQuery({ queryKey: ["projects"], queryFn: () => api.get<MonitoringProject[]>("/monitoring-projects") });
  const q = useQuery({
    queryKey: ["social", project],
    queryFn: () => api.get<SocialOverview>(`/social-sources${project ? `?project_id=${project}` : ""}`),
    refetchInterval: (query) => (query.state.data?.sources.some((s) => s.last_sync && ["queued", "running"].includes(s.last_sync.status)) ? 5000 : false),
  });
  const names = Object.fromEntries((projects.data ?? []).map((p) => [p.id, p.name]));
  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-xl font-semibold">{t("social.title")}</h1>
        <p className="mt-1 max-w-3xl text-sm text-ink-soft">{t("social.subtitle")}</p>
      </div>
      <ErrorBanner error={q.error ?? projects.error} />
      {!q.data ? <Spinner /> : (
        <>
          <div className="grid gap-6 lg:grid-cols-2">
            <Providers o={q.data} />
            <Usage o={q.data} />
          </div>
          {canWrite && <AddSocialSource projects={projects.data ?? []} />}
          <Card title={t("social.list")} action={
            <select className="field !w-56" value={project} aria-label={t("social.project")} onChange={(e) => setProject(e.target.value)}>
              <option value="">{t("social.allProjects")}</option>
              {(projects.data ?? []).map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
            </select>}>
            {q.data.sources.length === 0 ? <p className="text-sm text-ink-mute">{t("social.empty")}</p> : (
              <ul className="-my-4 divide-y divide-line">
                {q.data.sources.map((s) => <SourceRow key={s.id} s={s} projectName={names[s.project_id] ?? ""} canWrite={canWrite} />)}
              </ul>
            )}
          </Card>
          <p className="text-xs text-ink-mute">{t("social.limits")}</p>
        </>
      )}
    </div>
  );
}
