"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useState } from "react";
import { useI18n } from "@/components/providers";
import { Badge, Button, Card, ErrorBanner, Spinner } from "@/components/ui";
import { api, type ChallengePage, type Fleet, type MissionDetail, type MissionPage, type MissionStatus } from "@/lib/api";
import { hasMessage, type MessageKey } from "@/lib/i18n";
import { useCan, useMe } from "@/lib/session";

const STATUS_TONE: Record<MissionStatus, "brand" | "neutral" | "warn" | "neg"> = {
  CREATED: "neutral", VALIDATING: "neutral", QUEUED: "neutral", ASSIGNED: "neutral", RUNNING: "brand",
  PAUSED: "warn", WAITING_FOR_HUMAN: "warn", COMPLETED: "brand", FAILED: "neg", CANCELLED: "warn", EXPIRED: "neutral",
};
const STATUSES = Object.keys(STATUS_TONE) as MissionStatus[];
const CANCELLABLE: MissionStatus[] = ["CREATED", "VALIDATING", "QUEUED", "ASSIGNED", "RUNNING", "PAUSED", "WAITING_FOR_HUMAN"];

function useKind() {
  const { t } = useI18n();
  return (k: string) => t(`challenge.kind.${k}` as MessageKey);
}

/** Reasons are codes (`not_authorized:domain_cooling_down`, `challenge:turnstile`…): translate them. */
function useReason() {
  const { t } = useI18n();
  const kind = useKind();
  return (code: string | null) => {
    if (!code) return "";
    const [head, tail] = code.split(":", 2);
    if (head === "challenge" && tail) return t("browser.reason.challenge", { kind: kind(tail) });
    if (head === "not_authorized" && tail) {
      const k = `browser.reason.${tail}`;
      return t("browser.reason.notAuthorized", { r: hasMessage(k) ? t(k) : tail });
    }
    const k = `browser.reason.${head}`;
    return hasMessage(k) ? t(k) : code;
  };
}

function Detail({ id, onClose }: { id: string; onClose: () => void }) {
  const { t, fmtDate } = useI18n();
  const reason = useReason();
  const q = useQuery({ queryKey: ["mission", id], queryFn: () => api.get<MissionDetail>(`/browser/missions/${id}`) });
  if (!q.data) return <Card title={t("browser.detail")}><ErrorBanner error={q.error} />{q.isLoading && <Spinner />}</Card>;
  const m = q.data;
  return (
    <Card title={t("browser.detail")} action={<button className="text-sm text-brand hover:underline" onClick={onClose}>{t("common.close")}</button>}>
      <div className="space-y-4 text-sm">
        <p className="break-all font-mono text-xs" dir="ltr">{m.url}</p>
        {m.error && <p className="text-signal-warn">{reason(m.error)}</p>}
        <div>
          <h3 className="label">{t("browser.timeline")}</h3>
          <ol className="space-y-1 border-s border-line ps-3">
            {m.events.map((e, i) => (
              <li key={i} className="text-xs">
                <span className="text-ink-mute">{fmtDate(e.created_at)}</span>{" · "}
                {e.kind === "transition"
                  ? <strong>{e.from_status ? `${t(`browser.st.${e.from_status}` as MessageKey)} → ` : ""}{t(`browser.st.${e.to_status}` as MessageKey)}</strong>
                  : <span>{t(`browser.ev.${e.kind}` as MessageKey)}</span>}
                {e.kind === "navigation" && typeof e.detail.status === "number" && <span className="text-ink-soft"> · HTTP {e.detail.status} · {String(e.detail.elapsed_ms)} ms</span>}
                {e.kind === "blocked_request" && <span className="text-ink-soft" dir="ltr"> · {(e.detail.hosts as string[]).join(", ")}</span>}
                {e.kind === "validation" && <span className="text-ink-soft"> · {t(e.detail.decision === "authorized" ? "browser.authorized" : "browser.denied")}</span>}
                {e.kind === "challenge" && <span className="text-signal-warn"> · {String(e.detail.kind)}</span>}
              </li>
            ))}
          </ol>
        </div>
        {m.sessions.map((s) => (
          <p key={s.id} className="text-xs text-ink-soft">
            {t("browser.session", { n: s.requests_blocked })}{" "}
            <Badge tone={s.cleanup_verified ? "brand" : "warn"}>{t(s.cleanup_verified ? "browser.cleaned" : "browser.notCleaned")}</Badge>
          </p>
        ))}
        <details>
          <summary className="cursor-pointer text-xs font-medium">{t("browser.authorization")}</summary>
          <pre className="mt-2 overflow-x-auto rounded bg-paper-sunk p-2 font-mono text-[11px]" dir="ltr">{JSON.stringify(m.authorization_record, null, 2)}</pre>
        </details>
      </div>
    </Card>
  );
}

