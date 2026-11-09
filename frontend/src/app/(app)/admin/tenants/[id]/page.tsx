"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import Link from "next/link";
import { useParams } from "next/navigation";
import { useEffect, useState } from "react";
import { useI18n } from "@/components/providers";
import { Badge, Button, Card, ErrorBanner, Spinner } from "@/components/ui";
import { api, type AdminInvoice, type AdminPlan, type AdminSubscription, type AdminTenantDetail } from "@/lib/api";
import { fmtDay, fmtMoney, invoiceTone, parseMoney } from "@/lib/billing";
import type { MessageKey } from "@/lib/i18n";

const STATUSES = ["trialing", "active", "past_due", "canceled", "expired"] as const;
const METHODS = ["bank_transfer", "ccp", "cheque", "cash", "card", "other"] as const;
const day = (iso: string | null) => (iso ? iso.slice(0, 10) : "");
const toIso = (d: string) => (d ? new Date(`${d}T00:00:00Z`).toISOString() : null);

function SubscriptionCard({ id, sub }: { id: string; sub: AdminSubscription }) {
  const { t } = useI18n();
  const qc = useQueryClient();
  const plans = useQuery({ queryKey: ["admin", "plans"], queryFn: () => api.get<AdminPlan[]>("/admin/plans") });
  const init = () => ({ plan_key: sub.plan_key, status: sub.status, trial_ends_at: day(sub.trial_ends_at),
    current_period_start: day(sub.current_period_start), current_period_end: day(sub.current_period_end),
    billing_interval: sub.billing_interval ?? "month", currency: sub.currency ?? "DZD", notes: sub.notes });
  const [f, setF] = useState(init);
  useEffect(() => setF(init()), [sub]); // eslint-disable-line react-hooks/exhaustive-deps
  const save = useMutation({
    mutationFn: () => api.patch(`/admin/tenants/${id}/subscription`, {
      ...f, trial_ends_at: toIso(f.trial_ends_at), current_period_start: toIso(f.current_period_start),
      current_period_end: toIso(f.current_period_end) }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["admin"] }),
  });
  return (
    <Card title={t("adm.subscription")}>
      <form className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4" onSubmit={(e) => { e.preventDefault(); save.mutate(); }}>
        <label className="block"><span className="label">{t("adm.col.plan")}</span>
          <select className="field" value={f.plan_key} onChange={(e) => setF({ ...f, plan_key: e.target.value })}>
            {(plans.data ?? []).map((p) => <option key={p.key} value={p.key}>{p.name}{p.is_active ? "" : ` (${t("adm.inactive")})`}</option>)}
          </select></label>
        <label className="block"><span className="label">{t("adm.col.status")}</span>
          <select className="field" value={f.status} onChange={(e) => setF({ ...f, status: e.target.value as AdminSubscription["status"] })}>
            {STATUSES.map((s) => <option key={s} value={s}>{t(`bill.st.${s}`)}</option>)}
          </select></label>
        <label className="block"><span className="label">{t("bill.interval")}</span>
          <select className="field" value={f.billing_interval} onChange={(e) => setF({ ...f, billing_interval: e.target.value as "month" | "year" })}>
            <option value="month">{t("bill.monthly")}</option><option value="year">{t("bill.yearly")}</option>
          </select></label>
        <label className="block"><span className="label">{t("bill.f.currency")}</span>
          <select className="field" value={f.currency} onChange={(e) => setF({ ...f, currency: e.target.value })}>
            {["DZD", "EUR", "USD"].map((c) => <option key={c}>{c}</option>)}
          </select></label>
        <label className="block"><span className="label">{t("adm.trialEnd")}</span>
          <input type="date" className="field" value={f.trial_ends_at} onChange={(e) => setF({ ...f, trial_ends_at: e.target.value })} /></label>
        <label className="block"><span className="label">{t("adm.periodStart")}</span>
          <input type="date" className="field" value={f.current_period_start} onChange={(e) => setF({ ...f, current_period_start: e.target.value })} /></label>
        <label className="block"><span className="label">{t("adm.periodEnd")}</span>
          <input type="date" className="field" value={f.current_period_end} onChange={(e) => setF({ ...f, current_period_end: e.target.value })} /></label>
        <label className="block"><span className="label">{t("adm.notes")}</span>
          <input className="field" value={f.notes} onChange={(e) => setF({ ...f, notes: e.target.value })} /></label>
        <div className="flex items-center gap-3 sm:col-span-2 lg:col-span-4">
          <Button type="submit" disabled={save.isPending}>{t("common.save")}</Button>
          {save.isSuccess && <span className="text-sm text-brand">{t("common.saved")}</span>}
          <span className="text-xs text-ink-mute">{t("adm.auditNote")}</span>
        </div>
      </form>
      <ErrorBanner error={save.error ?? plans.error} />
    </Card>
  );
}

