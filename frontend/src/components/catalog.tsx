"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { api, type Competitor, type Keyword, type Product } from "@/lib/api";
import { splitList } from "@/lib/format";
import { useI18n } from "./providers";
import { Badge, Button, Card, ErrorBanner, Field } from "./ui";

type Named = Product | Competitor;

/** Products and competitors share the same shape in the UI: a name plus other spellings. */
export function NamedListCard({ brandId, kind, canWrite }: {
  brandId: string; kind: "products" | "competitors"; canWrite: boolean;
}) {
  const { t } = useI18n();
  const qc = useQueryClient();
  const key = ["brand", brandId, kind];
  const list = useQuery({ queryKey: key, queryFn: () => api.get<Named[]>(`/brands/${brandId}/${kind}`) });
  const [name, setName] = useState("");
  const [aliases, setAliases] = useState("");
  const add = useMutation({
    mutationFn: () => api.post(`/brands/${brandId}/${kind}`, { name, aliases: splitList(aliases) }),
    onSuccess: () => { setName(""); setAliases(""); qc.invalidateQueries({ queryKey: key }); },
  });
  const del = useMutation({
    mutationFn: (id: string) => api.del(`/brands/${brandId}/${kind}/${id}`),
    onSuccess: () => qc.invalidateQueries({ queryKey: key }),
  });

  return (
    <Card title={t(kind === "products" ? "catalog.products" : "catalog.competitors")}>
      <ul className="mb-4 divide-y divide-line">
        {list.data?.length === 0 && <li className="py-2 text-sm text-ink-mute">{t("common.none")}</li>}
        {list.data?.map((item) => (
          <li key={item.id} className="flex items-start justify-between gap-3 py-2">
            <div className="min-w-0">
              <p className="text-sm font-medium">{item.name}</p>
              {item.aliases.length > 0 && (
                <p className="mt-0.5 flex flex-wrap gap-1" dir="auto">
                  {item.aliases.map((a) => <Badge key={a}>{a}</Badge>)}
                </p>
              )}
            </div>
            {canWrite && (
              <button aria-label={t("common.delete")} className="text-ink-mute hover:text-signal-neg"
                      onClick={() => del.mutate(item.id)}>×</button>
            )}
          </li>
        ))}
      </ul>
      {canWrite && (
        <form className="grid gap-2 sm:grid-cols-[1fr_1.4fr_auto] sm:items-end"
              onSubmit={(e) => { e.preventDefault(); add.mutate(); }}>
          <Field label={t("catalog.name")} required dir="auto" value={name} onChange={(e) => setName(e.target.value)} />
          <Field label={t("catalog.aliasesInline")} dir="auto" value={aliases} onChange={(e) => setAliases(e.target.value)} />
          <Button type="submit" disabled={add.isPending}>{t("common.add")}</Button>
        </form>
      )}
      <div className="mt-2"><ErrorBanner error={add.error ?? del.error} /></div>
    </Card>
  );
}

export function KeywordsCard({ brandId, canWrite }: { brandId: string; canWrite: boolean }) {
  const { t } = useI18n();
  const qc = useQueryClient();
  const key = ["brand", brandId, "keywords"];
  const list = useQuery({ queryKey: key, queryFn: () => api.get<Keyword[]>(`/brands/${brandId}/keywords`) });
  const [expression, setExpression] = useState("");
  const [kind, setKind] = useState<"include" | "exclude">("include");
  const add = useMutation({
    mutationFn: () => api.post(`/brands/${brandId}/keywords`, { expression, kind }),
    onSuccess: () => { setExpression(""); qc.invalidateQueries({ queryKey: key }); },
  });
  const del = useMutation({
    mutationFn: (id: string) => api.del(`/brands/${brandId}/keywords/${id}`),
    onSuccess: () => qc.invalidateQueries({ queryKey: key }),
  });
  const group = (k: Keyword["kind"]) => list.data?.filter((x) => x.kind === k) ?? [];

  return (
    <Card title={t("catalog.keywords")}>
      <p className="mb-4 text-xs text-ink-mute">{t("catalog.keywordsHelp")}</p>
      {(["include", "exclude"] as const).map((k) => (
        <div key={k} className="mb-3">
          <p className="mb-1 text-xs font-medium uppercase tracking-wide text-ink-soft">
            {t(k === "include" ? "catalog.include" : "catalog.exclude")}
          </p>
          <ul className="flex flex-wrap gap-2">
            {group(k).length === 0 && <li className="text-sm text-ink-mute">—</li>}
            {group(k).map((kw) => (
              <li key={kw.id} className={`flex items-center gap-2 rounded border px-2 py-1 text-sm ${
                k === "exclude" ? "border-signal-neg/30 text-signal-neg" : "border-line"}`}>
                <span dir="auto">{kw.expression}</span>
                {canWrite && <button aria-label={t("common.delete")} className="opacity-60 hover:opacity-100"
                                     onClick={() => del.mutate(kw.id)}>×</button>}
              </li>
            ))}
          </ul>
        </div>
      ))}
      {canWrite && (
        <form className="mt-4 flex flex-wrap items-end gap-2" onSubmit={(e) => { e.preventDefault(); add.mutate(); }}>
          <div className="min-w-48 flex-1">
            <Field label={t("catalog.keywords")} required dir="auto" value={expression}
                   onChange={(e) => setExpression(e.target.value)} />
          </div>
          <select className="field w-auto" value={kind} aria-label={t("brands.kind")}
                  onChange={(e) => setKind(e.target.value as "include" | "exclude")}>
            <option value="include">{t("catalog.include")}</option>
            <option value="exclude">{t("catalog.exclude")}</option>
          </select>
          <Button type="submit" disabled={add.isPending}>{t("common.add")}</Button>
        </form>
      )}
      <div className="mt-2"><ErrorBanner error={add.error ?? del.error} /></div>
    </Card>
  );
}
