"use client";

import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Fragment, useState } from "react";
import { AnalysisBadges, AnalysisDetails, CorrectionForm, langLabel, SENTIMENTS } from "@/components/analysis";
import { useI18n } from "@/components/providers";
import { Badge, Button, ErrorBanner, Spinner } from "@/components/ui";
import { api, type Brand, type Mention, type MentionDetail, type Page, type Verification } from "@/lib/api";
import { useCan } from "@/lib/session";

const PAGE = 25;
const VERIF_TONE: Record<Verification, "brand" | "neutral" | "warn" | "neg"> = {
  verified_on_page: "brand", feed_item: "brand", snippet_only: "neutral",
  not_found_on_page: "warn", page_unavailable: "neutral", blocked_by_robots: "neutral",
};
const VERIFS = Object.keys(VERIF_TONE) as Verification[];

function MentionDate({ m }: { m: Mention }) {
  const { t, fmtDate } = useI18n();
  if (!m.published_at) return <span className="text-ink-mute">{t("mentions.dateUnknown")}</span>;
  const approx = m.published_at_source === "provider_relative";
  return (
    <span title={approx ? t("mentions.dateApprox") : undefined}>
      {approx && "≈ "}{fmtDate(m.published_at)}
    </span>
  );
}

function Provenance({ id }: { id: string }) {
  const { t, fmtDate } = useI18n();
  const q = useQuery({ queryKey: ["mention", id], queryFn: () => api.get<MentionDetail>(`/mentions/${id}`) });
  if (!q.data) return q.error ? <ErrorBanner error={q.error} /> : <Spinner />;
  const d = q.data;
  const rows: [string, React.ReactNode][] = [
    [t("mentions.canonical"), <span key="c" dir="ltr" className="break-all font-mono text-xs">{d.canonical_url}</span>],
    [t("mentions.method"), t(`method.${d.collection_method}`)],
    [t("mentions.connector"), `${d.connector} v${d.connector_version}`],
    [t("mentions.collectedAt"), fmtDate(d.collected_at)],
    [t("mentions.seen", { n: d.seen_count }), fmtDate(d.last_seen_at)],
    [t("mentions.matched"), d.matched_terms.join(", ") || "—"],
    ...(d.project_name ? [[t("mentions.project"), d.project_name] as [string, string]] : []),
    ...(d.source ? [[t("nav.sources"), `${d.source.name || d.source.domain} · ${t(`coverage.${d.source.coverage_status}`)}`] as [string, string]] : []),
  ];
  return (
    <div className="mt-3 rounded bg-paper-sunk p-3 text-sm">
      <h4 className="mb-2 text-xs font-medium uppercase tracking-wide text-ink-soft">{t("mentions.provenance")}</h4>
      <dl className="grid gap-x-4 gap-y-1 sm:grid-cols-[auto_1fr]">
        {rows.map(([k, v]) => (
          <Fragment key={k}><dt className="text-ink-mute">{k}</dt><dd>{v}</dd></Fragment>
        ))}
      </dl>
      <div className="mt-2"><AnalysisDetails a={d.analysis} /></div>
      {d.snippet && d.snippet !== d.original_text && (
        <p className="mt-2 text-xs text-ink-soft" dir="auto"><span className="text-ink-mute">{t("mentions.snippet")}: </span>{d.snippet}</p>
      )}
    </div>
  );
}

function MentionCard({ m }: { m: Mention }) {
  const { t } = useI18n();
  const canCorrect = useCan("nlp:correct");
  const [open, setOpen] = useState(false);
  const [correcting, setCorrecting] = useState(false);
  return (
    <li className="p-5">
      <div className="mb-1 flex flex-wrap items-center gap-2 text-xs text-ink-mute">
        <span className="font-medium text-ink-soft" dir="ltr">{m.domain}</span>
        <span>·</span><MentionDate m={m} />
        <span>·</span><span>{t(`entity.${m.entity_type}`)}: {m.entity_name}</span>
      </div>
      <a href={m.url} target="_blank" rel="noopener noreferrer nofollow ugc"
         className="font-medium hover:underline" dir="auto">{m.title || m.url}</a>
      {m.original_text && <p className="mt-1 text-sm leading-relaxed text-ink-soft" dir="auto">{m.original_text}</p>}
      <div className="mt-2 flex flex-wrap items-center gap-2">
        <Badge tone={VERIF_TONE[m.verification_status]}>{t(`verif.${m.verification_status}`)}</Badge>
        <Badge>{t(`method.${m.collection_method}`)}</Badge>
        {m.is_repost && <Badge tone="warn">{t("mentions.repost")}</Badge>}
        <AnalysisBadges a={m.analysis} />
        <span className="ms-auto flex gap-3">
          {canCorrect && m.analysis.status === "done" && (
            <button className="text-xs text-brand hover:underline" onClick={() => setCorrecting(!correcting)}>{t("nlp.correct")}</button>
          )}
          <button className="text-xs text-brand hover:underline" onClick={() => setOpen(!open)}>
            {t(open ? "monitoring.hide" : "mentions.provenance")}
          </button>
        </span>
      </div>
      {correcting && <CorrectionForm id={m.id} a={m.analysis} onDone={() => setCorrecting(false)} />}
      {open && <Provenance id={m.id} />}
    </li>
  );
}