type Line = { description: string; quantity: string; unit: string };
const emptyLine: Line = { description: "", quantity: "1", unit: "" };

function InvoiceEditor({ id, draft, onDone }: { id: string; draft?: AdminInvoice; onDone: () => void }) {
  const { t } = useI18n();
  const qc = useQueryClient();
  const [lines, setLines] = useState<Line[]>(draft ? draft.lines.map((l) => ({
    description: l.description, quantity: String(l.quantity), unit: (l.unit_minor / 100).toFixed(2) })) : [emptyLine]);
  const [meta, setMeta] = useState({ currency: draft?.currency ?? "DZD", locale: draft?.locale ?? "fr",
    tax_rate: draft ? String(draft.tax_rate) : "", stamp: draft ? (draft.stamp_duty_minor / 100).toFixed(2) : "",
    period_start: draft?.period_start ?? "", period_end: draft?.period_end ?? "", notes: draft?.notes ?? "" });
  const valid = lines.length > 0 && lines.every((l) => l.description.trim() && Number(l.quantity) > 0 && parseMoney(l.unit) !== null && l.unit);
  const body = () => ({
    currency: meta.currency, locale: meta.locale, notes: meta.notes,
    tax_rate: meta.tax_rate === "" ? null : Number(meta.tax_rate.replace(",", ".")),
    stamp_duty_minor: meta.stamp ? parseMoney(meta.stamp) ?? 0 : 0,
    period_start: meta.period_start || null, period_end: meta.period_end || null,
    lines: lines.map((l) => ({ description: l.description, quantity: Number(l.quantity.replace(",", ".")), unit_minor: parseMoney(l.unit) })),
  });
  const save = useMutation({
    mutationFn: () => draft ? api.put(`/admin/tenants/${id}/invoices/${draft.id}`, body()) : api.post(`/admin/tenants/${id}/invoices`, body()),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["admin"] }); onDone(); },
  });
  const set = (i: number, patch: Partial<Line>) => setLines(lines.map((l, j) => (j === i ? { ...l, ...patch } : l)));
  return (
    <form className="space-y-4 rounded border border-line p-4" onSubmit={(e) => { e.preventDefault(); save.mutate(); }}>
      <div className="space-y-2">
        {lines.map((l, i) => (
          <div key={i} className="grid gap-2 sm:grid-cols-[1fr_80px_140px_auto]">
            <input className="field" placeholder={t("adm.lineDesc")} value={l.description} onChange={(e) => set(i, { description: e.target.value })} />
            <input className="field" inputMode="decimal" aria-label={t("adm.qty")} value={l.quantity} onChange={(e) => set(i, { quantity: e.target.value })} />
            <input className="field" inputMode="decimal" placeholder={t("adm.unitHt")} value={l.unit} onChange={(e) => set(i, { unit: e.target.value })} />
            <button type="button" className="text-xs text-signal-neg hover:underline" disabled={lines.length === 1}
                    onClick={() => setLines(lines.filter((_, j) => j !== i))}>{t("common.delete")}</button>
          </div>
        ))}
        <button type="button" className="text-sm text-brand hover:underline" onClick={() => setLines([...lines, emptyLine])}>+ {t("adm.addLine")}</button>
      </div>
      <div className="grid gap-3 sm:grid-cols-3 lg:grid-cols-6">
        <label className="block"><span className="label">{t("bill.f.currency")}</span>
          <select className="field" value={meta.currency} onChange={(e) => setMeta({ ...meta, currency: e.target.value })}>
            {["DZD", "EUR", "USD"].map((c) => <option key={c}>{c}</option>)}</select></label>
        <label className="block"><span className="label">{t("adm.lang")}</span>
          <select className="field" value={meta.locale} onChange={(e) => setMeta({ ...meta, locale: e.target.value })}>
            {(["fr", "ar", "en"] as const).map((l) => <option key={l} value={l}>{t(`lang.${l}`)}</option>)}</select></label>
        <label className="block"><span className="label">{t("adm.vat")}</span>
          <input className="field" inputMode="decimal" placeholder={t("adm.vatDefault")} value={meta.tax_rate} onChange={(e) => setMeta({ ...meta, tax_rate: e.target.value })} /></label>
        <label className="block"><span className="label">{t("adm.stamp")}</span>
          <input className="field" inputMode="decimal" value={meta.stamp} onChange={(e) => setMeta({ ...meta, stamp: e.target.value })} /></label>
        <label className="block"><span className="label">{t("adm.periodStart")}</span>
          <input type="date" className="field" value={meta.period_start} onChange={(e) => setMeta({ ...meta, period_start: e.target.value })} /></label>
        <label className="block"><span className="label">{t("adm.periodEnd")}</span>
          <input type="date" className="field" value={meta.period_end} onChange={(e) => setMeta({ ...meta, period_end: e.target.value })} /></label>
      </div>
      <label className="block"><span className="label">{t("adm.notes")}</span>
        <input className="field" value={meta.notes} onChange={(e) => setMeta({ ...meta, notes: e.target.value })} /></label>
      <ErrorBanner error={save.error} />
      <div className="flex gap-2">
        <Button type="submit" disabled={!valid || save.isPending}>{t("adm.saveDraft")}</Button>
        <Button type="button" variant="ghost" onClick={onDone}>{t("common.cancel")}</Button>
      </div>
    </form>
  );
}

