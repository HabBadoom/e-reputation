"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { useI18n } from "@/components/providers";
import { Badge, Button, Card, ErrorBanner, Field, fieldErrors, Spinner } from "@/components/ui";
import { api, type BillingAccount, type BillingOverview, type PublicPlan, type SubscriptionStatus } from "@/lib/api";
import type { MessageKey } from "@/lib/i18n";
import { fmtDay, fmtMoney, invoiceTone, USAGE_ROWS } from "@/lib/billing";
import { useCan } from "@/lib/session";

const ACCOUNT_FIELDS: (keyof BillingAccount)[] = [
  "legal_name", "address", "city", "postal_code", "email", "phone", "rc", "nif", "nis",
  "article_imposition", "vat_number",
];
const statusTone = (s: SubscriptionStatus) =>
  s === "active" ? "brand" : s === "trialing" ? "neutral" : s === "past_due" ? "warn" : "neg";

function UsageBar({ label, used, limit }: { label: string; used: number; limit: number | null | undefined }) {
  const { t } = useI18n();
  const pct = limit ? Math.min(100, Math.round((used / limit) * 100)) : 0;
  return (
    <li>
      <div className="mb-1 flex justify-between gap-3 text-sm">
        <span>{label}</span>
        <span className="tabular-nums text-ink-soft">
          {used.toLocaleString()} / {limit === null || limit === undefined ? t("plan.unlimited") : limit.toLocaleString()}
        </span>
      </div>
      {limit !== null && limit !== undefined && (
        <div className="h-1.5 rounded bg-paper-sunk">
          <div className={`h-1.5 rounded ${pct >= 100 ? "bg-signal-neg" : pct >= 80 ? "bg-signal-warn" : "bg-brand"}`}
               style={{ width: `${limit === 0 ? 100 : pct}%` }} />
        </div>
      )}
    </li>
  );
}

function PlanUsageCard({ b }: { b: BillingOverview }) {
  const { t, locale } = useI18n();
  return (
    <Card title={t("bill.current")}>
      <p className="mb-4 flex flex-wrap items-center gap-2 text-sm">
        <span className="text-base font-semibold">{b.plan.name}</span>
        <Badge tone={statusTone(b.plan.status)}>{t(`bill.st.${b.plan.status}` as MessageKey)}</Badge>
        {b.plan.trial_ends_at && b.plan.status === "trialing" && (
          <span className="text-ink-mute">{t("plan.trialEnds", { date: fmtDay(b.plan.trial_ends_at, locale) })}</span>
        )}
      </p>
      <ul className="grid gap-x-8 gap-y-3 md:grid-cols-2">
        {USAGE_ROWS.map((k) => (
          <UsageBar key={k} label={t(`plan.${k}` as MessageKey)} used={b.usage[k] ?? 0} limit={b.plan.limits[k]} />
        ))}
      </ul>
      <p className="mt-4 text-xs text-ink-mute">
        {t("bill.monthNote", { calls: (b.usage_month.ai_calls ?? 0).toLocaleString(), tokens: (b.usage_month.ai_tokens ?? 0).toLocaleString(), queries: (b.usage_month.search_queries ?? 0).toLocaleString() })}
      </p>
    </Card>
  );
}