export default function MentionsPage() {
  const { t } = useI18n();
  const [f, setF] = useState({ brand_id: "", verification: "", entity_type: "", sentiment: "", language: "", q: "", since: "", include_reposts: false });
  const canCorrect = useCan("nlp:correct");
  const qc = useQueryClient();
  const backlog = useMutation({
    mutationFn: () => api.post<{ queued: number }>("/mentions/analyze"),
    onSuccess: () => setTimeout(() => qc.invalidateQueries({ queryKey: ["mentions"] }), 5000),
  });
  const [offset, setOffset] = useState(0);
  const brands = useQuery({ queryKey: ["brands", "all"], queryFn: () => api.get<Page<Brand>>("/brands?limit=100") });
  const params = new URLSearchParams();
  Object.entries(f).forEach(([k, v]) => {
    if (k === "since" && v) params.set(k, new Date(String(v)).toISOString());
    else if (v) params.set(k, String(v));
  });
  const qs = params.toString();
  const list = useQuery({
    queryKey: ["mentions", qs, offset],
    queryFn: () => api.get<Page<Mention>>(`/mentions?${qs}&limit=${PAGE}&offset=${offset}`),
    placeholderData: keepPreviousData,
  });
  const set = (k: keyof typeof f) => (e: React.ChangeEvent<HTMLInputElement | HTMLSelectElement>) => {
    setOffset(0);
    setF({ ...f, [k]: e.target.type === "checkbox" ? (e.target as HTMLInputElement).checked : e.target.value });
  };

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h1 className="text-2xl font-semibold">{t("mentions.title")}</h1>
        <div className="flex flex-wrap items-center gap-2">
          {backlog.data && <span className="text-xs text-ink-mute">{t("nlp.queued", { n: backlog.data.queued })}</span>}
          {canCorrect && <Button variant="ghost" onClick={() => backlog.mutate()} disabled={backlog.isPending}>{t("nlp.analyzeBacklog")}</Button>}
          <a href={`/api/v1/mentions/export.csv?${qs}`} download><Button variant="ghost">{t("mentions.export")}</Button></a>
        </div>
      </div>
      <div className="card grid gap-3 p-4 md:grid-cols-3 lg:grid-cols-6">
        <input className="field md:col-span-2" type="search" dir="auto" placeholder={t("mentions.search")}
               value={f.q} onChange={set("q")} />
        <select className="field" value={f.brand_id} onChange={set("brand_id")} aria-label={t("monitoring.brand")}>
          <option value="">{t("mentions.allBrands")}</option>
          {brands.data?.items.map((b) => <option key={b.id} value={b.id}>{b.name}</option>)}
        </select>
        <select className="field" value={f.entity_type} onChange={set("entity_type")} aria-label={t("brands.kind")}>
          <option value="">{t("mentions.allEntities")}</option>
          {(["brand", "product", "competitor"] as const).map((e) => <option key={e} value={e}>{t(`entity.${e}`)}</option>)}
        </select>
        <select className="field" value={f.verification} onChange={set("verification")} aria-label={t("mentions.provenance")}>
          <option value="">{t("mentions.allStatuses")}</option>
          {VERIFS.map((v) => <option key={v} value={v}>{t(`verif.${v}`)}</option>)}
        </select>
        <select className="field" value={f.sentiment} onChange={set("sentiment")} aria-label={t("dash.sentiment")}>
          <option value="">{t("nlp.allSentiments")}</option>
          {[...SENTIMENTS, "UNCERTAIN" as const].map((s) => <option key={s} value={s}>{t(`sent.${s}`)}</option>)}
        </select>
        <select className="field" value={f.language} onChange={set("language")} aria-label={t("nlp.languageLabel")}>
          <option value="">{t("nlp.allLanguages")}</option>
          {["ar-DZ", "ar", "fr", "en", "mixed"].map((l) => <option key={l} value={l}>{langLabel(l, t)}</option>)}
        </select>
        <label className="flex items-center gap-2 text-sm">
          <span className="shrink-0 text-ink-soft">{t("mentions.since")}</span>
          <input className="field" type="date" value={f.since} onChange={set("since")} />
        </label>
        <label className="flex items-center gap-2 text-sm md:col-span-3 lg:col-span-6">
          <input type="checkbox" checked={f.include_reposts} onChange={set("include_reposts")} />
          {t("mentions.showReposts")}
        </label>
      </div>
      {list.isLoading ? <Spinner /> : list.error ? <ErrorBanner error={list.error} /> : (
        <ul className="card divide-y divide-line">
          {list.data!.items.length === 0 && <li className="p-5 text-sm text-ink-mute">{t("mentions.empty")}</li>}
          {list.data!.items.map((m) => <MentionCard key={m.id} m={m} />)}
        </ul>
      )}
      {list.data && list.data.total > PAGE && (
        <div className="flex items-center gap-3 text-sm">
          <Button variant="ghost" disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - PAGE))}><span className="flip-rtl">←</span></Button>
          <span className="tabular-nums text-ink-soft">{offset + 1}–{Math.min(offset + PAGE, list.data.total)} / {list.data.total}</span>
          <Button variant="ghost" disabled={offset + PAGE >= list.data.total} onClick={() => setOffset(offset + PAGE)}><span className="flip-rtl">→</span></Button>
        </div>
      )}
    </div>
  );
}
