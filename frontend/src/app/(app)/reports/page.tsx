"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { BrandSelect, useBrands } from "@/components/intel";
import { useI18n } from "@/components/providers";
import { Badge, Button, Card, ErrorBanner, Spinner } from "@/components/ui";
import { api, type Page, type Report, type ReportFormat, type ReportKind } from "@/lib/api";
import { LOCALES, type Locale } from "@/lib/i18n";
import { useCan } from "@/lib/session";

const KINDS: ReportKind[] = ["daily", "weekly", "monthly", "crisis", "competitor", "custom"];
const FORMATS: ReportFormat[] = ["pdf", "xlsx", "csv", "html"];

function size(n: number | null) {
  if (!n) return "";
  return n > 1_000_000 ? `${(n / 1_000_000).toFixed(1)} MB` : `${Math.max(1, Math.round(n / 1000))} kB`;
}

function NewReport() {
  const { t, locale } = useI18n();
  const qc = useQueryClient();
  const [kind, setKind] = useState<ReportKind>("weekly");
  const [format, setFormat] = useState<ReportFormat>("pdf");
  const [lang, setLang] = useState<Locale>(locale);
  const [brand, setBrand] = useState("");
  const [from, setFrom] = useState("");
  const [to, setTo] = useState("");
  const create = useMutation({
    mutationFn: () => api.post<Report>("/reports", {
      kind, format, locale: lang, brand_id: brand || null,
      ...(kind === "custom" && from && to
        ? { period_start: new Date(`${from}T00:00:00`).toISOString(), period_end: new Date(`${to}T23:59:59`).toISOString() }
        : {}),
    }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["reports"] }),
  });
  return (
    <Card title={t("rep.new")}>
      <form className="space-y-4" onSubmit={(e) => { e.preventDefault(); create.mutate(); }}>
        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
          <label className="block"><span className="label">{t("rep.kind")}</span>
            <select className="field" value={kind} onChange={(e) => setKind(e.target.value as ReportKind)}>
              {KINDS.map((k) => <option key={k} value={k}>{t(`rep.kind.${k}`)}</option>)}
            </select>
          </label>
          <label className="block"><span className="label">{t("intel.brand")}</span>
            <BrandSelect value={brand} onChange={setBrand} />
          </label>
          <label className="block"><span className="label">{t("rep.format")}</span>
            <select className="field" value={format} onChange={(e) => setFormat(e.target.value as ReportFormat)}>
              {FORMATS.map((f) => <option key={f} value={f}>{t(`rep.fmt.${f}`)}</option>)}
            </select>
          </label>
          <label className="block"><span className="label">{t("rep.language")}</span>
            <select className="field" value={lang} onChange={(e) => setLang(e.target.value as Locale)}>
              {LOCALES.map((l) => <option key={l} value={l}>{t(`lang.${l}`)}</option>)}
            </select>
          </label>
        </div>
        {kind === "custom" && (
          <div className="grid gap-4 sm:grid-cols-2">
            <label className="block"><span className="label">{t("rep.from")}</span>
              <input type="date" className="field" value={from} onChange={(e) => setFrom(e.target.value)} required /></label>
            <label className="block"><span className="label">{t("rep.to")}</span>
              <input type="date" className="field" value={to} onChange={(e) => setTo(e.target.value)} required /></label>
          </div>
        )}
        <p className="text-xs text-ink-mute">{t(`rep.help.${kind}`)}</p>
        <ErrorBanner error={create.error} />
        <Button type="submit" disabled={create.isPending}>{t("rep.generate")}</Button>
      </form>
    </Card>
  );
}

export default function ReportsPage() {
  const { t, fmtDate } = useI18n();
  const qc = useQueryClient();
  const canCreate = useCan("reports:create");
  const brands = useBrands().data?.items ?? [];
  const q = useQuery({
    queryKey: ["reports"],
    queryFn: () => api.get<Page<Report>>("/reports?limit=100"),
    // Poll only while something is being generated.
    refetchInterval: (query) =>
      query.state.data?.items.some((r) => r.status === "queued" || r.status === "running") ? 3000 : false,
  });
  const del = useMutation({ mutationFn: (id: string) => api.del(`/reports/${id}`), onSuccess: () => qc.invalidateQueries({ queryKey: ["reports"] }) });
  const brandName = (id: string | null) => (id ? brands.find((b) => b.id === id)?.name ?? "—" : t("intel.allBrands"));

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-semibold">{t("rep.title")}</h1>
        <p className="mt-1 text-sm text-ink-soft">{t("rep.subtitle")}</p>
      </div>
      {canCreate && <NewReport />}
      <ErrorBanner error={q.error ?? del.error} />
      {q.isLoading ? <Spinner /> : q.data?.items.length === 0 ? (
        <p className="rounded border border-dashed border-line p-6 text-center text-sm text-ink-mute">{t("rep.none")}</p>
      ) : (
        <ul className="space-y-2">
          {q.data?.items.map((r) => (
            <li key={r.id} className="card flex flex-wrap items-center justify-between gap-3 p-4 text-sm">
              <div>
                <p className="font-semibold">{t(`rep.kind.${r.kind}`)} · {brandName(r.brand_id)}
                  <span className="ms-2 font-mono text-xs uppercase text-ink-mute">{r.format} · {r.locale}</span></p>
                <p className="mt-1 text-xs text-ink-soft">
                  {fmtDate(r.period_start)} → {fmtDate(r.period_end)} · {t("rep.requested", { d: fmtDate(r.created_at) })}
                </p>
                {r.error && <p className="mt-1 text-xs text-signal-neg">{t("rep.failed")}</p>}
              </div>
              <div className="flex items-center gap-2">
                {r.status === "ready" ? (
                  <a className="inline-flex items-center rounded bg-brand px-3.5 py-2 text-sm font-medium text-white hover:bg-brand-strong"
                     href={`/api/v1/reports/${r.id}/download`} download>
                    {t("rep.download")} <span className="ms-1 text-xs opacity-80">{size(r.size_bytes)}</span>
                  </a>
                ) : (
                  <Badge tone={r.status === "failed" ? "neg" : "warn"}>{t(`rep.st.${r.status}`)}</Badge>
                )}
                {canCreate && <Button variant="danger" onClick={() => del.mutate(r.id)}>{t("common.delete")}</Button>}
              </div>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
