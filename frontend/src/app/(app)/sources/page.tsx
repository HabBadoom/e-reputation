"use client";

import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { useI18n } from "@/components/providers";
import { Badge, Button, Card, ErrorBanner, Field, Spinner } from "@/components/ui";
import { api, type ConnectorInfo, type Coverage, type Page, type Source, type SourceCheck } from "@/lib/api";
import { useCan } from "@/lib/session";

const COVERAGE_TONE: Record<Coverage, "brand" | "neutral" | "warn" | "neg"> = {
  AVAILABLE: "brand", PARTIALLY_AVAILABLE: "neutral", PENDING_AUTHORIZATION: "warn",
  RESTRICTED: "warn", UNAVAILABLE: "neg", CONNECTOR_ERROR: "neg",
};

function Connectors() {
  const { t } = useI18n();
  const q = useQuery({ queryKey: ["connectors"], queryFn: () => api.get<ConnectorInfo[]>("/connectors") });
  if (!q.data) return q.error ? <ErrorBanner error={q.error} /> : null;
  return (
    <Card title={t("connectors.title")}>
      <ul className="grid gap-4 md:grid-cols-3">
        {q.data.map((c) => (
          <li key={c.key} className="rounded border border-line p-3 text-sm">
            <p className="mb-1 font-medium">{c.name}</p>
            <p className="mb-2 flex flex-wrap gap-1">
              <Badge tone={c.configured ? "brand" : "neutral"}>
                {t(c.configured ? "connectors.configured" : "connectors.notConfigured")}
              </Badge>
              <Badge tone={c.review_status === "approved" ? "brand" : c.review_status === "rejected" ? "neg" : "warn"}>
                {t(`connectors.review.${c.review_status}` as "connectors.review.approved")}
              </Badge>
            </p>
            <details className="text-xs text-ink-soft">
              <summary className="cursor-pointer text-ink-mute">{t("connectors.limitations")}</summary>
              <ul className="mt-1 list-disc space-y-0.5 ps-4" dir="ltr">
                {c.limitations.map((l) => <li key={l}>{l}</li>)}
              </ul>
              <p className="mt-2" dir="ltr">{c.legal_basis}</p>
            </details>
          </li>
        ))}
      </ul>
    </Card>
  );
}

function AddSource({ onChecked }: { onChecked: (r: SourceCheck) => void }) {
  const { t } = useI18n();
  const qc = useQueryClient();
  const [kind, setKind] = useState<"rss" | "website">("rss");
  const [url, setUrl] = useState("");
  const m = useMutation({
    mutationFn: () => api.post<SourceCheck>("/sources", { kind, url }),
    onSuccess: (r) => { setUrl(""); onChecked(r); qc.invalidateQueries({ queryKey: ["sources"] }); },
  });
  return (
    <Card title={t("sources.add")}>
      <form className="flex flex-wrap items-end gap-2" onSubmit={(e) => { e.preventDefault(); m.mutate(); }}>
        <select className="field w-auto" value={kind} onChange={(e) => setKind(e.target.value as "rss" | "website")}
                aria-label={t("brands.kind")}>
          <option value="rss">{t("sources.kind.rss")}</option>
          <option value="website">{t("sources.kind.website")}</option>
        </select>
        <div className="min-w-64 flex-1">
          <Field label={t("sources.url")} type="url" required dir="ltr" placeholder="https://"
                 value={url} onChange={(e) => setUrl(e.target.value)} />
        </div>
        <Button type="submit" disabled={m.isPending}>{t("common.add")}</Button>
      </form>
      <div className="mt-2"><ErrorBanner error={m.error} /></div>
    </Card>
  );
}

