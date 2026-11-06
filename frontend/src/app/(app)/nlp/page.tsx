"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { INTENTS, SENTIMENTS, langLabel, useNlpStatus } from "@/components/analysis";
import { useI18n } from "@/components/providers";
import { Badge, Button, Card, ErrorBanner, Spinner } from "@/components/ui";
import { api, type CorpusItem, type EvalRun, type Expression, type MetricBlock } from "@/lib/api";
import { useMe } from "@/lib/session";

const TASKS = ["sentiment", "sarcasm", "about_target", "language", "intents"] as const;
const LANGS = ["ar-DZ", "ar", "fr", "en", "kab", "mixed", "other"];

function StatusCard() {
  const { t } = useI18n();
  const s = useNlpStatus().data;
  if (!s) return <Spinner />;
  return (
    <Card title={t("nlpPage.status")}>
      <p className="mb-3 text-sm text-ink-soft">{t("nlpPage.statusHelp")}</p>
      <table className="w-full text-sm">
        <tbody className="divide-y divide-line">
          {TASKS.map((k) => {
            const x = s.tasks[k];
            return (
              <tr key={k}>
                <td className="py-2">{t(`nlpPage.task.${k}`)}</td>
                <td className="py-2 tabular-nums text-ink-soft">
                  {x.macro_f1 != null ? `macro-F1 ${x.macro_f1.toFixed(2)} · ${t("nlpPage.items", { n: x.n })}` : t("nlpPage.noRun")}
                </td>
                <td className="py-2 text-end"><Badge tone={x.validated ? "brand" : "warn"}>{t(x.validated ? "nlp.validated" : "nlp.notValidated")}</Badge></td>
              </tr>
            );
          })}
        </tbody>
      </table>
      <p className="mt-3 text-xs text-ink-mute">
        {t("nlpPage.thresholds", { f1: s.thresholds.macro_f1, cov: s.thresholds.coverage, n: s.thresholds.gold_items })}
      </p>
      {!s.ai_configured && <p className="mt-1 text-xs text-signal-warn">{t("nlp.notConfigured")}</p>}
    </Card>
  );
}

function Expressions() {
  const { t } = useI18n();
  const q = useQuery({ queryKey: ["expressions"], queryFn: () => api.get<Expression[]>("/nlp/expressions") });
  return (
    <Card title={t("nlpPage.expressions")}>
      <p className="mb-3 text-xs text-ink-mute">{t("nlpPage.expressionsHelp")}</p>
      <ul className="grid gap-x-6 gap-y-1 text-sm sm:grid-cols-2">
        {q.data?.map((e) => (
          <li key={e.canonical} className="flex items-baseline justify-between gap-2 border-b border-line py-1">
            <span><span className="font-medium" dir="ltr">{e.canonical}</span> <span dir="rtl">{e.arabic}</span></span>
            <span className="text-xs text-ink-soft">{e.meaning_fr}{e.polarity !== "none" && ` · ${e.polarity === "positive" ? "+" : "−"}`}</span>
          </li>
        ))}
      </ul>
    </Card>
  );
}