function PlanCard({ p, current, pending, canManage, onRequest }: {
  p: PublicPlan; current: boolean; pending: boolean; canManage: boolean;
  onRequest: (key: string, interval: "month" | "year") => void;
}) {
  const { t, locale } = useI18n();
  const [interval, setInterval] = useState<"month" | "year">("month");
  const highlights = ["max_brands", "max_mentions_per_month", "max_ai_calls_per_month", "max_members"] as const;
  return (
    <div className={`flex flex-col rounded border p-4 ${current ? "border-brand bg-brand-tint/40" : "border-line"}`}>
      <div className="flex items-center justify-between gap-2">
        <h3 className="font-semibold">{p.name}</h3>
        {current && <Badge tone="brand">{t("bill.yourPlan")}</Badge>}
      </div>
      {p.description && <p className="mt-1 text-sm text-ink-soft">{p.description}</p>}
      <div className="mt-3 space-y-0.5 text-sm">
        {p.prices.length === 0 ? <p className="text-ink-mute">{t("bill.priceOnRequest")}</p> : p.prices.map((x) => (
          <p key={`${x.currency}-${x.billing_interval}`} className="tabular-nums">
            <span className="font-medium">{fmtMoney(x.amount_minor, x.currency, locale)}</span>
            <span className="text-ink-mute"> {t(`bill.per.${x.billing_interval}`)} {t("bill.exVat")}</span>
          </p>
        ))}
      </div>
      <ul className="mt-3 flex-1 space-y-1 text-xs text-ink-soft">
        {highlights.map((k) => (
          <li key={k} className="flex justify-between gap-2">
            <span>{t(`plan.${k}` as MessageKey)}</span>
            <span className="tabular-nums">{p.limits[k] === null || p.limits[k] === undefined ? t("plan.unlimited") : p.limits[k]!.toLocaleString()}</span>
          </li>
        ))}
      </ul>
      {canManage && !current && (
        <div className="mt-4 flex gap-2">
          <select className="field !w-auto" value={interval} aria-label={t("bill.interval")}
                  onChange={(e) => setInterval(e.target.value as "month" | "year")}>
            <option value="month">{t("bill.monthly")}</option>
            <option value="year">{t("bill.yearly")}</option>
          </select>
          <Button variant="ghost" disabled={pending} onClick={() => onRequest(p.key, interval)}>{t("bill.request")}</Button>
        </div>
      )}
    </div>
  );
}

function PlansCard({ b, canManage }: { b: BillingOverview; canManage: boolean }) {
  const { t, fmtDate } = useI18n();
  const qc = useQueryClient();
  const refresh = () => qc.invalidateQueries({ queryKey: ["billing"] });
  const request = useMutation({
    mutationFn: (v: { plan_key: string; billing_interval: string }) => api.post("/billing/plan-request", v),
    onSuccess: refresh,
  });
  const cancel = useMutation({ mutationFn: () => api.del("/billing/plan-request"), onSuccess: refresh });
  const pendingPlan = b.plans.find((p) => p.key === b.pending_request?.plan_key);
  return (
    <Card title={t("bill.plans")}>
      <p className="mb-4 text-sm text-ink-soft">{t("bill.manualHelp")}</p>
      {b.pending_request && (
        <div role="status" className="mb-4 flex flex-wrap items-center justify-between gap-2 rounded border border-signal-warn/30 bg-signal-warn/10 px-3 py-2 text-sm">
          <span>{t("bill.pending", { plan: pendingPlan?.name ?? b.pending_request.plan_key, date: fmtDate(b.pending_request.created_at) })}</span>
          {canManage && <button className="font-medium underline" disabled={cancel.isPending} onClick={() => cancel.mutate()}>{t("bill.cancelRequest")}</button>}
        </div>
      )}
      <ErrorBanner error={request.error ?? cancel.error} />
      {b.plans.length === 0 ? (
        <p className="text-sm text-ink-mute">{t("bill.noPlans")}</p>
      ) : (
        <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
          {b.plans.map((p) => (
            <PlanCard key={p.key} p={p} current={p.key === b.plan.key} canManage={canManage}
                      pending={!!b.pending_request || request.isPending}
                      onRequest={(plan_key, billing_interval) => request.mutate({ plan_key, billing_interval })} />
          ))}
        </div>
      )}
    </Card>
  );
}

