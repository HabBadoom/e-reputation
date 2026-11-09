"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useState } from "react";
import { BrandSelect } from "@/components/intel";
import { useI18n } from "@/components/providers";
import { Badge, Button, Card, ErrorBanner, Spinner } from "@/components/ui";
import {
  api, type ActionStatus, type AgentCatalog, type AgentFinding, type AiActionOut, type AiTask,
  type AiTaskDetail, type AiTaskStatus, type Page, type TaskKind,
} from "@/lib/api";
import { hasMessage, type Locale, type MessageKey } from "@/lib/i18n";
import { useCan } from "@/lib/session";

const KINDS: TaskKind[] = ["brand_health", "crisis_response", "competitor_review", "source_discovery", "report", "ask"];
const TASK_TONE: Record<AiTaskStatus, "brand" | "neutral" | "warn" | "neg"> = {
  queued: "neutral", running: "neutral", waiting_approval: "warn", completed: "brand", failed: "neg", cancelled: "neutral",
};
const ACTION_TONE: Record<ActionStatus, "brand" | "neutral" | "warn" | "neg"> = {
  proposed: "neutral", rejected_by_policy: "neg", pending_approval: "warn", approved: "brand",
  rejected: "neutral", executed: "brand", failed: "neg", expired: "neutral",
};
const LEVEL: Record<AgentFinding["level"], string> = {
  info: "border-line", warning: "border-signal-warn", critical: "border-signal-neg",
};

/** Translate a code with values; values are formatted (shares as %, lists joined, dates). */
function useT() {
  const { t, fmtDate } = useI18n();
  return (key: string, values: Record<string, unknown> = {}, fallback?: string) => {
    if (!hasMessage(key)) return fallback ?? key;
    const v: Record<string, string | number> = {};
    for (const [k, x] of Object.entries(values)) {
      if (x === null || x === undefined) v[k] = "—";
      else if (Array.isArray(x)) v[k] = x.join(", ");
      else if (typeof x === "number" && /share/.test(k) && x <= 1) v[k] = `${Math.round(x * 1000) / 10}`;
      else if (k === "since" && typeof x === "string") v[k] = fmtDate(x);
      else if (typeof x === "object") v[k] = Object.entries(x as Record<string, unknown>).map(([a, b]) => `${a}: ${b}`).join(", ") || "—";
      else v[k] = x as string | number;
    }
    return t(key as MessageKey, v);
  };
}

function NewTask({ onCreated }: { onCreated: (id: string) => void }) {
  const { t, locale } = useI18n();
  const [kind, setKind] = useState<TaskKind>("brand_health");
  const [brand, setBrand] = useState("");
  const [goal, setGoal] = useState("");
  const [format, setFormat] = useState("pdf");
  const create = useMutation({
    mutationFn: () => api.post<AiTask>("/ai/tasks", {
      kind, brand_id: brand || null, goal: kind === "ask" ? goal : "", locale: locale as Locale,
      ...(kind === "report" ? { report_format: format } : {}),
    }),
    onSuccess: (task) => { setGoal(""); onCreated(task.id); },
  });
  return (
    <Card title={t("ai.new")}>
      <form className="space-y-4" onSubmit={(e) => { e.preventDefault(); create.mutate(); }}>
        <div className="grid gap-4 sm:grid-cols-3">
          <label className="block sm:col-span-1"><span className="label">{t("ai.task")}</span>
            <select className="field" value={kind} onChange={(e) => setKind(e.target.value as TaskKind)}>
              {KINDS.map((k) => <option key={k} value={k}>{t(`ai.kind.${k}`)}</option>)}
            </select>
          </label>
          <label className="block"><span className="label">{t("intel.brand")}</span>
            <BrandSelect value={brand} onChange={setBrand} />
          </label>
          {kind === "report" && (
            <label className="block"><span className="label">{t("rep.format")}</span>
              <select className="field" value={format} onChange={(e) => setFormat(e.target.value)}>
                {(["pdf", "xlsx", "csv", "html"] as const).map((f) => <option key={f} value={f}>{t(`rep.fmt.${f}`)}</option>)}
              </select>
            </label>
          )}
        </div>
        {kind === "ask" && (
          <label className="block"><span className="label">{t("ai.goal")}</span>
            <textarea className="field min-h-20" dir="auto" maxLength={1000} required minLength={3}
                      value={goal} onChange={(e) => setGoal(e.target.value)} placeholder={t("ai.goalPlaceholder")} />
          </label>
        )}
        <p className="text-xs text-ink-mute">{t(`ai.help.${kind}`)}</p>
        <ErrorBanner error={create.error} />
        <Button type="submit" disabled={create.isPending}>{t("ai.start")}</Button>
      </form>
    </Card>
  );
}

