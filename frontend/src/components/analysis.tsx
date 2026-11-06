"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { api, type Analysis, type MentionDetail, type NlpStatus, type Sentiment } from "@/lib/api";
import { hasMessage } from "@/lib/i18n";
import { useI18n } from "./providers";
import { Badge, Button, ErrorBanner } from "./ui";

export const SENTIMENTS: Exclude<Sentiment, "UNCERTAIN">[] = ["positive", "negative", "neutral", "mixed"];
const SENT_TONE: Record<Sentiment, "brand" | "neg" | "neutral" | "warn"> = {
  positive: "brand", negative: "neg", neutral: "neutral", mixed: "warn", UNCERTAIN: "neutral",
};
export const INTENTS = ["complaint", "praise", "recommendation", "purchase_intent", "boycott_intent",
  "support_dissatisfaction", "escalation_threat", "question", "information", "comparison"] as const;

export function useNlpStatus() {
  return useQuery({ queryKey: ["nlp-status"], queryFn: () => api.get<NlpStatus>("/nlp/status"), staleTime: 300_000 });
}

export function langLabel(code: string | null | undefined, t: ReturnType<typeof useI18n>["t"]) {
  const key = `lang.code.${code}`;
  return code && hasMessage(key) ? t(key) : code ?? "—";
}

/** Labels for one mention. Unvalidated tasks say so; nothing is shown that wasn't produced. */
export function AnalysisBadges({ a }: { a: Analysis }) {
  const { t } = useI18n();
  const status = useNlpStatus().data;
  if (a.status !== "done") {
    const key = { pending: "nlp.pending", failed: "nlp.failed", not_configured: "nlp.notConfigured",
      blocked_by_policy: "nlp.blocked", done: "nlp.pending" }[a.status] as "nlp.pending";
    return (
      <span className="flex flex-wrap gap-1">
        <Badge>{t(key)}</Badge>
        {a.heuristic_language && a.heuristic_language !== "UNCERTAIN" && <Badge>{langLabel(a.heuristic_language, t)}</Badge>}
      </span>
    );
  }
  const validated = status?.tasks.sentiment.validated;
  return (
    <span className="flex flex-wrap items-center gap-1">
      {a.sentiment && (
        <Badge tone={SENT_TONE[a.sentiment]}>
          {t(`sent.${a.sentiment}`)}{!validated && <span className="ms-1 opacity-60">· {t("nlp.notValidated")}</span>}
        </Badge>
      )}
      {a.sarcasm === "yes" && <Badge tone="warn">{t("nlp.sarcasm")}</Badge>}
      {a.about_target === "no" && <Badge tone="warn">{t("nlp.homonym")}</Badge>}
      {a.about_target === "UNCERTAIN" && <Badge>{t("nlp.aboutUncertain")}</Badge>}
      {a.language && <Badge>{langLabel(a.language, t)}{a.code_switching && ` · ${t("nlp.codeSwitching")}`}</Badge>}
      {a.intents?.map((i) => <Badge key={i}>{hasMessage(`intent.${i}`) ? t(`intent.${i}` as "intent.complaint") : i}</Badge>)}
      {a.corrected && <Badge tone="brand">{t("nlp.corrected")}</Badge>}
    </span>
  );
}

export function AnalysisDetails({ a }: { a: Analysis }) {
  const { t } = useI18n();
  return (
    <div className="space-y-1 text-xs">
      {a.agreement != null && (
        <p className="text-ink-mute">
          {t("nlp.agreement", { n: Math.round(a.agreement * 100) })}{a.arbitration_used && ` · ${t("nlp.arbitrated")}`}
        </p>
      )}
      {a.translation_fr && <p><span className="text-ink-mute">{t("nlp.translation")}: </span>{a.translation_fr}</p>}
      {a.normalized_text && <p dir="auto"><span className="text-ink-mute">{t("nlp.normalized")}: </span>{a.normalized_text}</p>}
      {a.script && <p className="text-ink-mute">{t("nlp.script")}: {a.script}</p>}
    </div>
  );
}

export function CorrectionForm({ id, a, onDone }: { id: string; a: Analysis; onDone: () => void }) {
  const { t } = useI18n();
  const qc = useQueryClient();
  const [f, setF] = useState({
    sentiment: a.sentiment && a.sentiment !== "UNCERTAIN" ? a.sentiment : "neutral",
    about_target: a.about_target === "no" ? "no" : "yes",
    sarcasm: a.sarcasm === "yes" ? "yes" : "no",
    intents: a.intents ?? [],
  });
  const m = useMutation({
    mutationFn: () => api.patch<MentionDetail>(`/mentions/${id}/analysis`, f),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["mentions"] }); qc.invalidateQueries({ queryKey: ["mention", id] }); onDone(); },
  });
  const toggle = (i: string) => setF({ ...f, intents: f.intents.includes(i) ? f.intents.filter((x) => x !== i) : [...f.intents, i] });
  return (
    <form className="mt-3 space-y-3 rounded border border-line p-3 text-sm" onSubmit={(e) => { e.preventDefault(); m.mutate(); }}>
      <p className="font-medium">{t("nlp.correctTitle")}</p>
      <div className="flex flex-wrap gap-3">
        <label className="flex items-center gap-2">{t("dash.sentiment")}
          <select className="field w-auto" value={f.sentiment} onChange={(e) => setF({ ...f, sentiment: e.target.value as typeof f.sentiment })}>
            {SENTIMENTS.map((s) => <option key={s} value={s}>{t(`sent.${s}`)}</option>)}
          </select>
        </label>
        <label className="flex items-center gap-2">{t("nlp.aboutTarget")}
          <select className="field w-auto" value={f.about_target} onChange={(e) => setF({ ...f, about_target: e.target.value })}>
            <option value="yes">{t("nlp.yes")}</option><option value="no">{t("nlp.no")}</option>
          </select>
        </label>
        <label className="flex items-center gap-2">{t("nlp.sarcasm")}
          <select className="field w-auto" value={f.sarcasm} onChange={(e) => setF({ ...f, sarcasm: e.target.value })}>
            <option value="no">{t("nlp.no")}</option><option value="yes">{t("nlp.yes")}</option>
          </select>
        </label>
      </div>
      <fieldset>
        <legend className="mb-1 text-xs text-ink-mute">{t("nlp.intents")}</legend>
        <div className="flex flex-wrap gap-2">
          {INTENTS.map((i) => (
            <label key={i} className="flex items-center gap-1 rounded border border-line px-2 py-0.5 text-xs">
              <input type="checkbox" checked={f.intents.includes(i)} onChange={() => toggle(i)} />{t(`intent.${i}`)}
            </label>
          ))}
        </div>
      </fieldset>
      <ErrorBanner error={m.error} />
      <div className="flex gap-2">
        <Button type="submit" disabled={m.isPending}>{t("common.save")}</Button>
        <Button type="button" variant="ghost" onClick={onDone}>{t("common.cancel")}</Button>
      </div>
    </form>
  );
}
