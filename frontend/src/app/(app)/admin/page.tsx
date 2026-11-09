"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import Link from "next/link";
import { useState } from "react";
import { useI18n } from "@/components/providers";
import { Badge, Button, Card, ErrorBanner, Spinner } from "@/components/ui";
import { api, type AdminPlan, type AdminTenantRow, type ConnectorReview, type PlatformStats } from "@/lib/api";
import { fmtDay, fmtMoney, parseMoney } from "@/lib/billing";
import type { MessageKey } from "@/lib/i18n";

const ENTITLEMENTS = [
  "max_brands", "max_monitoring_projects", "max_members", "min_frequency_minutes",
  "max_competitors_per_brand", "max_keywords_per_brand", "max_browser_missions_per_day",
  "max_ai_tasks_per_day", "max_mentions_per_month", "max_ai_calls_per_month",
  "max_reports_per_month", "max_webhooks", "max_concurrent_browser_missions", "max_retention_days",
] as const;
type Tab = "orgs" | "plans" | "connectors";

function money(m: Record<string, number> | null, locale: "fr" | "ar" | "en") {
  const e = Object.entries(m ?? {});
  return e.length ? e.map(([c, v]) => fmtMoney(v, c, locale)).join(" · ") : "—";
}

function Overview() {
  const { t, locale } = useI18n();
  const s = useQuery({ queryKey: ["admin", "stats"], queryFn: () => api.get<PlatformStats>("/admin/stats") });
  const rows = useQuery({ queryKey: ["admin", "tenants"], queryFn: () => api.get<AdminTenantRow[]>("/admin/tenants") });
  const [q, setQ] = useState("");
  if (s.error || rows.error) return <ErrorBanner error={s.error ?? rows.error} />;
  if (!s.data || !rows.data) return <Spinner />;
  const st = s.data;
  const tiles: [string, string][] = [
    [t("adm.tenants"), String(st.tenants)],
    [t("adm.users"), String(st.users)],
    [t("adm.active"), String((st.subscriptions?.active ?? 0) + (st.subscriptions?.past_due ?? 0))],
    [t("adm.trialing"), String(st.subscriptions?.trialing ?? 0)],
    [t("adm.requests"), String(st.pending_requests)],
    [t("adm.outstanding"), money(st.outstanding, locale)],
    [t("adm.paid30"), money(st.paid_last_30d, locale)],
    [t("adm.mentionsMonth"), (st.usage_month?.mentions_collected ?? 0).toLocaleString()],
  ];
  const list = rows.data.filter((r) => !q || `${r.name} ${r.slug}`.toLowerCase().includes(q.toLowerCase()));
  return (
    <div className="space-y-6">
      <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
        {tiles.map(([k, v]) => (
          <div key={k} className="card p-4">
            <p className="text-xs text-ink-mute">{k}</p>
            <p className="mt-1 text-lg font-semibold tabular-nums">{v}</p>
          </div>
        ))}
      </div>
      <Card title={t("adm.orgs")} action={
        <input className="field !w-48" placeholder={t("common.search")} value={q} onChange={(e) => setQ(e.target.value)} />}>
        <div className="-mx-5 -my-5 overflow-x-auto">
          <table className="w-full text-sm">
            <thead className="text-xs text-ink-mute">
              <tr className="border-b border-line">
                {["org", "plan", "status", "members", "brands", "mentions", "ai", "invoices"].map((h) =>
                  <th key={h} className="px-4 py-2 text-start font-medium">{t(`adm.col.${h}` as MessageKey)}</th>)}
              </tr>
            </thead>
            <tbody>
              {list.map((r) => (
                <tr key={r.tenant_id} className="border-b border-line last:border-0 hover:bg-paper-sunk/50">
                  <td className="px-4 py-2">
                    <Link className="font-medium text-brand hover:underline" href={`/admin/tenants/${r.tenant_id}`}>{r.name}</Link>
                    {r.pending_request && <span className="ms-2"><Badge tone="warn">{t("adm.request")}</Badge></span>}
                    <p className="text-xs text-ink-mute">{fmtDay(r.created_at, locale)}</p>
                  </td>
                  <td className="px-4 py-2">{r.plan_name}</td>
                  <td className="px-4 py-2">
                    <Badge tone={r.status === "active" ? "brand" : r.status === "trialing" ? "neutral" : "neg"}>{t(`bill.st.${r.status}` as MessageKey)}</Badge>
                    {r.status === "trialing" && r.trial_ends_at && <p className="text-xs text-ink-mute">{fmtDay(r.trial_ends_at, locale)}</p>}
                  </td>
                  <td className="px-4 py-2 tabular-nums">{r.members}</td>
                  <td className="px-4 py-2 tabular-nums">{r.brands}</td>
                  <td className="px-4 py-2 tabular-nums">{r.mentions_month.toLocaleString()}</td>
                  <td className="px-4 py-2 tabular-nums">{r.ai_calls_month.toLocaleString()}</td>
                  <td className="px-4 py-2 tabular-nums">{r.open_invoices}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Card>
    </div>
  );
}

function PlanEditor({ p }: { p: AdminPlan }) {
  const { t, locale } = useI18n();
  const qc = useQueryClient();
  const refresh = () => qc.invalidateQueries({ queryKey: ["admin", "plans"] });
  const [meta, setMeta] = useState({ name: p.name, description: p.description, is_active: p.is_active, is_public: p.is_public, sort_order: p.sort_order });
  const [ents, setEnts] = useState<Record<string, string>>(
    Object.fromEntries(ENTITLEMENTS.map((k) => [k, p.entitlements[k] === null || p.entitlements[k] === undefined ? "" : String(p.entitlements[k])])));
  const [price, setPrice] = useState({ currency: "DZD", billing_interval: "month", amount: "" });
  const saveMeta = useMutation({ mutationFn: () => api.patch(`/admin/plans/${p.key}`, meta), onSuccess: refresh });
  const saveEnts = useMutation({
    mutationFn: () => api.put(`/admin/plans/${p.key}/entitlements`,
      Object.fromEntries(Object.entries(ents).map(([k, v]) => [k, v.trim() === "" ? null : Number(v)]))),
    onSuccess: refresh,
  });
  const addPrice = useMutation({
    mutationFn: () => api.post(`/admin/plans/${p.key}/prices`, {
      currency: price.currency, billing_interval: price.billing_interval, amount_minor: parseMoney(price.amount) }),
    onSuccess: () => { setPrice({ ...price, amount: "" }); refresh(); },
  });
  const delPrice = useMutation({ mutationFn: (id: string) => api.del(`/admin/plans/${p.key}/prices/${id}`), onSuccess: refresh });
  return (
    <Card title={<span className="flex items-center gap-2">{p.name} <code className="text-xs text-ink-mute">{p.key}</code>
      {p.is_default && <Badge>{t("adm.default")}</Badge>}
      {!p.is_active && <Badge tone="neg">{t("adm.inactive")}</Badge>}
      {p.is_public && <Badge tone="brand">{t("adm.public")}</Badge>}</span>}>
      <div className="grid gap-6 lg:grid-cols-3">
        <form className="space-y-3" onSubmit={(e) => { e.preventDefault(); saveMeta.mutate(); }}>
          <label className="block"><span className="label">{t("adm.planName")}</span>
            <input className="field" value={meta.name} onChange={(e) => setMeta({ ...meta, name: e.target.value })} /></label>
          <label className="block"><span className="label">{t("adm.description")}</span>
            <textarea className="field" rows={3} value={meta.description} onChange={(e) => setMeta({ ...meta, description: e.target.value })} /></label>
          <label className="flex items-center gap-2 text-sm"><input type="checkbox" checked={meta.is_active}
            onChange={(e) => setMeta({ ...meta, is_active: e.target.checked })} />{t("adm.isActive")}</label>
          <label className="flex items-center gap-2 text-sm"><input type="checkbox" checked={meta.is_public}
            onChange={(e) => setMeta({ ...meta, is_public: e.target.checked })} />{t("adm.isPublic")}</label>
          <label className="block"><span className="label">{t("adm.order")}</span>
            <input className="field" type="number" min={0} value={meta.sort_order} onChange={(e) => setMeta({ ...meta, sort_order: Number(e.target.value) })} /></label>
          <ErrorBanner error={saveMeta.error} />
          <Button type="submit" variant="ghost" disabled={saveMeta.isPending}>{t("common.save")}</Button>
        </form>
        <form className="space-y-2" onSubmit={(e) => { e.preventDefault(); saveEnts.mutate(); }}>
          <p className="label">{t("adm.limits")}</p>
          <p className="text-xs text-ink-mute">{t("adm.limitsHelp")}</p>
          {ENTITLEMENTS.map((k) => (
            <label key={k} className="flex items-center justify-between gap-2 text-sm">
              <span className="text-ink-soft">{t(`plan.${k}` as MessageKey)}</span>
              <input className="field !w-24 text-end" inputMode="numeric" placeholder="∞" value={ents[k]}
                     onChange={(e) => setEnts({ ...ents, [k]: e.target.value.replace(/[^0-9]/g, "") })} />
            </label>
          ))}
          <ErrorBanner error={saveEnts.error} />
          <Button type="submit" variant="ghost" disabled={saveEnts.isPending}>{t("common.save")}</Button>
        </form>
        <div className="space-y-3">
          <p className="label">{t("adm.prices")}</p>
          {p.prices.length === 0 && <p className="text-sm text-ink-mute">{t("adm.noPrice")}</p>}
          <ul className="space-y-1 text-sm">
            {p.prices.map((x) => (
              <li key={x.id} className="flex items-center justify-between gap-2">
                <span className="tabular-nums">{fmtMoney(x.amount_minor, x.currency, locale)} {t(`bill.per.${x.billing_interval}`)}</span>
                <button className="text-xs text-signal-neg hover:underline" onClick={() => x.id && delPrice.mutate(x.id)}>{t("common.delete")}</button>
              </li>
            ))}
          </ul>
          <form className="flex flex-wrap gap-2" onSubmit={(e) => { e.preventDefault(); addPrice.mutate(); }}>
            <select className="field !w-auto" value={price.currency} onChange={(e) => setPrice({ ...price, currency: e.target.value })}>
              {["DZD", "EUR", "USD"].map((c) => <option key={c}>{c}</option>)}
            </select>
            <select className="field !w-auto" value={price.billing_interval} onChange={(e) => setPrice({ ...price, billing_interval: e.target.value })}>
              <option value="month">{t("bill.monthly")}</option><option value="year">{t("bill.yearly")}</option>
            </select>
            <input className="field !w-32" inputMode="decimal" placeholder={t("adm.amountHt")} value={price.amount}
                   onChange={(e) => setPrice({ ...price, amount: e.target.value })} />
            <Button type="submit" variant="ghost" disabled={parseMoney(price.amount) === null || !price.amount || addPrice.isPending}>{t("common.add")}</Button>
          </form>
          <p className="text-xs text-ink-mute">{t("adm.priceHelp")}</p>
          <ErrorBanner error={addPrice.error ?? delPrice.error} />
        </div>
      </div>
    </Card>
  );
}

function Plans() {
  const { t } = useI18n();
  const q = useQuery({ queryKey: ["admin", "plans"], queryFn: () => api.get<AdminPlan[]>("/admin/plans") });
  if (q.error) return <ErrorBanner error={q.error} />;
  if (!q.data) return <Spinner />;
  return (
    <div className="space-y-4">
      <p className="text-sm text-ink-soft">{t("adm.plansHelp")}</p>
      {q.data.map((p) => <PlanEditor key={`${p.key}-${JSON.stringify(p.prices)}`} p={p} />)}
    </div>
  );
}

function ConnectorRow({ c }: { c: ConnectorReview }) {
  const { t, fmtDate } = useI18n();
  const qc = useQueryClient();
  const [status, setStatus] = useState(c.review_status);
  const [notes, setNotes] = useState(c.review_notes);
  const save = useMutation({
    mutationFn: () => api.patch(`/admin/connectors/${c.key}`, { review_status: status, review_notes: notes }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["admin", "connectors"] }),
  });
  const cats = Array.isArray(c.data_categories) ? c.data_categories.join(", ") : c.data_categories;
  return (
    <Card title={<span className="flex items-center gap-2">{c.name} <code className="text-xs text-ink-mute">{c.key}</code>
      <Badge tone={c.review_status === "approved" ? "brand" : c.review_status === "rejected" ? "neg" : "warn"}>{t(`adm.rev.${c.review_status}` as MessageKey)}</Badge></span>}>
      <dl className="grid gap-x-6 gap-y-2 text-sm md:grid-cols-2">
        <div><dt className="text-xs text-ink-mute">{t("adm.access")}</dt><dd>{c.access_method}</dd></div>
        <div><dt className="text-xs text-ink-mute">{t("adm.basis")}</dt><dd>{c.legal_basis}</dd></div>
        <div><dt className="text-xs text-ink-mute">{t("adm.data")}</dt><dd>{cats || "—"}</dd></div>
        <div><dt className="text-xs text-ink-mute">{t("adm.restrictions")}</dt><dd>{c.restrictions || "—"}</dd></div>
      </dl>
      {c.reviewed_at && <p className="mt-3 text-xs text-ink-mute">{t("adm.reviewedBy", { who: c.reviewed_by ?? "", date: fmtDate(c.reviewed_at) })}</p>}
      <form className="mt-4 grid gap-3 md:grid-cols-[200px_1fr_auto] md:items-end" onSubmit={(e) => { e.preventDefault(); save.mutate(); }}>
        <label className="block"><span className="label">{t("adm.decision")}</span>
          <select className="field" value={status} onChange={(e) => setStatus(e.target.value as ConnectorReview["review_status"])}>
            {(["pending_legal_review", "approved", "rejected"] as const).map((s) => <option key={s} value={s}>{t(`adm.rev.${s}`)}</option>)}
          </select>
        </label>
        <label className="block"><span className="label">{t("adm.notes")}</span>
          <input className="field" value={notes} placeholder={t("adm.notesPlaceholder")} onChange={(e) => setNotes(e.target.value)} />
        </label>
        <Button type="submit" variant="ghost" disabled={save.isPending}>{t("common.save")}</Button>
      </form>
      <ErrorBanner error={save.error} />
    </Card>
  );
}

function Connectors() {
  const { t } = useI18n();
  const q = useQuery({ queryKey: ["admin", "connectors"], queryFn: () => api.get<ConnectorReview[]>("/admin/connectors") });
  if (q.error) return <ErrorBanner error={q.error} />;
  if (!q.data) return <Spinner />;
  return (
    <div className="space-y-4">
      <p className="rounded border border-signal-warn/30 bg-signal-warn/10 px-3 py-2 text-sm text-signal-warn">{t("adm.legalFlag")}</p>
      {q.data.map((c) => <ConnectorRow key={c.key} c={c} />)}
    </div>
  );
}

export default function AdminPage() {
  const { t } = useI18n();
  const [tab, setTab] = useState<Tab>("orgs");
  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-xl font-semibold">{t("adm.title")}</h1>
        <p className="mt-1 text-sm text-ink-soft">{t("adm.subtitle")}</p>
      </div>
      <div role="tablist" className="flex gap-1 border-b border-line">
        {(["orgs", "plans", "connectors"] as const).map((k) => (
          <button key={k} role="tab" aria-selected={tab === k} onClick={() => setTab(k)}
                  className={`-mb-px border-b-2 px-3 py-2 text-sm ${tab === k ? "border-brand font-medium text-brand-strong" : "border-transparent text-ink-soft hover:text-ink"}`}>
            {t(`adm.tab.${k}`)}
          </button>
        ))}
      </div>
      {tab === "orgs" ? <Overview /> : tab === "plans" ? <Plans /> : <Connectors />}
    </div>
  );
}