function ResponseDraft({ a, canApprove }: { a: AiActionOut; canApprove: boolean }) {
  const { t } = useI18n();
  const qc = useQueryClient();
  const [reply, setReply] = useState(String(a.params.reply ?? ""));
  const refresh = () => { qc.invalidateQueries({ queryKey: ["ai-task"] }); qc.invalidateQueries({ queryKey: ["ai-approvals"] }); qc.invalidateQueries({ queryKey: ["ai-tasks"] }); };
  const approve = useMutation({
    mutationFn: () => api.post(`/ai/actions/${a.id}/approve`, reply !== a.params.reply ? { reply } : {}),
    onSuccess: refresh,
  });
  const reject = useMutation({ mutationFn: () => api.post(`/ai/actions/${a.id}/reject`, {}), onSuccess: refresh });
  const url = typeof a.rationale.mention_url === "string" ? a.rationale.mention_url : null;
  return (
    <div className="space-y-2">
      {url && (
        <p className="text-xs text-ink-soft">
          {t("ai.replyTo")} <a href={url} target="_blank" rel="noopener noreferrer nofollow" className="text-brand hover:underline" dir="auto">
            {String(a.rationale.mention_title || url)}</a>
        </p>
      )}
      {a.status === "pending_approval" && canApprove ? (
        <>
          <textarea className="field min-h-24" dir="auto" maxLength={800} value={reply} onChange={(e) => setReply(e.target.value)} />
          {Boolean(a.params.needs_facts) && <p className="text-xs text-signal-warn">{t("ai.needsFacts")}</p>}
          <div className="flex gap-2">
            <Button disabled={approve.isPending || !reply.trim()} onClick={() => approve.mutate()}>{t("ai.approveReply")}</Button>
            <Button variant="ghost" disabled={reject.isPending} onClick={() => reject.mutate()}>{t("ai.reject")}</Button>
          </div>
          <ErrorBanner error={approve.error ?? reject.error} />
        </>
      ) : (
        <p className="whitespace-pre-wrap rounded bg-paper-sunk p-3 text-sm" dir="auto">
          {String((a.result.approved_reply as string | undefined) ?? a.params.reply ?? "")}
        </p>
      )}
      {a.status === "executed" && <p className="text-xs text-ink-mute">{t("ai.notPublished")}</p>}
    </div>
  );
}

function ActionRow({ a }: { a: AiActionOut }) {
  const { t, fmtDate } = useI18n();
  const tr = useT();
  const qc = useQueryClient();
  const canApprove = useCan("ai:approve");
  const refresh = () => { qc.invalidateQueries({ queryKey: ["ai-task"] }); qc.invalidateQueries({ queryKey: ["ai-approvals"] }); };
  const approve = useMutation({ mutationFn: () => api.post(`/ai/actions/${a.id}/approve`, {}), onSuccess: refresh });
  const reject = useMutation({ mutationFn: () => api.post(`/ai/actions/${a.id}/reject`, {}), onSuccess: refresh });
  const reason = typeof a.rationale.reason === "string" ? a.rationale.reason : "";
  return (
    <li className="space-y-2 p-4 text-sm">
      <div className="flex flex-wrap items-center gap-2">
        <strong>{tr(`ai.action.${a.kind}`, {}, a.kind)}</strong>
        <Badge tone={ACTION_TONE[a.status]}>{t(`ai.ast.${a.status}`)}</Badge>
        <Badge tone={a.risk === "high" ? "neg" : a.risk === "medium" ? "warn" : "neutral"}>{t(`ai.risk.${a.risk}`)}</Badge>
        {a.approval?.status === "pending" && <span className="text-xs text-ink-mute">{t("ai.expires", { d: fmtDate(a.approval.expires_at) })}</span>}
      </div>
      {reason && <p className="text-xs text-ink-soft">{tr(`ai.reason.${reason}`, a.rationale, reason)}</p>}
      {a.error && <p className="text-xs text-signal-neg">{a.error.split(",").map((e) => tr(`ai.err.${e.split(":")[0]}`, {}, e)).join(" · ")}</p>}
      {a.kind === "use_response" ? <ResponseDraft a={a} canApprove={canApprove} /> : (
        a.status === "pending_approval" && canApprove && (
          <div className="flex gap-2">
            <Button disabled={approve.isPending} onClick={() => approve.mutate()}>{t("ai.approve")}</Button>
            <Button variant="ghost" disabled={reject.isPending} onClick={() => reject.mutate()}>{t("ai.reject")}</Button>
            <ErrorBanner error={approve.error ?? reject.error} />
          </div>
        )
      )}
    </li>
  );
}

