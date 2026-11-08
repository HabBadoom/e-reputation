"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useEffect } from "react";
import { useI18n } from "@/components/providers";
import { Badge, LangSwitcher, Spinner } from "@/components/ui";
import { api, ApiError, type AlertSummary } from "@/lib/api";
import type { MessageKey } from "@/lib/i18n";
import { useMe } from "@/lib/session";

const NAV: { href: string; key: MessageKey; perm?: string }[] = [
  { href: "/dashboard", key: "nav.dashboard" },
  { href: "/brands", key: "nav.brands" },
  { href: "/monitoring", key: "nav.monitoring", perm: "monitoring:read" },
  { href: "/mentions", key: "nav.mentions", perm: "mentions:read" },
  { href: "/analytics", key: "nav.analytics", perm: "mentions:read" },
  { href: "/alerts", key: "nav.alerts", perm: "alerts:read" },
  { href: "/reports", key: "nav.reports", perm: "reports:read" },
  { href: "/browser", key: "nav.browser", perm: "browser:read" },
  { href: "/sources", key: "nav.sources", perm: "sources:read" },
  { href: "/nlp", key: "nav.nlp" },
  { href: "/team", key: "nav.team" },
  { href: "/audit", key: "nav.audit", perm: "audit:read" },
  { href: "/settings", key: "nav.settings" },
  { href: "/account", key: "nav.account" },
];
// Shown disabled so users can see what's coming without being shown fake screens.
const UPCOMING: { key: MessageKey; milestone: number }[] = [
  { key: "nav.agents", milestone: 7 },
];

/** Open alerts counter, refreshed every minute. */
function AlertBell() {
  const { t } = useI18n();
  const q = useQuery({
    queryKey: ["alerts-summary"],
    queryFn: () => api.get<AlertSummary>("/alerts/summary"),
    refetchInterval: 60_000,
  });
  const n = q.data?.open ?? 0;
  const urgent = (q.data?.by_severity.CRITICAL ?? 0) + (q.data?.by_severity.HIGH ?? 0) > 0;
  return (
    <Link href="/alerts" aria-label={t("bell.label", { n })}
          className="relative inline-flex h-8 w-8 items-center justify-center rounded text-ink-soft hover:bg-paper-sunk">
      <svg viewBox="0 0 24 24" className="h-5 w-5" fill="none" stroke="currentColor" strokeWidth="1.8" aria-hidden>
        <path d="M6 16V11a6 6 0 1 1 12 0v5l1.5 2h-15L6 16Z" strokeLinejoin="round" />
        <path d="M10 20a2 2 0 0 0 4 0" strokeLinecap="round" />
      </svg>
      {n > 0 && (
        <span className={`absolute -end-1 -top-1 min-w-[18px] rounded-full px-1 text-center text-[11px] font-semibold leading-[18px] text-white ${urgent ? "bg-signal-neg" : "bg-signal-warn"}`}>
          {n > 99 ? "99+" : n}
        </span>
      )}
    </Link>
  );
}

function UnverifiedBanner() {
  const { t } = useI18n();
  const resend = useMutation({ mutationFn: () => api.post("/auth/email/resend") });
  return (
    <div role="status" className="flex flex-wrap items-center justify-between gap-2 border-b border-signal-warn/30 bg-signal-warn/10 px-4 py-2 text-sm text-signal-warn md:px-8">
      <span>{t("banner.unverified")}</span>
      {resend.isSuccess ? <span>{t("banner.resent")}</span> : (
        <button className="font-medium underline" disabled={resend.isPending} onClick={() => resend.mutate()}>
          {t("banner.resend")}
        </button>
      )}
    </div>
  );
}

export default function AppLayout({ children }: { children: React.ReactNode }) {
  const { t } = useI18n();
  const me = useMe();
  const path = usePathname();
  const router = useRouter();
  const qc = useQueryClient();

  useEffect(() => {
    if (me.error instanceof ApiError && me.error.status === 401) {
      router.replace(`/login?next=${encodeURIComponent(path)}`);
    }
  }, [me.error, path, router]);

  if (!me.data) return <Spinner />;
  const u = me.data;

  async function switchTenant(id: string) {
    await api.post("/auth/switch-tenant", { tenant_id: id });
    qc.clear();
    router.replace("/dashboard");
    router.refresh();
  }
  async function logout() {
    await api.post("/auth/logout").catch(() => undefined);
    qc.clear();
    router.replace("/login");
  }

  return (
    <div className="flex min-h-screen">
      <aside className="sticky top-0 hidden h-screen w-60 shrink-0 flex-col border-e border-line bg-paper-raised md:flex">
        <div className="flex items-center justify-between border-b border-line px-5 py-3">
          <span className="text-base font-semibold tracking-tight">{t("app.name")}</span>
          {u.permissions.includes("alerts:read") && <AlertBell />}
        </div>
        <div className="border-b border-line px-3 py-3">
          {u.tenants.length > 1 ? (
            <select className="field" value={u.tenant.id} onChange={(e) => switchTenant(e.target.value)}
                    aria-label="Organization">
              {u.tenants.map((x) => <option key={x.id} value={x.id}>{x.name}</option>)}
            </select>
          ) : (
            <p className="truncate px-2 text-sm font-medium">{u.tenant.name}</p>
          )}
          <p className="mt-1 px-2 text-xs text-ink-mute">{t(`role.${u.tenant.role}`)}</p>
        </div>
        <nav className="flex-1 space-y-0.5 p-3">
          {NAV.filter((n) => !n.perm || u.permissions.includes(n.perm)).map((n) => {
            const active = path === n.href || path.startsWith(n.href + "/");
            return (
              <Link key={n.href} href={n.href} aria-current={active ? "page" : undefined}
                    className={`block rounded px-2 py-1.5 text-sm ${active ? "bg-brand-tint font-medium text-brand-strong" : "text-ink-soft hover:bg-paper-sunk"}`}>
                {t(n.key)}
              </Link>
            );
          })}
          <p className="px-2 pb-1 pt-5 text-[11px] font-medium uppercase tracking-wider text-ink-mute">{t("nav.upcoming")}</p>
          {UPCOMING.map((n) => (
            <div key={n.key} aria-disabled className="flex items-center justify-between px-2 py-1.5 text-sm text-ink-mute">
              {t(n.key)} <Badge>{t("nav.milestone", { n: n.milestone })}</Badge>
            </div>
          ))}
        </nav>
        <div className="space-y-3 border-t border-line p-3">
          <LangSwitcher />
          <div className="flex items-center justify-between px-1 text-xs">
            <span className="truncate text-ink-soft" dir="ltr">{u.email}</span>
            <button onClick={logout} className="text-brand hover:underline">{t("auth.logout")}</button>
          </div>
        </div>
      </aside>
      <div className="flex-1">
        <header className="flex items-center justify-between border-b border-line bg-paper-raised px-4 py-3 md:hidden">
          <span className="font-semibold">{t("app.name")}</span>
          {u.permissions.includes("alerts:read") && <AlertBell />}
          <nav className="flex gap-3 text-sm">
            {NAV.slice(0, 3).map((n) => <Link key={n.href} href={n.href}>{t(n.key)}</Link>)}
          </nav>
        </header>
        {!u.email_verified && <UnverifiedBanner />}
        <main className="mx-auto max-w-6xl p-4 md:p-8">{children}</main>
      </div>
    </div>
  );
}