function SourceRow({ s, canWrite, onChecked }: { s: Source; canWrite: boolean; onChecked: (r: SourceCheck) => void }) {
  const { t, fmtDate } = useI18n();
  const qc = useQueryClient();
  const refresh = () => qc.invalidateQueries({ queryKey: ["sources"] });
  const check = useMutation({ mutationFn: () => api.post<SourceCheck>(`/sources/${s.id}/check`), onSuccess: (r) => { onChecked(r); refresh(); } });
  const toggle = useMutation({ mutationFn: () => api.patch(`/sources/${s.id}`, { enabled: !s.enabled }), onSuccess: refresh });
  const del = useMutation({ mutationFn: () => api.del(`/sources/${s.id}`), onSuccess: refresh });
  return (
    <li className="flex flex-wrap items-start justify-between gap-3 p-4">
      <div className="min-w-0">
        <p className="flex flex-wrap items-center gap-2 text-sm font-medium">
          <span dir="ltr">{s.name && s.name !== s.domain ? s.name : s.domain}</span>
          <Badge>{t(`sources.kind.${s.kind}`)}</Badge>
          <Badge tone={COVERAGE_TONE[s.coverage_status]}>{t(`coverage.${s.coverage_status}`)}</Badge>
          <Badge tone={s.monitored ? "brand" : "neutral"}>{t(s.monitored ? "sources.monitored" : "sources.notMonitored")}</Badge>
        </p>
        <p className="mt-1 truncate text-xs text-ink-mute" dir="ltr">{s.url}</p>
        <p className="mt-1 text-xs text-ink-mute">
          {s.mentions_count} {t("sources.mentions")} · {t(`sources.via.${s.discovered_via}`)}
          {s.last_collected_at && <> · {t("sources.lastCollected")} {fmtDate(s.last_collected_at)}</>}
        </p>
        {(s.restrictions || s.last_error) && (
          <p className="mt-1 text-xs text-signal-warn" dir="ltr">{s.restrictions || s.last_error}</p>
        )}
      </div>
      {canWrite && (
        <div className="flex gap-2">
          <Button variant="ghost" onClick={() => check.mutate()} disabled={check.isPending}>{t("sources.check")}</Button>
          <Button variant="ghost" onClick={() => toggle.mutate()} disabled={toggle.isPending}>
            {t(s.enabled ? "sources.disable" : "sources.enable")}
          </Button>
          <Button variant="danger" onClick={() => confirm(t("common.confirmDelete")) && del.mutate()}>{t("common.delete")}</Button>
        </div>
      )}
      <div className="w-full"><ErrorBanner error={check.error ?? toggle.error ?? del.error} /></div>
    </li>
  );
}

export default function SourcesPage() {
  const { t } = useI18n();
  const canWrite = useCan("sources:write");
  const [kind, setKind] = useState("");
  const [result, setResult] = useState<SourceCheck | null>(null);
  const q = useQuery({
    queryKey: ["sources", kind],
    queryFn: () => api.get<Page<Source>>(`/sources?limit=200${kind ? `&kind=${kind}` : ""}`),
    placeholderData: keepPreviousData,
  });
  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-semibold">{t("sources.title")}</h1>
        <p className="mt-1 max-w-3xl text-sm text-ink-soft">{t("sources.intro")}</p>
      </div>
      <Connectors />
      {canWrite && <AddSource onChecked={setResult} />}
      {result && (
        <div role="status" className={`rounded border p-3 text-sm ${result.ok ? "border-brand/30 bg-brand-tint text-brand-strong" : "border-signal-warn/30 bg-signal-warn/5 text-signal-warn"}`}>
          <p>{result.source.domain}: {result.ok ? t("sources.checkOk") : t("sources.checkFailed", { reason: result.detail })}</p>
          {result.discovered_feeds.length > 0 && (
            <div className="mt-2">
              <p>{t("sources.discovered")}</p>
              <ul className="mt-1 list-disc ps-5" dir="ltr">{result.discovered_feeds.map((f) => <li key={f.id}>{f.url}</li>)}</ul>
            </div>
          )}
        </div>
      )}
      <div className="flex gap-2">
        {(["", "website", "rss"] as const).map((k) => (
          <Button key={k} variant={kind === k ? "primary" : "ghost"} onClick={() => setKind(k)}>
            {k ? t(`sources.kind.${k}`) : `${t("sources.title")} (${q.data?.total ?? "…"})`}
          </Button>
        ))}
      </div>
      {q.isLoading ? <Spinner /> : q.error ? <ErrorBanner error={q.error} /> : (
        <ul className="card divide-y divide-line">
          {q.data!.items.length === 0 && <li className="p-5 text-sm text-ink-mute">{t("sources.empty")}</li>}
          {q.data!.items.map((s) => <SourceRow key={s.id} s={s} canWrite={canWrite} onChecked={setResult} />)}
        </ul>
      )}
    </div>
  );
}
