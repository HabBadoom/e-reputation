"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import Link from "next/link";
import { useState } from "react";
import { useI18n } from "@/components/providers";
import { Badge, Button, Card, ErrorBanner, Field, Spinner } from "@/components/ui";
import {
  api, type Brand, type MonitoringProject, type MonitoringRun, type Page, type QueryGroup,
} from "@/lib/api";
import { FREQUENCIES, frequencyLabel } from "@/lib/format";
import { hasMessage } from "@/lib/i18n";
import { useCan } from "@/lib/session";

const LANGS = [["fr", "Français"], ["ar", "العربية"], ["ar-DZ", "Darija"], ["en", "English"], ["kab", "Taqbaylit"]] as const;
const RUN_TONE = { queued: "neutral", running: "brand", completed: "brand", skipped: "warn", failed: "neg" } as const;

function RunBadge({ run }: { run: MonitoringRun }) {
  const { t } = useI18n();
  return <Badge tone={RUN_TONE[run.status]}>{t(`monitoring.run.${run.status}`)}</Badge>;
}

function runReason(run: MonitoringRun, t: ReturnType<typeof useI18n>["t"]): string | null {
  const reason = run.summary?.reason;
  if (reason === "no_active_connectors") return t("monitoring.noConnectors");
  const key = `monitoring.reason.${String(reason)}`;
  return hasMessage(key) ? t(key) : run.error;
}

function NewProject({ brands, onDone }: { brands: Brand[]; onDone: () => void }) {
  const { t } = useI18n();
  const qc = useQueryClient();
  const [f, setF] = useState({
    name: "", brand_id: brands[0]?.id ?? "", frequency_minutes: 1440, include_competitors: true,
    languages: ["fr", "ar", "ar-DZ"] as string[],
  });
  const m = useMutation({
    mutationFn: () => api.post("/monitoring-projects", f),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["projects"] }); onDone(); },
  });
  const toggleLang = (l: string) =>
    setF({ ...f, languages: f.languages.includes(l) ? f.languages.filter((x) => x !== l) : [...f.languages, l] });
  return (
    <Card title={t("monitoring.new")}>
      <form className="grid gap-4 md:grid-cols-2" onSubmit={(e) => { e.preventDefault(); m.mutate(); }}>
        <div className="md:col-span-2"><ErrorBanner error={m.error} /></div>
        <Field label={t("catalog.name")} required value={f.name} onChange={(e) => setF({ ...f, name: e.target.value })} />
        <label><span className="label">{t("monitoring.brand")}</span>
          <select className="field" value={f.brand_id} onChange={(e) => setF({ ...f, brand_id: e.target.value })}>
            {brands.map((b) => <option key={b.id} value={b.id}>{b.name}</option>)}
          </select>
        </label>
        <label><span className="label">{t("monitoring.frequency")}</span>
          <select className="field" value={f.frequency_minutes}
                  onChange={(e) => setF({ ...f, frequency_minutes: Number(e.target.value) })}>
            {FREQUENCIES.map((m) => <option key={m} value={m}>{frequencyLabel(m, t)}</option>)}
          </select>
        </label>
        <fieldset>
          <legend className="label">{t("monitoring.languages")}</legend>
          <div className="flex flex-wrap gap-2">
            {LANGS.map(([code, label]) => (
              <label key={code} className="flex items-center gap-1.5 rounded border border-line px-2 py-1 text-sm">
                <input type="checkbox" checked={f.languages.includes(code)} onChange={() => toggleLang(code)} />
                {label}
              </label>
            ))}
          </div>
        </fieldset>
        <label className="flex items-center gap-2 text-sm md:col-span-2">
          <input type="checkbox" checked={f.include_competitors}
                 onChange={(e) => setF({ ...f, include_competitors: e.target.checked })} />
          {t("monitoring.includeCompetitors")}
        </label>
        <div className="flex gap-2 md:col-span-2">
          <Button type="submit" disabled={m.isPending || !f.brand_id || f.languages.length === 0}>{t("common.save")}</Button>
          <Button type="button" variant="ghost" onClick={onDone}>{t("common.cancel")}</Button>
        </div>
      </form>
    </Card>
  );
}