function TaskView({ id, onClose }: { id: string; onClose: () => void }) {
  const { t } = useI18n();
  const tr = useT();
  const q = useQuery({
    queryKey: ["ai-task", id],
    queryFn: () => api.get<AiTaskDetail>(`/ai/tasks/${id}`),
    refetchInterval: (query) => (["queued", "running"].includes(query.state.data?.status ?? "queued") ? 2000 : false),
  });
  if (!q.data) return <Card title={t("ai.task")}><ErrorBanner error={q.error} />{q.isLoading && <Spinner />}</Card>;
  const task = q.data;
  const findings = task.result.findings ?? [];
  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h2 className="text-lg font-semibold">
          {t(`ai.kind.${task.resolved_kind ?? task.kind}`)} <Badge tone={TASK_TONE[task.status]}>{t(`ai.st.${task.status}`)}</Badge>
        </h2>
        <button className="text-sm text-brand hover:underline" onClick={onClose}>{t("common.close")}</button>
      </div>
      {task.goal && <p className="text-sm italic text-ink-soft" dir="auto">« {task.goal} »</p>}
      {task.error && <p className="text-sm text-signal-neg">{tr(`ai.err.${task.error.split(":")[0]}`, {}, task.error)}</p>}

      <Card title={t("ai.steps")}>
        <ol className="space-y-1 text-sm">
          {task.runs.map((r) => (
            <li key={r.id} className="flex flex-wrap items-center gap-2">
              <span className="w-6 text-end tabular-nums text-ink-mute">{r.position}.</span>
              <strong>{t(`ai.agent.${r.agent}` as MessageKey)}</strong>
              <Badge tone={r.status === "completed" ? "brand" : r.status === "skipped" ? "neutral" : "warn"}>{t(`ai.run.${r.status}`)}</Badge>
              <span className="text-xs text-ink-mute">
                v{r.agent_version}{r.duration_ms !== null && ` · ${r.duration_ms} ms`}{r.llm_calls > 0 && ` · ${t("ai.llmCalls", { n: r.llm_calls })}`}
                {r.error && ` · ${tr(`ai.err.${r.error.split(":")[0]}`, {}, r.error)}`}
              </span>
            </li>
          ))}
          {["queued", "running"].includes(task.status) && <li><Spinner /></li>}
        </ol>
      </Card>

      {findings.length > 0 && (
        <Card title={t("ai.findings")}>
          <ul className="space-y-2 text-sm">
            {findings.map((f, i) => (
              <li key={i} className={`border-s-4 ps-3 ${LEVEL[f.level]}`}>
                <span className="me-2 text-[11px] uppercase tracking-wide text-ink-mute">{t(`ai.fkind.${f.kind}`)} · {t(`ai.agent.${f.agent}` as MessageKey)}</span>
                <span>{tr(`ai.f.${f.code}`, f.values, f.code)}</span>
              </li>
            ))}
          </ul>
        </Card>
      )}

      {task.actions.length > 0 && (
        <Card title={t("ai.actions")}>
          <ul className="divide-y divide-line">{task.actions.map((a) => <ActionRow key={a.id} a={a} />)}</ul>
        </Card>
      )}
    </div>
  );
}

function Tasks() {
  const { t, fmtDate } = useI18n();
  const router = useRouter();
  const params = useSearchParams();
  const open = params.get("task");
  const canRun = useCan("ai:run");
  const q = useQuery({
    queryKey: ["ai-tasks"], queryFn: () => api.get<Page<AiTask>>("/ai/tasks?limit=50"),
    refetchInterval: (query) => (query.state.data?.items.some((x) => ["queued", "running"].includes(x.status)) ? 3000 : false),
  });
  const show = (id: string | null) => router.replace(id ? `/ai-agents?task=${id}` : "/ai-agents");
  return (
    <div className="grid gap-6 lg:grid-cols-[minmax(0,2fr)_minmax(0,3fr)]">
      <div className="space-y-4">
        {canRun && <NewTask onCreated={show} />}
        <ErrorBanner error={q.error} />
        {q.isLoading ? <Spinner /> : q.data?.items.length === 0 ? (
          <p className="rounded border border-dashed border-line p-6 text-center text-sm text-ink-mute">{t("ai.none")}</p>
        ) : (
          <ul className="divide-y divide-line rounded border border-line bg-paper-raised">
            {q.data?.items.map((x) => (
              <li key={x.id}>
                <button className={`w-full p-3 text-start text-sm hover:bg-paper-sunk ${open === x.id ? "bg-paper-sunk" : ""}`} onClick={() => show(x.id)}>
                  <p className="flex flex-wrap items-center gap-2">
                    <span className="font-medium">{t(`ai.kind.${x.resolved_kind ?? x.kind}`)}</span>
                    <Badge tone={TASK_TONE[x.status]}>{t(`ai.st.${x.status}`)}</Badge>
                  </p>
                  <p className="mt-1 truncate text-xs text-ink-mute" dir="auto">{fmtDate(x.created_at)}{x.goal && ` · ${x.goal}`}</p>
                </button>
              </li>
            ))}
          </ul>
        )}
      </div>
      <div>{open ? <TaskView id={open} onClose={() => show(null)} /> : <p className="text-sm text-ink-mute">{t("ai.pick")}</p>}</div>
    </div>
  );
}