function InvoiceRow({ id, inv, missing }: { id: string; inv: AdminInvoice; missing: boolean }) {
  const { t, locale } = useI18n();
  const qc = useQueryClient();
  const [mode, setMode] = useState<"" | "edit" | "pay" | "void">("");
  const [pay, setPay] = useState({ method: "bank_transfer", reference: "", paid_at: "" });
  const [reason, setReason] = useState("");
  const base = `/admin/tenants/${id}/invoices/${inv.id}`;
  const done = () => { setMode(""); qc.invalidateQueries({ queryKey: ["admin"] }); };
  const issue = useMutation({ mutationFn: () => api.post(`${base}/issue`), onSuccess: done });
  const paid = useMutation({ mutationFn: () => api.post(`${base}/paid`, { ...pay, paid_at: toIso(pay.paid_at) }), onSuccess: done });
  const voidIt = useMutation({ mutationFn: () => api.post(`${base}/void`, { reason }), onSuccess: done });
  return (
    <li className="py-3">
      <div className="flex flex-wrap items-center gap-3 text-sm">
        <span className="font-mono text-xs" dir="ltr">{inv.number ?? t("adm.draftNo")}</span>
        <Badge tone={invoiceTone(inv.status)}>{t(`bill.inv.${inv.status}` as MessageKey)}</Badge>
        <span className="tabular-nums font-medium">{fmtMoney(inv.total_minor, inv.currency, locale)}</span>
        <span className="text-ink-mute">{inv.issue_date ? `${fmtDay(inv.issue_date, locale)} → ${fmtDay(inv.due_date, locale)}` : fmtDay(inv.created_at, locale)}</span>
        <span className="ms-auto flex flex-wrap gap-3">
          <a className="text-brand hover:underline" href={`/api/v1${base}/pdf`}>{inv.status === "draft" ? t("adm.preview") : "PDF"}</a>
          {inv.status === "draft" && <>
            <button className="text-brand hover:underline" onClick={() => setMode(mode === "edit" ? "" : "edit")}>{t("adm.edit")}</button>
            <button className="font-medium text-brand hover:underline disabled:text-ink-mute" disabled={missing || issue.isPending}
                    title={missing ? t("adm.missingBuyer") : undefined}
                    onClick={() => { if (confirm(t("adm.issueConfirm"))) issue.mutate(); }}>{t("adm.issue")}</button>
            <button className="text-signal-neg hover:underline" onClick={() => voidIt.mutate()}>{t("common.delete")}</button>
          </>}
          {inv.status === "issued" && <>
            <button className="text-brand hover:underline" onClick={() => setMode(mode === "pay" ? "" : "pay")}>{t("adm.markPaid")}</button>
            <button className="text-signal-neg hover:underline" onClick={() => setMode(mode === "void" ? "" : "void")}>{t("adm.void")}</button>
          </>}
        </span>
      </div>
      <ul className="mt-1 text-xs text-ink-soft">
        {inv.lines.map((l, i) => <li key={i}>{l.description} — {l.quantity} × {fmtMoney(l.unit_minor, inv.currency, locale)}</li>)}
      </ul>
      {inv.status === "paid" && inv.paid_at && <p className="mt-1 text-xs text-ink-mute">{t("adm.paidOn", { date: fmtDay(inv.paid_at, locale), method: t(`adm.pm.${inv.payment_method ?? "other"}` as MessageKey) })}{inv.payment_reference ? ` · ${inv.payment_reference}` : ""}</p>}
      {inv.status === "void" && <p className="mt-1 text-xs text-ink-mute">{t("adm.voidedFor", { reason: inv.void_reason ?? "" })}</p>}
      <ErrorBanner error={issue.error ?? paid.error ?? voidIt.error} />
      {mode === "edit" && <div className="mt-3"><InvoiceEditor id={id} draft={inv} onDone={() => setMode("")} /></div>}
      {mode === "pay" && (
        <form className="mt-3 flex flex-wrap gap-2" onSubmit={(e) => { e.preventDefault(); paid.mutate(); }}>
          <select className="field !w-auto" value={pay.method} onChange={(e) => setPay({ ...pay, method: e.target.value })}>
            {METHODS.map((m) => <option key={m} value={m}>{t(`adm.pm.${m}`)}</option>)}</select>
          <input className="field !w-48" placeholder={t("adm.reference")} value={pay.reference} onChange={(e) => setPay({ ...pay, reference: e.target.value })} />
          <input type="date" className="field !w-auto" value={pay.paid_at} aria-label={t("adm.paidAt")} onChange={(e) => setPay({ ...pay, paid_at: e.target.value })} />
          <Button type="submit" variant="ghost" disabled={paid.isPending}>{t("adm.markPaid")}</Button>
        </form>
      )}
      {mode === "void" && (
        <form className="mt-3 flex flex-wrap gap-2" onSubmit={(e) => { e.preventDefault(); voidIt.mutate(); }}>
          <input className="field !w-72" placeholder={t("adm.voidReason")} value={reason} onChange={(e) => setReason(e.target.value)} />
          <Button type="submit" variant="danger" disabled={reason.trim().length < 3 || voidIt.isPending}>{t("adm.void")}</Button>
        </form>
      )}
    </li>
  );
}