function ProjectDetails({ project }: { project: MonitoringProject }) {
  const { t, fmtDate } = useI18n();
  const plan = useQuery({
    queryKey: ["project", project.id, "plan"],
    queryFn: () => api.get<{ groups: QueryGroup[] }>(`/monitoring-projects/${project.id}/query-plan`),
  });
  const runs = useQuery({
    queryKey: ["project", project.id, "runs"],
    queryFn: () => api.get<MonitoringRun[]>(`/monitoring-projects/${project.id}/runs?limit=10`),
    refetchInterval: (q) => q.state.data?.some((r) => r.status === "queued" || r.status === "running") ? 3000 : false,
  });
  return (
    <div className="mt-4 grid gap-6 border-t border-line pt-4 lg:grid-cols-2">
      <div>
        <h3 className="mb-2 text-xs font-medium uppercase tracking-wide text-ink-soft">{t("monitoring.queryPlan")}</h3>
        {plan.isLoading ? <Spinner /> : (
          <ul className="space-y-2">
            {plan.data?.groups.map((g) => (
              <li key={`${g.entity_type}:${g.entity_name}`} className="rounded bg-paper-sunk p-2 text-sm">
                <p className="mb-1 font-medium">{g.entity_name} <span className="text-xs font-normal text-ink-mute">· {g.entity_type}</span></p>
                <p className="flex flex-wrap gap-1" dir="auto">{g.terms.map((x) => <Badge key={x}>{x}</Badge>)}</p>
                {g.exclude.length > 0 && (
                  <p className="mt-1 flex flex-wrap items-center gap-1 text-xs text-ink-mute" dir="auto">
                    {t("monitoring.excluding")} {g.exclude.map((x) => <Badge key={x} tone="neg">{x}</Badge>)}
                  </p>
                )}
              </li>
            ))}
          </ul>
        )}
      </div>
      <div>
        <h3 className="mb-2 text-xs font-medium uppercase tracking-wide text-ink-soft">{t("monitoring.runs")}</h3>
        {runs.data?.length === 0 && <p className="text-sm text-ink-mute">{t("common.none")}</p>}
        <ul className="divide-y divide-line text-sm">
          {runs.data?.map((r) => (
            <li key={r.id} className="py-2">
              <div className="flex items-center justify-between gap-2">
                <span className="flex items-center gap-2"><RunBadge run={r} />
                  <span className="text-xs text-ink-mute">{t(`monitoring.trigger.${r.trigger}`)}</span></span>
                <span className="text-xs text-ink-mute">{fmtDate(r.finished_at ?? r.queued_at)}</span>
              </div>
              {r.status === "completed" && (
                <p className="mt-1 text-xs text-ink-soft">
                  {t("run.collected", { n: Number(r.summary.mentions_collected ?? 0) })}
                  {" · "}{t("run.known", { n: Number(r.summary.already_known ?? 0) })}
                  {" · "}{t("run.excluded", { n: Number(r.summary.excluded ?? 0) })}
                </p>
              )}
              {runReason(r, t) && <p className="mt-1 text-xs text-ink-soft">{runReason(r, t)}</p>}
            </li>
          ))}
        </ul>
      </div>
    </div>
  );
}

function ProjectRow({ p, canWrite }: { p: MonitoringProject; canWrite: boolean }) {
  const { t, fmtDate } = useI18n();
  const qc = useQueryClient();
  const [open, setOpen] = useState(false);
  const refresh = () => {
    qc.invalidateQueries({ queryKey: ["projects"] });
    qc.invalidateQueries({ queryKey: ["project", p.id] });
  };
  const run = useMutation({ mutationFn: () => api.post(`/monitoring-projects/${p.id}/runs`), onSuccess: () => { setOpen(true); refresh(); } });
  const toggle = useMutation({
    mutationFn: () => api.patch(`/monitoring-projects/${p.id}`, { status: p.status === "active" ? "paused" : "active" }),
    onSuccess: refresh,
  });
  const del = useMutation({ mutationFn: () => api.del(`/monitoring-projects/${p.id}`), onSuccess: refresh });

  return (
    <li className="p-5">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <p className="flex items-center gap-2 font-medium">
            {p.name}
            <Badge tone={p.status === "active" ? "brand" : "neutral"}>{t(`monitoring.status.${p.status}`)}</Badge>
          </p>
          <p className="mt-1 text-sm text-ink-soft">
            <Link className="hover:underline" href={`/brands/${p.brand_id}`}>{p.brand_name}</Link>
            {" · "}{frequencyLabel(p.frequency_minutes, t)}{" · "}{p.languages.join(", ")}
          </p>
          <p className="mt-1 flex flex-wrap items-center gap-2 text-xs text-ink-mute">
            {t("monitoring.lastRun")}: {p.last_run ? <><RunBadge run={p.last_run} />{p.last_run.finished_at && fmtDate(p.last_run.finished_at)}</> : t("monitoring.never")}
            {p.next_run_at && <span>· {t("monitoring.nextRun")}: {fmtDate(p.next_run_at)}</span>}
          </p>
        </div>
        <div className="flex flex-wrap gap-2">
          <Button variant="ghost" onClick={() => setOpen(!open)}>{t(open ? "monitoring.hide" : "monitoring.details")}</Button>
          {canWrite && <>
            <Button onClick={() => run.mutate()} disabled={run.isPending}>{t("monitoring.runNow")}</Button>
            <Button variant="ghost" onClick={() => toggle.mutate()} disabled={toggle.isPending}>
              {t(p.status === "active" ? "monitoring.pause" : "monitoring.resume")}
            </Button>
            <Button variant="danger" onClick={() => confirm(t("common.confirmDelete")) && del.mutate()}>{t("common.delete")}</Button>
          </>}
        </div>
      </div>
      <div className="mt-2"><ErrorBanner error={run.error ?? toggle.error ?? del.error} /></div>
      {open && <ProjectDetails project={p} />}
    </li>
  );
}

export default function MonitoringPage() {
  const { t } = useI18n();
  const canWrite = useCan("monitoring:write");
  const [creating, setCreating] = useState(false);
  const projects = useQuery({ queryKey: ["projects"], queryFn: () => api.get<MonitoringProject[]>("/monitoring-projects") });
  const brands = useQuery({ queryKey: ["brands", "all"], queryFn: () => api.get<Page<Brand>>("/brands?limit=100") });

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h1 className="text-2xl font-semibold">{t("monitoring.title")}</h1>
        {canWrite && !creating && (
          brands.data?.total === 0
            ? <Link href="/brands" className="text-sm text-brand hover:underline">{t("monitoring.needBrand")}</Link>
            : <Button onClick={() => setCreating(true)} disabled={!brands.data}>{t("monitoring.new")}</Button>
        )}
      </div>
      {creating && brands.data && <NewProject brands={brands.data.items} onDone={() => setCreating(false)} />}
      {projects.isLoading ? <Spinner /> : projects.error ? <ErrorBanner error={projects.error} /> : (
        <ul className="card divide-y divide-line">
          {projects.data!.length === 0 && <li className="p-5 text-sm text-ink-mute">{t("common.none")}</li>}
          {projects.data!.map((p) => <ProjectRow key={p.id} p={p} canWrite={canWrite} />)}
        </ul>
      )}
    </div>
  );
}