function ReviewItem({ item, onDone }: { item: CorpusItem; onDone: () => void }) {
  const { t } = useI18n();
  const auto = item.auto_labels as Record<string, string | string[]>;
  const init = () => ({
    sentiment: SENTIMENTS.includes(auto.sentiment as never) ? String(auto.sentiment) : "neutral",
    sarcasm: auto.sarcasm === "yes" ? "yes" : "no",
    about_target: auto.about_target === "no" ? "no" : "yes",
    language: LANGS.includes(String(auto.language)) ? String(auto.language) : "ar-DZ",
    intents: Array.isArray(auto.intents) ? auto.intents : [],
  });
  const [l, setL] = useState(init);
  useEffect(() => setL(init()), [item.id]); // eslint-disable-line react-hooks/exhaustive-deps
  const verify = useMutation({ mutationFn: () => api.post(`/nlp/corpus/${item.id}/verify`, { labels: l }), onSuccess: onDone });
  const reject = useMutation({ mutationFn: () => api.post(`/nlp/corpus/${item.id}/reject`), onSuccess: onDone });
  const sel = (k: "sentiment" | "sarcasm" | "about_target" | "language", opts: [string, string][]) => (
    <label className="flex flex-col gap-1 text-xs text-ink-mute">
      {k === "sentiment" ? t("dash.sentiment") : k === "sarcasm" ? t("nlp.sarcasm") : k === "about_target" ? t("nlp.aboutTarget") : t("nlp.languageLabel")}
      <select className="field" value={l[k]} onChange={(e) => setL({ ...l, [k]: e.target.value })}>
        {opts.map(([v, label]) => <option key={v} value={v}>{label}</option>)}
      </select>
    </label>
  );
  return (
    <div className="space-y-4">
      <div className="rounded bg-paper-sunk p-4">
        <p className="text-xs text-ink-mute">{String(auto.target ?? "")} · {item.script}</p>
        {auto.title ? <p className="mt-1 font-medium" dir="auto">{String(auto.title)}</p> : null}
        <p className="mt-1 text-base leading-relaxed" dir="auto">{item.text}</p>
        {item.source_url && <a href={item.source_url} target="_blank" rel="noopener noreferrer nofollow" className="mt-2 inline-block text-xs text-brand hover:underline" dir="ltr">{item.source_url}</a>}
      </div>
      <div className="grid gap-3 sm:grid-cols-4">
        {sel("sentiment", SENTIMENTS.map((s) => [s, t(`sent.${s}`)]))}
        {sel("about_target", [["yes", t("nlp.yes")], ["no", t("nlp.no")]])}
        {sel("sarcasm", [["no", t("nlp.no")], ["yes", t("nlp.yes")]])}
        {sel("language", LANGS.map((x) => [x, langLabel(x, t)]))}
      </div>
      <div className="flex flex-wrap gap-2">
        {INTENTS.map((i) => (
          <label key={i} className="flex items-center gap-1 rounded border border-line px-2 py-0.5 text-xs">
            <input type="checkbox" checked={l.intents.includes(i)}
                   onChange={() => setL({ ...l, intents: l.intents.includes(i) ? l.intents.filter((x) => x !== i) : [...l.intents, i] })} />
            {t(`intent.${i}`)}
          </label>
        ))}
      </div>
      <ErrorBanner error={verify.error ?? reject.error} />
      <div className="flex gap-2">
        <Button onClick={() => verify.mutate()} disabled={verify.isPending}>{t("nlpPage.confirm")}</Button>
        <Button variant="ghost" onClick={() => reject.mutate()} disabled={reject.isPending}>{t("nlpPage.reject")}</Button>
      </div>
    </div>
  );
}

