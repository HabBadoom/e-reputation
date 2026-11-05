"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useParams, useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { useI18n } from "@/components/providers";
import { Badge, Button, Card, ErrorBanner, Field, Spinner } from "@/components/ui";
import { api, type Brand } from "@/lib/api";
import { useCan } from "@/lib/session";

const SCRIPTS = ["latin", "arabic", "arabizi", "tifinagh", "other"] as const;
const KINDS = ["alias", "misspelling", "transliteration", "product", "abbreviation"] as const;
const STATUSES = ["active", "paused", "archived"] as const;

export default function BrandDetail() {
  const { t } = useI18n();
  const { id } = useParams<{ id: string }>();
  const router = useRouter();
  const qc = useQueryClient();
  const canWrite = useCan("brands:write");
  const canDelete = useCan("brands:delete");
  const key = ["brand", id];
  const q = useQuery({ queryKey: key, queryFn: () => api.get<Brand>(`/brands/${id}`) });
  const [form, setForm] = useState({ name: "", website: "", description: "", status: "active" as Brand["status"] });
  const [alias, setAlias] = useState({ alias: "", script: "", kind: "alias" });

  useEffect(() => {
    if (q.data) setForm({ name: q.data.name, website: q.data.website ?? "", description: q.data.description, status: q.data.status });
  }, [q.data]);

  const refresh = () => { qc.invalidateQueries({ queryKey: key }); qc.invalidateQueries({ queryKey: ["brands"] }); };
  const save = useMutation({
    mutationFn: () => api.patch<Brand>(`/brands/${id}`, { ...form, website: form.website || null }),
    onSuccess: refresh,
  });
  const addAlias = useMutation({
    mutationFn: () => api.post(`/brands/${id}/aliases`, { alias: alias.alias, kind: alias.kind, script: alias.script || null }),
    onSuccess: () => { setAlias({ alias: "", script: "", kind: "alias" }); refresh(); },
  });
  const delAlias = useMutation({ mutationFn: (aid: string) => api.del(`/brands/${id}/aliases/${aid}`), onSuccess: refresh });
  const del = useMutation({
    mutationFn: () => api.del(`/brands/${id}`),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["brands"] }); router.replace("/brands"); },
  });

  if (q.isLoading) return <Spinner />;
  if (q.error || !q.data) return <ErrorBanner error={q.error} />;
  const b = q.data;

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h1 className="text-2xl font-semibold">{b.name}</h1>
        {canDelete && (
          <Button variant="danger" onClick={() => confirm(t("common.confirmDelete")) && del.mutate()}>
            {t("common.delete")}
          </Button>
        )}
      </div>
      <ErrorBanner error={del.error} />

      <Card title={t("brands.aliases")}>
        <p className="mb-4 text-xs text-ink-mute">{t("brands.aliasesHint")}</p>
        <ul className="mb-4 flex flex-wrap gap-2">
          {b.aliases.length === 0 && <li className="text-sm text-ink-mute">{t("common.none")}</li>}
          {b.aliases.map((a) => (
            <li key={a.id} className="flex items-center gap-2 rounded border border-line px-2 py-1 text-sm">
              <span dir="auto">{a.alias}</span>
              <Badge>{a.script}</Badge>
              {a.kind !== "alias" && <Badge tone="warn">{a.kind}</Badge>}
              {canWrite && (
                <button aria-label={t("common.delete")} className="text-ink-mute hover:text-signal-neg"
                        onClick={() => delAlias.mutate(a.id)}>×</button>
              )}
            </li>
          ))}
        </ul>
        {canWrite && (
          <form className="flex flex-wrap items-end gap-2" onSubmit={(e) => { e.preventDefault(); addAlias.mutate(); }}>
            <div className="min-w-48 flex-1">
              <Field label={t("brands.aliases")} dir="auto" required value={alias.alias}
                     placeholder={t("brands.aliasPlaceholder")} onChange={(e) => setAlias({ ...alias, alias: e.target.value })} />
            </div>
            <label><span className="label">{t("brands.script")}</span>
              <select className="field" value={alias.script} onChange={(e) => setAlias({ ...alias, script: e.target.value })}>
                <option value="">auto</option>
                {SCRIPTS.map((s) => <option key={s} value={s}>{s}</option>)}
              </select>
            </label>
            <label><span className="label">{t("brands.kind")}</span>
              <select className="field" value={alias.kind} onChange={(e) => setAlias({ ...alias, kind: e.target.value })}>
                {KINDS.map((s) => <option key={s} value={s}>{s}</option>)}
              </select>
            </label>
            <Button type="submit" disabled={addAlias.isPending}>{t("common.add")}</Button>
          </form>
        )}
        <div className="mt-3"><ErrorBanner error={addAlias.error ?? delAlias.error} /></div>
      </Card>

      <Card title={t("nav.settings")}>
        <form className="grid gap-4 md:grid-cols-2" onSubmit={(e) => { e.preventDefault(); save.mutate(); }}>
          <fieldset disabled={!canWrite} className="contents">
            <Field label={t("brands.name")} required value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} />
            <Field label={t("brands.website")} type="url" dir="ltr" value={form.website} onChange={(e) => setForm({ ...form, website: e.target.value })} />
            <label className="block md:col-span-2"><span className="label">{t("brands.description")}</span>
              <textarea className="field min-h-24" dir="auto" value={form.description}
                        onChange={(e) => setForm({ ...form, description: e.target.value })} />
            </label>
            <label><span className="label">{t("brands.status")}</span>
              <select className="field" value={form.status} onChange={(e) => setForm({ ...form, status: e.target.value as Brand["status"] })}>
                {STATUSES.map((s) => <option key={s} value={s}>{t(`brands.status.${s}`)}</option>)}
              </select>
            </label>
          </fieldset>
          <div className="flex items-center gap-3 md:col-span-2">
            {canWrite && <Button type="submit" disabled={save.isPending}>{t("common.save")}</Button>}
            {save.isSuccess && <span className="text-sm text-signal-pos">{t("common.saved")}</span>}
            <ErrorBanner error={save.error} />
          </div>
        </form>
      </Card>
    </div>
  );
}