function AccountCard({ b, canManage }: { b: BillingOverview; canManage: boolean }) {
  const { t } = useI18n();
  const qc = useQueryClient();
  const [form, setForm] = useState<BillingAccount>(b.account);
  useEffect(() => setForm(b.account), [b.account]);
  const save = useMutation({
    mutationFn: () => api.put("/billing/account", form),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["billing"] }),
  });
  const errs = fieldErrors(save.error);
  return (
    <Card title={t("bill.account")}>
      <p className="mb-3 text-sm text-ink-soft">{t("bill.accountHelp")}</p>
      {b.missing_fields.length > 0 && (
        <p role="status" className="mb-4 rounded border border-signal-warn/30 bg-signal-warn/10 px-3 py-2 text-sm text-signal-warn">
          {t("bill.missing", { fields: b.missing_fields.map((f) => t(`bill.f.${f}` as MessageKey)).join(", ") })}
        </p>
      )}
      <form className="space-y-4" onSubmit={(e) => { e.preventDefault(); save.mutate(); }}>
        <fieldset disabled={!canManage || save.isPending} className="grid gap-4 sm:grid-cols-2">
          {ACCOUNT_FIELDS.map((f) => (
            <Field key={f} label={t(`bill.f.${f}` as MessageKey)} value={form[f] ?? ""} error={errs[f]}
                   dir={["rc", "nif", "nis", "article_imposition", "vat_number", "email", "phone"].includes(f) ? "ltr" : undefined}
                   onChange={(e) => setForm({ ...form, [f]: e.target.value })} />
          ))}
          <label className="block"><span className="label">{t("bill.f.currency")}</span>
            <select className="field" value={form.currency} onChange={(e) => setForm({ ...form, currency: e.target.value })}>
              {["DZD", "EUR", "USD"].map((c) => <option key={c}>{c}</option>)}
            </select>
          </label>
        </fieldset>
        <ErrorBanner error={save.error && Object.keys(errs).length === 0 ? save.error : null} />
        {canManage && (
          <div className="flex items-center gap-3">
            <Button type="submit" disabled={save.isPending}>{t("common.save")}</Button>
            {save.isSuccess && <span className="text-sm text-brand">{t("common.saved")}</span>}
          </div>
        )}
      </form>
    </Card>
  );
}

function InvoicesCard({ b }: { b: BillingOverview }) {
  const { t, locale } = useI18n();
  return (
    <Card title={t("bill.invoices")}>
      {b.invoices.length === 0 ? <p className="text-sm text-ink-mute">{t("bill.noInvoices")}</p> : (
        <div className="-mx-5 overflow-x-auto">
          <table className="w-full text-sm">
            <thead className="text-start text-xs text-ink-mute">
              <tr className="border-b border-line">
                {["number", "date", "due", "total", "status"].map((h) => <th key={h} className="px-5 py-2 text-start font-medium">{t(`bill.col.${h}` as MessageKey)}</th>)}
                <th />
              </tr>
            </thead>
            <tbody>
              {b.invoices.map((i) => (
                <tr key={i.id} className="border-b border-line last:border-0">
                  <td className="px-5 py-2 font-mono text-xs" dir="ltr">{i.number}</td>
                  <td className="px-5 py-2">{fmtDay(i.issue_date, locale)}</td>
                  <td className="px-5 py-2">{fmtDay(i.due_date, locale)}</td>
                  <td className="px-5 py-2 tabular-nums">{fmtMoney(i.total_minor, i.currency, locale)}</td>
                  <td className="px-5 py-2"><Badge tone={invoiceTone(i.status)}>{t(`bill.inv.${i.status}` as MessageKey)}</Badge></td>
                  <td className="px-5 py-2 text-end">
                    <a className="text-brand hover:underline" href={`/api/v1/billing/invoices/${i.id}/pdf`}>PDF</a>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </Card>
  );
}

export default function BillingPage() {
  const { t } = useI18n();
  const canManage = useCan("billing:manage");
  const q = useQuery({ queryKey: ["billing"], queryFn: () => api.get<BillingOverview>("/billing") });
  if (q.error) return <ErrorBanner error={q.error} />;
  if (!q.data) return <Spinner />;
  const b = q.data;
  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-xl font-semibold">{t("bill.title")}</h1>
        <p className="mt-1 text-sm text-ink-soft">{t("bill.subtitle")}</p>
      </div>
      {!b.plan.active && (
        <div role="alert" className="rounded border border-signal-neg/30 bg-signal-neg/5 px-4 py-3 text-sm text-signal-neg">
          {t("bill.inactive")}
        </div>
      )}
      <PlanUsageCard b={b} />
      <PlansCard b={b} canManage={canManage} />
      <InvoicesCard b={b} />
      <AccountCard b={b} canManage={canManage} />
    </div>
  );
}