function AdminSection() {
  const { t, fmtDate } = useI18n();
  const qc = useQueryClient();
  const stats = useQuery({ queryKey: ["corpus-stats"], queryFn: () => api.get<{ total: number; by_split_status: { split: string; status: string; count: number }[] }>("/nlp/corpus/stats") });
  const queue = useQuery({ queryKey: ["corpus-queue"], queryFn: () => api.get<CorpusItem[]>("/nlp/corpus/queue?limit=50") });
  const runs = useQuery({ queryKey: ["eval-runs"], queryFn: () => api.get<EvalRun[]>("/nlp/benchmark"),
    refetchInterval: (q) => q.state.data?.some((r) => r.status === "queued" || r.status === "running") ? 5000 : false });
  const refresh = () => ["corpus-stats", "corpus-queue", "eval-runs", "nlp-status"].forEach((k) => qc.invalidateQueries({ queryKey: [k] }));
  const sample = useMutation({ mutationFn: () => api.post("/nlp/corpus/sample", { n: 50 }), onSuccess: refresh });
  const bench = useMutation({ mutationFn: () => api.post("/nlp/benchmark", {}), onSuccess: refresh });
  const item = queue.data?.[0];

  return (
    <>
      <Card title={t("nlpPage.corpus")} action={<Button variant="ghost" onClick={() => sample.mutate()} disabled={sample.isPending}>{t("nlpPage.sample", { n: 50 })}</Button>}>
        <p className="text-sm">{stats.data?.total ?? "…"}</p>
        <ul className="mt-2 flex flex-wrap gap-2 text-xs">
          {stats.data?.by_split_status.map((x) => <li key={`${x.split}-${x.status}`} className="rounded bg-paper-sunk px-2 py-1">{x.split} · {x.status}: {x.count}</li>)}
        </ul>
        <div className="mt-2"><ErrorBanner error={sample.error ?? stats.error} /></div>
      </Card>
      <Card title={t("nlpPage.review")} action={queue.data && <Badge>{t("nlpPage.remaining", { n: queue.data.length })}</Badge>}>
        <p className="mb-4 text-xs text-ink-mute">{t("nlpPage.reviewHelp")}</p>
        {item ? <ReviewItem item={item} onDone={refresh} /> : <p className="text-sm text-ink-mute">{t("nlpPage.queueEmpty")}</p>}
      </Card>
      <Card title={t("nlpPage.benchmark")} action={<Button onClick={() => bench.mutate()} disabled={bench.isPending}>{t("nlpPage.runBenchmark")}</Button>}>
        <p className="mb-3 text-xs text-ink-mute">{t("nlpPage.benchmarkCost")}</p>
        <ErrorBanner error={bench.error} />
        <ul className="divide-y divide-line text-sm">
          {runs.data?.map((r) => {
            const sent = r.metrics.sentiment as MetricBlock | undefined;
            return (
              <li key={r.id} className="py-3">
                <p className="flex flex-wrap items-center gap-2">
                  <Badge tone={r.status === "completed" ? "brand" : r.status === "failed" ? "neg" : "neutral"}>{r.status}</Badge>
                  <span className="text-ink-mute">{fmtDate(r.created_at)} · {t("nlpPage.items", { n: r.n_items })} · {r.prompt_version}</span>
                </p>
                {r.error && <p className="mt-1 text-xs text-signal-neg">{r.error}</p>}
                {r.status === "completed" && (
                  <div className="mt-2 grid gap-2 text-xs sm:grid-cols-5">
                    {TASKS.map((k) => {
                      const m = r.metrics[k] as MetricBlock | undefined;
                      return m ? (
                        <div key={k} className="rounded bg-paper-sunk p-2">
                          <p className="text-ink-mute">{t(`nlpPage.task.${k}`)}</p>
                          <p className="text-base font-semibold tabular-nums">{m.macro_f1.toFixed(2)}</p>
                          {m.coverage != null && <p className="text-ink-mute">{t("nlpPage.coverage")} {Math.round(m.coverage * 100)} %</p>}
                        </div>
                      ) : null;
                    })}
                  </div>
                )}
                {sent?.confusion && (
                  <table className="mt-2 text-xs tabular-nums">
                    <tbody>
                      {Object.entries(sent.confusion).map(([g, row]) => (
                        <tr key={g}><th className="pe-3 text-start font-normal text-ink-mute">{g}</th>
                          {Object.entries(row).map(([p, n]) => <td key={p} className="pe-3">{p}: {n}</td>)}</tr>
                      ))}
                    </tbody>
                  </table>
                )}
              </li>
            );
          })}
        </ul>
      </Card>
    </>
  );
}

export default function NlpPage() {
  const { t } = useI18n();
  const me = useMe().data;
  return (
    <div className="max-w-5xl space-y-6">
      <h1 className="text-2xl font-semibold">{t("nlpPage.title")}</h1>
      <StatusCard />
      {me?.is_platform_admin && <AdminSection />}
      <Expressions />
    </div>
  );
}