function Approvals() {
  const { t } = useI18n();
  const q = useQuery({ queryKey: ["ai-approvals"], queryFn: () => api.get<AiActionOut[]>("/ai/approvals"), refetchInterval: 30_000 });
  return (
    <div className="space-y-4">
      <p className="text-sm text-ink-soft">{t("ai.approvalsHelp")}</p>
      <ErrorBanner error={q.error} />
      {q.isLoading ? <Spinner /> : q.data?.length === 0 ? (
        <p className="rounded border border-dashed border-line p-6 text-center text-sm text-ink-mute">{t("ai.noApprovals")}</p>
      ) : <ul className="card divide-y divide-line">{q.data?.map((a) => <ActionRow key={a.id} a={a} />)}</ul>}
    </div>
  );
}

function Catalog() {
  const { t } = useI18n();
  const q = useQuery({ queryKey: ["ai-catalog"], queryFn: () => api.get<AgentCatalog>("/ai/agents") });
  if (!q.data) return <ErrorBanner error={q.error} />;
  return (
    <div className="space-y-4">
      <div className="rounded border border-brand/30 bg-brand-tint px-4 py-3 text-sm text-brand-strong">{t("ai.safety")}</div>
      <div className="grid gap-3 md:grid-cols-2">
        {q.data.agents.map((a) => (
          <div key={a.name} className="card p-4 text-sm">
            <p className="font-semibold">{t(`ai.agent.${a.name}` as MessageKey)} <span className="text-xs font-normal text-ink-mute">v{a.version} · {a.timeout_seconds} s</span></p>
            <p className="mt-1 text-ink-soft">{t(`ai.resp.${a.name}` as MessageKey)}</p>
            <p className="mt-2 font-mono text-[11px] text-ink-mute" dir="ltr">{a.tools.join(" · ") || "—"}</p>
          </div>
        ))}
      </div>
      <Card title={t("ai.policy")}>
        <ul className="divide-y divide-line text-sm">
          {q.data.actions.map((a) => (
            <li key={a.kind} className="flex flex-wrap justify-between gap-2 py-2">
              <span>{t(`ai.action.${a.kind}` as MessageKey)}</span>
              <span className="text-xs text-ink-soft">{t(`ai.risk.${a.risk}` as MessageKey)} · {a.requires_approval ? t("ai.needsApproval") : t("ai.auto")} · <span dir="ltr">{a.permission}</span></span>
            </li>
          ))}
        </ul>
      </Card>
    </div>
  );
}

function AiInner() {
  const { t } = useI18n();
  const router = useRouter();
  const params = useSearchParams();
  const tabs = ["tasks", "approvals", "agents"] as const;
  const raw = params.get("tab");
  const tab = tabs.find((x) => x === raw) ?? "tasks";
  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-semibold">{t("ai.title")}</h1>
        <p className="mt-1 text-sm text-ink-soft">{t("ai.subtitle")}</p>
      </div>
      <nav className="flex gap-1 border-b border-line" role="tablist">
        {tabs.map((x) => (
          <button key={x} role="tab" aria-selected={tab === x} onClick={() => router.replace(`/ai-agents?tab=${x}`)}
                  className={`-mb-px border-b-2 px-3 py-2 text-sm ${tab === x ? "border-brand font-medium text-brand-strong" : "border-transparent text-ink-soft hover:text-ink"}`}>
            {t(`ai.tab.${x}`)}
          </button>
        ))}
      </nav>
      {tab === "tasks" && <Tasks />}
      {tab === "approvals" && <Approvals />}
      {tab === "agents" && <Catalog />}
    </div>
  );
}

export default function AiAgentsPage() {
  return <Suspense><AiInner /></Suspense>;
}