function InvoicesCard({ d }: { d: AdminTenantDetail }) {
  const { t } = useI18n();
  const qc = useQueryClient();
  const [editing, setEditing] = useState(false);
  const fromSub = useMutation({
    mutationFn: () => api.post(`/admin/tenants/${d.id}/invoices`, { from_subscription: true }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["admin"] }),
  });
  return (
    <Card title={t("bill.invoices")} action={
      <div className="flex gap-2">
        <Button variant="ghost" disabled={fromSub.isPending} onClick={() => fromSub.mutate()}>{t("adm.fromSub")}</Button>
        <Button variant="ghost" onClick={() => setEditing(true)}>{t("adm.newInvoice")}</Button>
      </div>}>
      <ErrorBanner error={fromSub.error} />
      {d.missing_billing_fields.length > 0 && (
        <p className="mb-3 rounded border border-signal-warn/30 bg-signal-warn/10 px-3 py-2 text-sm text-signal-warn">
          {t("adm.missingBuyerList", { fields: d.missing_billing_fields.map((f) => t(`bill.f.${f}` as MessageKey)).join(", ") })}
        </p>
      )}
      {editing && <InvoiceEditor id={d.id} onDone={() => setEditing(false)} />}
      {d.invoices.length === 0 && !editing ? <p className="text-sm text-ink-mute">{t("bill.noInvoices")}</p> : (
        <ul className="divide-y divide-line">
          {d.invoices.map((i) => <InvoiceRow key={i.id} id={d.id} inv={i} missing={d.missing_billing_fields.length > 0} />)}
        </ul>
      )}
      <p className="mt-3 text-xs text-ink-mute">{t("adm.invoiceLegal")}</p>
    </Card>
  );
}