function Missions() {
  const { t, fmtDate } = useI18n();
  const qc = useQueryClient();
  const reason = useReason();
  const canRun = useCan("browser:run");
  const [status, setStatus] = useState<"" | MissionStatus>("");
  const [open, setOpen] = useState<string | null>(null);
  const q = useQuery({
    queryKey: ["missions", status],
    queryFn: () => api.get<MissionPage>(`/browser/missions?limit=100${status ? `&status=${status}` : ""}`),
    refetchInterval: (query) => (query.state.data?.items.some((m) => !["COMPLETED", "FAILED", "CANCELLED", "EXPIRED"].includes(m.status)) ? 5000 : false),
  });
  const cancel = useMutation({
    mutationFn: (id: string) => api.post(`/browser/missions/${id}/cancel`),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["missions"] }),
  });
  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-2">
        <select className="field w-52" value={status} onChange={(e) => setStatus(e.target.value as typeof status)} aria-label={t("browser.status")}>
          <option value="">{t("browser.allStatuses")}</option>
          {STATUSES.map((s) => <option key={s} value={s}>{t(`browser.st.${s}`)}</option>)}
        </select>
        <div className="flex flex-wrap gap-1 text-xs">
          {Object.entries(q.data?.by_status ?? {}).map(([s, n]) => <Badge key={s} tone={STATUS_TONE[s as MissionStatus]}>{t(`browser.st.${s}` as MessageKey)} {n}</Badge>)}
        </div>
      </div>
      <ErrorBanner error={q.error ?? cancel.error} />
      {open && <Detail id={open} onClose={() => setOpen(null)} />}
      {q.isLoading ? <Spinner /> : q.data?.items.length === 0 ? (
        <p className="rounded border border-dashed border-line p-6 text-center text-sm text-ink-mute">{t("browser.none")}</p>
      ) : (
        <ul className="divide-y divide-line rounded border border-line bg-paper-raised">
          {q.data?.items.map((m) => (
            <li key={m.id} className="flex flex-wrap items-center justify-between gap-3 p-3 text-sm">
              <button className="min-w-0 flex-1 text-start" onClick={() => setOpen(m.id)}>
                <p className="flex flex-wrap items-center gap-2">
                  <Badge tone={STATUS_TONE[m.status]}>{t(`browser.st.${m.status}`)}</Badge>
                  <span className="font-medium">{t(`browser.kind.${m.kind}`)}</span>
                  <span className="text-ink-soft" dir="ltr">{m.domain}</span>
                </p>
                <p className="mt-1 text-xs text-ink-mute">
                  {fmtDate(m.created_at)} · {t("browser.attempts", { n: m.attempts, max: m.max_attempts })}
                  {m.error && <span className="ms-1 text-signal-warn">· {reason(m.error)}</span>}
                </p>
              </button>
              {canRun && CANCELLABLE.includes(m.status) && (
                <Button variant="ghost" onClick={() => cancel.mutate(m.id)}>{t("common.cancel")}</Button>
              )}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

function Challenges() {
  const { t, fmtDate } = useI18n();
  const kind = useKind();
  const q = useQuery({ queryKey: ["challenges"], queryFn: () => api.get<ChallengePage>("/browser/challenges?limit=100") });
  return (
    <div className="space-y-4">
      <div className="rounded border border-brand/30 bg-brand-tint px-4 py-3 text-sm text-brand-strong">{t("challenge.policy")}</div>
      <ErrorBanner error={q.error} />
      {q.isLoading ? <Spinner /> : q.data?.items.length === 0 ? (
        <p className="rounded border border-dashed border-line p-6 text-center text-sm text-ink-mute">{t("challenge.none")}</p>
      ) : (
        <ul className="space-y-2">
          {q.data?.items.map((c) => (
            <li key={c.id} className="card p-4 text-sm">
              <p className="flex flex-wrap items-center gap-2">
                <strong>{kind(c.kind)}</strong>
                <span className="text-ink-soft" dir="ltr">{c.domain}</span>
                <Badge tone={c.state === "SOURCE_RESTRICTED" ? "neg" : "warn"}>{t(`challenge.state.${c.state}`)}</Badge>
                <Badge>{t(`challenge.via.${c.via}`)}</Badge>
              </p>
              <p className="mt-1 text-xs text-ink-soft">
                {fmtDate(c.detected_at)} · {t("challenge.confidence", { p: Math.round(c.confidence * 100) })}
                {c.response.cooldown_until && <> · {t("challenge.cooldown", { d: fmtDate(c.response.cooldown_until) })}</>}
                {c.response.fallback && <> · {t(`challenge.fallback.${c.response.fallback}` as MessageKey)}</>}
              </p>
              <p className="mt-1 truncate font-mono text-[11px] text-ink-mute" dir="ltr">{c.url}</p>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

function FleetView() {
  const { t, fmtDate } = useI18n();
  const q = useQuery({ queryKey: ["fleet"], queryFn: () => api.get<Fleet>("/browser/fleet"), refetchInterval: 15_000 });
  if (q.isLoading) return <Spinner />;
  if (!q.data) return <ErrorBanner error={q.error} />;
  const f = q.data;
  return (
    <div className="space-y-4">
      <p className="text-sm text-ink-soft">{t("fleet.summary", { busy: f.busy, cap: f.capacity, n: f.workers.filter((w) => w.status === "online").length })}</p>
      <Card title={t("fleet.workers")}>
        {f.workers.length === 0 ? <p className="text-sm text-ink-mute">{t("fleet.noWorkers")}</p> : (
          <ul className="divide-y divide-line text-sm">
            {f.workers.map((w) => (
              <li key={w.id} className="flex flex-wrap justify-between gap-2 py-2">
                <span><strong>{w.name}</strong> <Badge tone={w.status === "online" ? "brand" : "neutral"}>{t(`fleet.st.${w.status}` as MessageKey)}</Badge></span>
                <span className="text-xs text-ink-soft tabular-nums">
                  {w.active_contexts}/{w.max_contexts} · {t("fleet.mem", { m: w.metrics.free_memory_mb ?? "—" })} · {t("fleet.beat", { d: fmtDate(w.last_heartbeat_at) })}
                </span>
              </li>
            ))}
          </ul>
        )}
      </Card>
      <Card title={t("fleet.cooling")}>
        {f.cooling_domains.length === 0 ? <p className="text-sm text-ink-mute">{t("fleet.noCooling")}</p> : (
          <ul className="divide-y divide-line text-sm">
            {f.cooling_domains.map((d) => (
              <li key={d.domain} className="flex justify-between gap-2 py-2">
                <span dir="ltr">{d.domain}</span>
                <span className="text-xs text-ink-soft">{d.last_challenge_kind} ×{d.consecutive_challenges} · {t("challenge.cooldown", { d: fmtDate(d.cooldown_until) })}</span>
              </li>
            ))}
          </ul>
        )}
      </Card>
    </div>
  );
}

function BrowserInner() {
  const { t } = useI18n();
  const router = useRouter();
  const params = useSearchParams();
  const admin = !!useMe().data?.is_platform_admin;
  const tabs = (["missions", "challenges", ...(admin ? ["fleet"] : [])] as const);
  const raw = params.get("tab");
  const tab = tabs.find((x) => x === raw) ?? "missions";
  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-semibold">{t("browser.title")}</h1>
        <p className="mt-1 text-sm text-ink-soft">{t("browser.subtitle")}</p>
      </div>
      <nav className="flex gap-1 border-b border-line" role="tablist">
        {tabs.map((x) => (
          <button key={x} role="tab" aria-selected={tab === x} onClick={() => router.replace(`/browser?tab=${x}`)}
                  className={`-mb-px border-b-2 px-3 py-2 text-sm ${tab === x ? "border-brand font-medium text-brand-strong" : "border-transparent text-ink-soft hover:text-ink"}`}>
            {t(`browser.tab.${x}` as MessageKey)}
          </button>
        ))}
      </nav>
      {tab === "missions" && <Missions />}
      {tab === "challenges" && <Challenges />}
      {tab === "fleet" && <FleetView />}
    </div>
  );
}

export default function BrowserPage() {
  return <Suspense><BrowserInner /></Suspense>;
}
