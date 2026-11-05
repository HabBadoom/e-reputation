"use client";

import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import Link from "next/link";
import { useState } from "react";
import { useI18n } from "@/components/providers";
import { Badge, Button, Card, ErrorBanner, Field, fieldErrors, Spinner } from "@/components/ui";
import { api, type Brand, type Page } from "@/lib/api";
import { useCan } from "@/lib/session";

const PAGE = 25;
const splitAliases = (s: string) => s.split(/[,،\n]/).map((x) => x.trim()).filter(Boolean);

function NewBrand({ onDone }: { onDone: () => void }) {
  const { t } = useI18n();
  const qc = useQueryClient();
  const [name, setName] = useState("");
  const [website, setWebsite] = useState("");
  const [aliases, setAliases] = useState("");
  const m = useMutation({
    mutationFn: () => api.post<Brand>("/brands", {
      name, website: website || null, aliases: splitAliases(aliases).map((alias) => ({ alias })),
    }),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["brands"] }); qc.invalidateQueries({ queryKey: ["dashboard"] }); onDone(); },
  });
  const fe = fieldErrors(m.error);
  return (
    <Card title={t("brands.new")}>
      <form className="grid gap-4 md:grid-cols-2" onSubmit={(e) => { e.preventDefault(); m.mutate(); }}>
        <div className="md:col-span-2">{!Object.keys(fe).length && <ErrorBanner error={m.error} />}</div>
        <Field label={t("brands.name")} required value={name} onChange={(e) => setName(e.target.value)} error={fe.name} />
        <Field label={t("brands.website")} type="url" dir="ltr" placeholder="https://" value={website}
               onChange={(e) => setWebsite(e.target.value)} error={fe.website} />
        <label className="block md:col-span-2">
          <span className="label">{t("brands.aliases")}</span>
          <textarea className="field min-h-20" dir="auto" placeholder={t("brands.aliasPlaceholder")}
                    value={aliases} onChange={(e) => setAliases(e.target.value)} />
          <span className="mt-1 block text-xs text-ink-mute">{t("brands.aliasesHint")}</span>
        </label>
        <div className="flex gap-2 md:col-span-2">
          <Button type="submit" disabled={m.isPending}>{t("common.save")}</Button>
          <Button type="button" variant="ghost" onClick={onDone}>{t("common.cancel")}</Button>
        </div>
      </form>
    </Card>
  );
}

export default function BrandsPage() {
  const { t } = useI18n();
  const canWrite = useCan("brands:write");
  const [creating, setCreating] = useState(false);
  const [q, setQ] = useState("");
  const [offset, setOffset] = useState(0);
  const list = useQuery({
    queryKey: ["brands", q, offset],
    queryFn: () => api.get<Page<Brand>>(`/brands?limit=${PAGE}&offset=${offset}${q ? `&q=${encodeURIComponent(q)}` : ""}`),
    placeholderData: keepPreviousData,
  });

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h1 className="text-2xl font-semibold">{t("brands.title")}</h1>
        {canWrite && !creating && <Button onClick={() => setCreating(true)}>{t("brands.new")}</Button>}
      </div>
      {creating && <NewBrand onDone={() => setCreating(false)} />}
      <input className="field max-w-sm" type="search" dir="auto" placeholder={t("common.search")}
             value={q} onChange={(e) => { setQ(e.target.value); setOffset(0); }} />
      {list.isLoading ? <Spinner /> : list.error ? <ErrorBanner error={list.error} /> : (
        <div className="card divide-y divide-line">
          {list.data!.items.length === 0 && <p className="p-5 text-sm text-ink-mute">{t("common.none")}</p>}
          {list.data!.items.map((b) => (
            <Link key={b.id} href={`/brands/${b.id}`} className="flex items-center justify-between gap-4 px-5 py-3 hover:bg-paper-sunk">
              <div className="min-w-0">
                <p className="font-medium">{b.name}</p>
                <p className="truncate text-xs text-ink-mute" dir="auto">{b.aliases.map((a) => a.alias).join(" · ")}</p>
              </div>
              <Badge tone={b.status === "active" ? "brand" : "neutral"}>{t(`brands.status.${b.status}`)}</Badge>
            </Link>
          ))}
        </div>
      )}
      {list.data && list.data.total > PAGE && (
        <div className="flex items-center gap-3 text-sm">
          <Button variant="ghost" disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - PAGE))}>
            <span className="flip-rtl">←</span>
          </Button>
          <span className="tabular-nums text-ink-soft">
            {offset + 1}–{Math.min(offset + PAGE, list.data.total)} / {list.data.total}
          </span>
          <Button variant="ghost" disabled={offset + PAGE >= list.data.total} onClick={() => setOffset(offset + PAGE)}>
            <span className="flip-rtl">→</span>
          </Button>
        </div>
      )}
    </div>
  );
}