function RequestsCard({ d }: { d: AdminTenantDetail }) {
  const { t, fmtDate } = useI18n();
  const qc = useQueryClient();
  const decide = useMutation({
    mutationFn: (v: { rid: string; status: "done" | "declined" }) => api.post(`/admin/tenants/${d.id}/requests/${v.rid}`, { status: v.status }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["admin"] }),
  });
  if (d.requests.length === 0) return null;
  return (
    <Card title={t("adm.planRequests")}>
      <ul className="divide-y divide-line text-sm">
        {d.requests.map((r) => (
          <li key={r.id} className="flex flex-wrap items-center gap-3 py-2">
            <span className="font-medium">{r.plan_key}</span>
            <span className="text-ink-soft">{t(r.billing_interval === "year" ? "bill.yearly" : "bill.monthly")}</span>
            <Badge tone={r.status === "pending" ? "warn" : "neutral"}>{t(`adm.req.${r.status}` as MessageKey)}</Badge>
            <span className="text-xs text-ink-mute">{fmtDate(r.created_at)}</span>
            {r.message && <span className="w-full text-ink-soft">“{r.message}”</span>}
            {r.status === "pending" && (
              <span className="ms-auto flex gap-3">
                <button className="text-brand hover:underline" onClick={() => decide.mutate({ rid: r.id, status: "done" })}>{t("adm.markDone")}</button>
                <button className="text-signal-neg hover:underline" onClick={() => decide.mutate({ rid: r.id, status: "declined" })}>{t("adm.decline")}</button>
              </span>
            )}
          </li>
        ))}
      </ul>
      <p className="mt-2 text-xs text-ink-mute">{t("adm.requestsHelp")}</p>
      <ErrorBanner error={decide.error} />
    </Card>
  );
}

export default function TenantAdminPage() {
  const { t, locale } = useI18n();
  const { id } = useParams<{ id: string }>();
  const q = useQuery({ queryKey: ["admin", "tenant", id], queryFn: () => api.get<AdminTenantDetail>(`/admin/tenants/${id}`) });
  if (q.error) return <ErrorBanner error={q.error} />;
  if (!q.data) return <Spinner />;
  const d = q.data;
  const acct = d.billing_account;
  const usageKeys = ["mentions_collected", "search_queries", "ai_calls", "ai_tokens", "reports_generated", "browser_missions", "agent_tasks", "emails_sent"];
  return (
    <div className="space-y-6">
      <div>
        <Link href="/admin" className="text-sm text-brand hover:underline">← {t("adm.title")}</Link>
        <h1 className="mt-1 text-xl font-semibold">{d.name}</h1>
        <p className="text-sm text-ink-mute"><code>{d.slug}</code> · {fmtDay(d.created_at, locale)}</p>
      </div>
      <SubscriptionCard id={d.id} sub={d.subscription} />
      <RequestsCard d={d} />
      <div className="grid gap-6 lg:grid-cols-2">
        <Card title={t("adm.usageMonth")}>
          <dl className="grid grid-cols-2 gap-3 text-sm">
            {usageKeys.map((k) => (
              <div key={k}><dt className="text-xs text-ink-mute">{t(`adm.u.${k}` as MessageKey)}</dt>
                <dd className="tabular-nums font-medium">{(d.usage_month[k] ?? 0).toLocaleString()}</dd></div>
            ))}
          </dl>
        </Card>
        <Card title={t("bill.account")}>
          {Object.keys(acct).length === 0 ? <p className="text-sm text-ink-mute">{t("adm.noAccount")}</p> : (
            <dl className="grid grid-cols-2 gap-2 text-sm">
              {(["legal_name", "address", "city", "email", "phone", "rc", "nif", "nis", "article_imposition", "vat_number"] as const).map((k) => (
                <div key={k}><dt className="text-xs text-ink-mute">{t(`bill.f.${k}`)}</dt><dd className="break-words">{acct[k] || "—"}</dd></div>
              ))}
            </dl>
          )}
        </Card>
      </div>
      <InvoicesCard d={d} />
    </div>
  );
}
