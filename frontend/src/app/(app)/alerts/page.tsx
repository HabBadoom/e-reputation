"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useState } from "react";
import { BrandSelect, SEVERITIES, SeverityBadge, useAlertText, useBrands } from "@/components/intel";
import { useI18n } from "@/components/providers";
import { Badge, Button, Card, ErrorBanner, Field, fieldErrors, Spinner } from "@/components/ui";
import {
  api, type Alert, type AlertRule, type Channel, type Crisis, type Page, type RoleKey, type RuleKind,
  type Severity, type Webhook,
} from "@/lib/api";
import type { MessageKey } from "@/lib/i18n";
import { useCan } from "@/lib/session";

const TABS = ["feed", "crises", "rules", "webhooks"] as const;
type Tab = (typeof TABS)[number];
const KINDS: RuleKind[] = ["crisis", "negative_spike", "negative_mention", "emerging_topic", "competitor_spike", "connector_failure"];
const CHANNELS: Channel[] = ["in_app", "email", "webhook"];
const ROLES: RoleKey[] = ["owner", "admin", "analyst", "viewer"];

// --------------------------------------------------------------------------------- feed
function Feed() {
  const { t, fmtDate } = useI18n();
  const qc = useQueryClient();
  const canManage = useCan("alerts:manage");
  const text = useAlertText();
  const [status, setStatus] = useState<"" | "open" | "acknowledged" | "resolved">("open");
  const [severity, setSeverity] = useState<"" | Severity>("");
  const q = useQuery({
    queryKey: ["alerts", status, severity],
    queryFn: () => api.get<Page<Alert>>(`/alerts?limit=100${status ? `&status=${status}` : ""}${severity ? `&severity=${severity}` : ""}`),
    refetchInterval: 60_000,
  });
  const refresh = () => { qc.invalidateQueries({ queryKey: ["alerts"] }); qc.invalidateQueries({ queryKey: ["alerts-summary"] }); };
  const act = useMutation({ mutationFn: ({ id, a }: { id: string; a: "acknowledge" | "resolve" }) => api.post(`/alerts/${id}/${a}`), onSuccess: refresh });
  const ackAll = useMutation({ mutationFn: () => api.post("/alerts/acknowledge-all"), onSuccess: refresh });
  const evaluate = useMutation({ mutationFn: () => api.post<{ queued: boolean }>("/alerts/evaluate") });

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-2">
        <select className="field w-44" value={status} onChange={(e) => setStatus(e.target.value as typeof status)} aria-label={t("alerts.status")}>
          <option value="">{t("alerts.allStatuses")}</option>
          {(["open", "acknowledged", "resolved"] as const).map((s) => <option key={s} value={s}>{t(`alerts.st.${s}`)}</option>)}
        </select>
        <select className="field w-44" value={severity} onChange={(e) => setSeverity(e.target.value as typeof severity)} aria-label={t("alerts.severity")}>
          <option value="">{t("alerts.allSeverities")}</option>
          {SEVERITIES.map((s) => <option key={s} value={s}>{t(`sev.${s}`)}</option>)}
        </select>
        {canManage && (
          <div className="ms-auto flex gap-2">
            <Button variant="ghost" disabled={evaluate.isPending} onClick={() => evaluate.mutate()}>
              {evaluate.isSuccess ? t("alerts.evaluating") : t("alerts.evaluate")}
            </Button>
            <Button variant="ghost" disabled={ackAll.isPending} onClick={() => ackAll.mutate()}>{t("alerts.ackAll")}</Button>
          </div>
        )}
      </div>
      <ErrorBanner error={q.error ?? act.error ?? evaluate.error} />
      {q.isLoading ? <Spinner /> : q.data?.items.length === 0 ? (
        <p className="rounded border border-dashed border-line p-6 text-center text-sm text-ink-mute">{t("alerts.none")}</p>
      ) : (
        <ul className="space-y-2">
          {q.data?.items.map((a) => {
            const { title, body } = text(a);
            const url = typeof a.data.url === "string" ? a.data.url : null;
            return (
              <li key={a.id} className={`card p-4 ${a.status === "open" ? "" : "opacity-75"}`}>
                <div className="flex flex-wrap items-start justify-between gap-3">
                  <div className="min-w-0 flex-1 space-y-1">
                    <p className="flex flex-wrap items-center gap-2 text-sm font-semibold">
                      <SeverityBadge s={a.severity} /> {title}
                    </p>
                    <p className="text-sm text-ink-soft">{body}</p>
                    {typeof a.data.mention_title === "string" && a.data.mention_title && (
                      <p className="text-sm italic text-ink-soft" dir="auto">« {a.data.mention_title} »</p>
                    )}
                    <p className="text-xs text-ink-mute">
                      {fmtDate(a.created_at)} · {t(`alerts.st.${a.status}`)}
                      {url && <> · <a href={url} target="_blank" rel="noopener noreferrer nofollow" className="text-brand hover:underline" dir="ltr">{t("alerts.openSource")}</a></>}
                    </p>
                  </div>
                  {canManage && a.status !== "resolved" && (
                    <div className="flex gap-2">
                      {a.status === "open" && <Button variant="ghost" onClick={() => act.mutate({ id: a.id, a: "acknowledge" })}>{t("alerts.ack")}</Button>}
                      <Button variant="ghost" onClick={() => act.mutate({ id: a.id, a: "resolve" })}>{t("alerts.resolve")}</Button>
                    </div>
                  )}
                </div>
              </li>
            );
          })}
        </ul>
      )}
    </div>
  );
}

