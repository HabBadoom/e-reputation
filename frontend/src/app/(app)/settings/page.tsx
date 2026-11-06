"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import type { MessageKey } from "@/lib/i18n";
import { useI18n } from "@/components/providers";
import { Button, Card, ErrorBanner, Field, Spinner } from "@/components/ui";
import { api, type Organization, type PlanUsage } from "@/lib/api";
import { frequencyLabel } from "@/lib/format";
import { LOCALES } from "@/lib/i18n";
import { useCan } from "@/lib/session";

const USAGE_KEYS = ["max_brands", "max_monitoring_projects", "max_members"] as const;

function PlanCard() {
  const { t, fmtDate } = useI18n();
  const q = useQuery({ queryKey: ["plan"], queryFn: () => api.get<PlanUsage>("/organization/plan") });
  if (!q.data) return <ErrorBanner error={q.error} />;
  const p = q.data;
  const freq = p.limits.min_frequency_minutes;
  return (
    <Card title={t("plan.title")}>
      <p className="mb-4 flex flex-wrap items-center gap-2 text-sm">
        <span className="font-medium">{p.plan_name}</span>
        {p.trial_ends_at && p.status === "trialing" && (
          <span className="text-ink-mute">· {t("plan.trialEnds", { date: fmtDate(p.trial_ends_at) })}</span>
        )}
      </p>
      <ul className="space-y-3">
        {USAGE_KEYS.map((k) => {
          const limit = p.limits[k];
          const used = p.usage[k] ?? 0;
          const pct = limit ? Math.min(100, Math.round((used / limit) * 100)) : 0;
          return (
            <li key={k}>
              <div className="mb-1 flex justify-between text-sm">
                <span>{t(`plan.${k}` as MessageKey)}</span>
                <span className="tabular-nums text-ink-soft">{used} / {limit ?? t("plan.unlimited")}</span>
              </div>
              {limit !== null && (
                <div className="h-1.5 rounded bg-paper-sunk">
                  <div className={`h-1.5 rounded ${pct >= 100 ? "bg-signal-neg" : "bg-brand"}`} style={{ width: `${pct}%` }} />
                </div>
              )}
            </li>
          );
        })}
        <li className="flex justify-between text-sm">
          <span>{t("plan.min_frequency_minutes")}</span>
          <span className="text-ink-soft">{freq ? frequencyLabel(freq, t) : t("plan.unlimited")}</span>
        </li>
      </ul>
      <p className="mt-4 text-xs text-ink-mute">{t("plan.contact")}</p>
    </Card>
  );
}

function CorpusCard({ org }: { org: Organization }) {
  const { t } = useI18n();
  const qc = useQueryClient();
  const canEdit = useCan("tenant:update");
  const m = useMutation({
    mutationFn: (on: boolean) => api.patch<Organization>("/organization", { nlp_corpus_opt_in: on }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["org"] }),
  });
  return (
    <Card title={t("settings.corpusTitle")}>
      <p className="mb-3 text-sm text-ink-soft">{t("settings.corpusHelp")}</p>
      <label className="flex items-center gap-2 text-sm">
        <input type="checkbox" checked={org.nlp_corpus_opt_in} disabled={!canEdit || m.isPending}
               onChange={(e) => (e.target.checked || confirm(t("common.confirmDelete"))) && m.mutate(e.target.checked)} />
        {t("settings.corpusOn")}
      </label>
      <div className="mt-2"><ErrorBanner error={m.error} /></div>
    </Card>
  );
}

export default function SettingsPage() {
  const { t } = useI18n();
  const qc = useQueryClient();
  const canEdit = useCan("tenant:update");
  const org = useQuery({ queryKey: ["org"], queryFn: () => api.get<Organization>("/organization") });
  const [f, setF] = useState({ name: "", default_locale: "fr" });
  useEffect(() => { if (org.data) setF({ name: org.data.name, default_locale: org.data.default_locale }); }, [org.data]);
  const save = useMutation({
    mutationFn: () => api.patch<Organization>("/organization", f),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["org"] }); qc.invalidateQueries({ queryKey: ["me"] }); },
  });
  if (org.isLoading) return <Spinner />;
  return (
    <div className="max-w-xl space-y-6">
      <h1 className="text-2xl font-semibold">{t("settings.title")}</h1>
      <Card>
        <form className="space-y-4" onSubmit={(e) => { e.preventDefault(); save.mutate(); }}>
          <ErrorBanner error={org.error ?? save.error} />
          <fieldset disabled={!canEdit} className="space-y-4">
            <Field label={t("auth.orgName")} required value={f.name} onChange={(e) => setF({ ...f, name: e.target.value })} />
            <label className="block"><span className="label">{t("settings.language")}</span>
              <select className="field" value={f.default_locale} onChange={(e) => setF({ ...f, default_locale: e.target.value })}>
                {LOCALES.map((l) => <option key={l} value={l}>{t(`lang.${l}`)}</option>)}
              </select>
            </label>
          </fieldset>
          {canEdit && (
            <div className="flex items-center gap-3">
              <Button type="submit" disabled={save.isPending}>{t("common.save")}</Button>
              {save.isSuccess && <span className="text-sm text-signal-pos">{t("common.saved")}</span>}
            </div>
          )}
          <p className="font-mono text-xs text-ink-mute" dir="ltr">{org.data?.slug}</p>
        </form>
      </Card>
      <PlanCard />
      {org.data && <CorpusCard org={org.data} />}
    </div>
  );
}