// ------------------------------------------------------------------------------- crises
function Crises() {
  const { t, fmtDate } = useI18n();
  const q = useQuery({ queryKey: ["crises"], queryFn: () => api.get<Crisis[]>("/crises?limit=100") });
  if (q.isLoading) return <Spinner />;
  return (
    <div className="space-y-4">
      <p className="text-sm text-ink-soft">{t("crises.help")}</p>
      <ErrorBanner error={q.error} />
      {q.data?.length === 0 ? <p className="rounded border border-dashed border-line p-6 text-center text-sm text-ink-mute">{t("crises.none")}</p> : (
        <ul className="space-y-2">
          {q.data?.map((c) => (
            <li key={c.id} className="card p-4 text-sm">
              <div className="flex flex-wrap items-center gap-2">
                <SeverityBadge s={c.severity} />
                <strong>{c.brand}</strong>
                <Badge tone={c.status === "active" ? "neg" : "neutral"}>{t(`crises.${c.status}`)}</Badge>
              </div>
              <p className="mt-2 text-ink-soft">
                {t("crises.period", { from: fmtDate(c.started_at), last: fmtDate(c.last_detected_at) })}
                {c.resolved_at && <> · {t("crises.resolvedAt", { d: fmtDate(c.resolved_at) })}</>}
              </p>
              <p className="mt-1 text-ink-soft">
                {t("crises.metrics", {
                  peak: c.peak_negative, exp: String(c.metrics.expected ?? "—"), d: String(c.metrics.domains ?? "—"),
                  share: Math.round(Number(c.metrics.negative_share ?? 0) * 100),
                  esc: Math.round(Number(c.metrics.escalation_share ?? 0) * 100),
                })}
              </p>
              {c.status === "active" && (
                <p className="mt-2"><Link href="/reports" className="text-brand hover:underline">{t("crises.report")}</Link></p>
              )}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

// -------------------------------------------------------------------------------- rules
function RuleForm({ hooks, onDone }: { hooks: Webhook[]; onDone: () => void }) {
  const { t } = useI18n();
  const [name, setName] = useState("");
  const [kind, setKind] = useState<RuleKind>("crisis");
  const [brand, setBrand] = useState("");
  const [minSev, setMinSev] = useState<Severity>("MEDIUM");
  const [channels, setChannels] = useState<Channel[]>(["in_app"]);
  const [roles, setRoles] = useState<RoleKey[]>(["owner", "admin"]);
  const [hook, setHook] = useState("");
  const create = useMutation({
    mutationFn: () => api.post<AlertRule>("/alert-rules", {
      name: name || t(`rule.kind.${kind}`), kind, brand_id: brand || null, min_severity: minSev, channels,
      notify_roles: roles, webhook_id: channels.includes("webhook") ? hook || null : null,
    }),
    onSuccess: onDone,
  });
  const toggle = <T,>(list: T[], v: T) => (list.includes(v) ? list.filter((x) => x !== v) : [...list, v]);
  const errs = fieldErrors(create.error);
  return (
    <form className="space-y-4" onSubmit={(e) => { e.preventDefault(); create.mutate(); }}>
      <div className="grid gap-4 sm:grid-cols-2">
        <label className="block"><span className="label">{t("rule.kind")}</span>
          <select className="field" value={kind} onChange={(e) => setKind(e.target.value as RuleKind)}>
            {KINDS.map((k) => <option key={k} value={k}>{t(`rule.kind.${k}`)}</option>)}
          </select>
          <span className="mt-1 block text-xs text-ink-mute">{t(`rule.help.${kind}`)}</span>
        </label>
        <Field label={t("rule.name")} value={name} onChange={(e) => setName(e.target.value)} maxLength={200}
               placeholder={t(`rule.kind.${kind}`)} error={errs.name} />
        <label className="block"><span className="label">{t("intel.brand")}</span>
          <BrandSelect value={brand} onChange={setBrand} />
        </label>
        <label className="block"><span className="label">{t("rule.minSeverity")}</span>
          <select className="field" value={minSev} onChange={(e) => setMinSev(e.target.value as Severity)}>
            {SEVERITIES.map((s) => <option key={s} value={s}>{t(`sev.${s}`)}</option>)}
          </select>
        </label>
      </div>
      <fieldset>
        <legend className="label">{t("rule.channels")}</legend>
        <div className="flex flex-wrap gap-4 text-sm">
          {CHANNELS.map((c) => (
            <label key={c} className="inline-flex items-center gap-2">
              <input type="checkbox" checked={channels.includes(c)} disabled={c === "webhook" && hooks.length === 0}
                     onChange={() => setChannels(toggle(channels, c))} /> {t(`rule.ch.${c}`)}
            </label>
          ))}
        </div>
        {hooks.length === 0 && <p className="mt-1 text-xs text-ink-mute">{t("rule.noWebhook")}</p>}
      </fieldset>
      {channels.includes("webhook") && (
        <label className="block"><span className="label">{t("rule.webhook")}</span>
          <select className="field" value={hook} onChange={(e) => setHook(e.target.value)} required>
            <option value="">—</option>
            {hooks.map((h) => <option key={h.id} value={h.id}>{h.name}</option>)}
          </select>
        </label>
      )}
      {channels.includes("email") && (
        <fieldset>
          <legend className="label">{t("rule.roles")}</legend>
          <div className="flex flex-wrap gap-4 text-sm">
            {ROLES.map((r) => (
              <label key={r} className="inline-flex items-center gap-2">
                <input type="checkbox" checked={roles.includes(r)} onChange={() => setRoles(toggle(roles, r))} /> {t(`role.${r}`)}
              </label>
            ))}
          </div>
          <p className="mt-1 text-xs text-ink-mute">{t("rule.rolesHelp")}</p>
        </fieldset>
      )}
      <ErrorBanner error={create.error} />
      <div className="flex gap-2">
        <Button type="submit" disabled={create.isPending || channels.length === 0}>{t("rule.create")}</Button>
        <Button type="button" variant="ghost" onClick={onDone}>{t("common.cancel")}</Button>
      </div>
    </form>
  );
}

function Rules() {
  const { t, fmtDate } = useI18n();
  const qc = useQueryClient();
  const canManage = useCan("alerts:manage");
  const [adding, setAdding] = useState(false);
  const rules = useQuery({ queryKey: ["alert-rules"], queryFn: () => api.get<AlertRule[]>("/alert-rules") });
  const hooks = useQuery({ queryKey: ["webhooks"], queryFn: () => api.get<Webhook[]>("/webhooks"), enabled: canManage });
  const brands = useBrands().data?.items ?? [];
  const refresh = () => qc.invalidateQueries({ queryKey: ["alert-rules"] });
  const defaults = useMutation({ mutationFn: () => api.post("/alert-rules/defaults"), onSuccess: refresh });
  const patch = useMutation({ mutationFn: ({ id, body }: { id: string; body: Partial<AlertRule> }) => api.patch(`/alert-rules/${id}`, body), onSuccess: refresh });
  const del = useMutation({ mutationFn: (id: string) => api.del(`/alert-rules/${id}`), onSuccess: refresh });
  if (rules.isLoading) return <Spinner />;
  const brandName = (id: string | null) => (id ? brands.find((b) => b.id === id)?.name ?? "—" : t("intel.allBrands"));

  return (
    <div className="space-y-4">
      <ErrorBanner error={rules.error ?? defaults.error ?? patch.error ?? del.error} />
      {canManage && !adding && (
        <div className="flex flex-wrap gap-2">
          <Button onClick={() => setAdding(true)}>{t("rule.new")}</Button>
          <Button variant="ghost" disabled={defaults.isPending} onClick={() => defaults.mutate()}>{t("rule.defaults")}</Button>
        </div>
      )}
      {adding && <Card title={t("rule.new")}><RuleForm hooks={hooks.data ?? []} onDone={() => { setAdding(false); refresh(); }} /></Card>}
      {rules.data?.length === 0 ? (
        <p className="rounded border border-dashed border-line p-6 text-center text-sm text-ink-mute">{t("rule.none")}</p>
      ) : (
        <ul className="space-y-2">
          {rules.data?.map((r) => (
            <li key={r.id} className="card flex flex-wrap items-center justify-between gap-3 p-4 text-sm">
              <div className="min-w-0 flex-1">
                <p className="font-semibold">{r.name} {!r.enabled && <Badge>{t("rule.disabled")}</Badge>}</p>
                <p className="mt-1 text-ink-soft">
                  {t(`rule.kind.${r.kind}`)} · {brandName(r.brand_id)} · {t("rule.from", { s: t(`sev.${r.min_severity}`) })} · {r.channels.map((c) => t(`rule.ch.${c}`)).join(", ")}
                </p>
                {r.last_evaluated_at && <p className="mt-1 text-xs text-ink-mute">{t("rule.lastEval", { d: fmtDate(r.last_evaluated_at) })}</p>}
              </div>
              {canManage && (
                <div className="flex items-center gap-2">
                  <select className="field w-36" value={r.min_severity} aria-label={t("rule.minSeverity")}
                          onChange={(e) => patch.mutate({ id: r.id, body: { min_severity: e.target.value as Severity } })}>
                    {SEVERITIES.map((s) => <option key={s} value={s}>{t(`sev.${s}`)}</option>)}
                  </select>
                  <Button variant="ghost" onClick={() => patch.mutate({ id: r.id, body: { enabled: !r.enabled } })}>
                    {r.enabled ? t("rule.pause") : t("rule.enable")}
                  </Button>
                  <Button variant="danger" onClick={() => del.mutate(r.id)}>{t("common.delete")}</Button>
                </div>
              )}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

// ----------------------------------------------------------------------------- webhooks
function Webhooks() {
  const { t, fmtDate } = useI18n();
  const qc = useQueryClient();
  const [name, setName] = useState("");
  const [url, setUrl] = useState("");
  const [secret, setSecret] = useState<string | null>(null);
  const [tested, setTested] = useState<Record<string, string>>({});
  const q = useQuery({ queryKey: ["webhooks"], queryFn: () => api.get<Webhook[]>("/webhooks") });
  const refresh = () => qc.invalidateQueries({ queryKey: ["webhooks"] });
  const create = useMutation({
    mutationFn: () => api.post<Webhook>("/webhooks", { name, url }),
    onSuccess: (h) => { setSecret(h.secret ?? null); setName(""); setUrl(""); refresh(); },
  });
  const rotate = useMutation({ mutationFn: (id: string) => api.post<Webhook>(`/webhooks/${id}/rotate-secret`), onSuccess: (h) => setSecret(h.secret ?? null) });
  const test = useMutation({
    mutationFn: (id: string) => api.post<{ ok: boolean; status: number | null; detail: string }>(`/webhooks/${id}/test`),
    onSuccess: (r, id) => { setTested({ ...tested, [id]: r.ok ? t("hook.testOk", { s: r.status ?? "" }) : t("hook.testFail", { d: r.detail }) }); refresh(); },
  });
  const patch = useMutation({ mutationFn: ({ id, enabled }: { id: string; enabled: boolean }) => api.patch(`/webhooks/${id}`, { enabled }), onSuccess: refresh });
  const del = useMutation({ mutationFn: (id: string) => api.del(`/webhooks/${id}`), onSuccess: () => { refresh(); qc.invalidateQueries({ queryKey: ["alert-rules"] }); } });
  const errs = fieldErrors(create.error);

  return (
    <div className="space-y-4">
      <p className="text-sm text-ink-soft">{t("hook.help")}</p>
      {secret && (
        <div role="status" className="rounded border border-brand/30 bg-brand-tint p-4 text-sm">
          <p className="font-medium text-brand-strong">{t("hook.secretOnce")}</p>
          <code className="mt-2 block break-all rounded bg-paper-raised p-2 font-mono text-xs" dir="ltr">{secret}</code>
          <button className="mt-2 text-xs text-brand underline" onClick={() => setSecret(null)}>{t("hook.secretSaved")}</button>
        </div>
      )}
      <Card title={t("hook.new")}>
        <form className="grid gap-4 sm:grid-cols-[1fr_2fr_auto] sm:items-end" onSubmit={(e) => { e.preventDefault(); create.mutate(); }}>
          <Field label={t("hook.name")} value={name} onChange={(e) => setName(e.target.value)} required maxLength={100} error={errs.name} />
          <Field label={t("hook.url")} value={url} onChange={(e) => setUrl(e.target.value)} required type="url" dir="ltr"
                 placeholder="https://" error={errs.url} />
          <Button type="submit" disabled={create.isPending}>{t("hook.add")}</Button>
        </form>
        <div className="mt-3"><ErrorBanner error={create.error} /></div>
      </Card>
      <ErrorBanner error={q.error ?? rotate.error ?? test.error ?? patch.error ?? del.error} />
      {q.data?.length === 0 ? <p className="text-sm text-ink-mute">{t("hook.none")}</p> : (
        <ul className="space-y-2">
          {q.data?.map((h) => (
            <li key={h.id} className="card p-4 text-sm">
              <div className="flex flex-wrap items-center justify-between gap-3">
                <div className="min-w-0">
                  <p className="font-semibold">{h.name} {!h.enabled && <Badge>{t("rule.disabled")}</Badge>}</p>
                  <p className="truncate font-mono text-xs text-ink-soft" dir="ltr">{h.url}</p>
                  <p className="mt-1 text-xs text-ink-mute">
                    {h.last_delivery_at ? t("hook.last", { d: fmtDate(h.last_delivery_at), s: h.last_status ?? "—" }) : t("hook.never")}
                    {h.last_error && <span className="ms-2 text-signal-neg">{h.last_error}</span>}
                  </p>
                  {tested[h.id] && <p className="mt-1 text-xs">{tested[h.id]}</p>}
                </div>
                <div className="flex flex-wrap gap-2">
                  <Button variant="ghost" disabled={test.isPending} onClick={() => test.mutate(h.id)}>{t("hook.test")}</Button>
                  <Button variant="ghost" onClick={() => patch.mutate({ id: h.id, enabled: !h.enabled })}>{h.enabled ? t("rule.pause") : t("rule.enable")}</Button>
                  <Button variant="ghost" onClick={() => rotate.mutate(h.id)}>{t("hook.rotate")}</Button>
                  <Button variant="danger" onClick={() => del.mutate(h.id)}>{t("common.delete")}</Button>
                </div>
              </div>
            </li>
          ))}
        </ul>
      )}
      <details className="text-sm text-ink-soft">
        <summary className="cursor-pointer font-medium">{t("hook.verifyTitle")}</summary>
        <p className="mt-2">{t("hook.verifyBody")}</p>
        <pre className="mt-2 overflow-x-auto rounded bg-paper-sunk p-3 font-mono text-xs" dir="ltr">{`signature = "sha256=" + HMAC_SHA256(secret, timestamp + "." + raw_body).hex()
compare with header X-RepIntel-Signature (constant time)
reject if |now - X-RepIntel-Timestamp| > 300 s`}</pre>
      </details>
    </div>
  );
}

function AlertsInner() {
  const { t } = useI18n();
  const router = useRouter();
  const params = useSearchParams();
  const canManage = useCan("alerts:manage");
  const tabs = TABS.filter((x) => x !== "webhooks" || canManage);
  const raw = params.get("tab") as Tab | null;
  const tab: Tab = raw && tabs.includes(raw) ? raw : "feed";
  return (
    <div className="space-y-6">
      <h1 className="text-2xl font-semibold">{t("alerts.title")}</h1>
      <nav className="flex gap-1 border-b border-line" role="tablist">
        {tabs.map((x) => (
          <button key={x} role="tab" aria-selected={tab === x} onClick={() => router.replace(`/alerts?tab=${x}`)}
                  className={`-mb-px border-b-2 px-3 py-2 text-sm ${tab === x ? "border-brand font-medium text-brand-strong" : "border-transparent text-ink-soft hover:text-ink"}`}>
            {t(`alerts.tab.${x}` as MessageKey)}
          </button>
        ))}
      </nav>
      {tab === "feed" && <Feed />}
      {tab === "crises" && <Crises />}
      {tab === "rules" && <Rules />}
      {tab === "webhooks" && <Webhooks />}
    </div>
  );
}

export default function AlertsPage() {
  return <Suspense><AlertsInner /></Suspense>;
}
