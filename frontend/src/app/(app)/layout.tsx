"use client";

import { useQueryClient } from "@tanstack/react-query";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useEffect } from "react";
import { useI18n } from "@/components/providers";
import { Badge, LangSwitcher, Spinner } from "@/components/ui";
import { api, ApiError } from "@/lib/api";
import type { MessageKey } from "@/lib/i18n";
import { useMe } from "@/lib/session";

const NAV: { href: string; key: MessageKey; perm?: string }[] = [
  { href: "/dashboard", key: "nav.dashboard" },
  { href: "/brands", key: "nav.brands" },
  { href: "/team", key: "nav.team" },
  { href: "/audit", key: "nav.audit", perm: "audit:read" },
  { href: "/settings", key: "nav.settings" },
];
// Shown disabled so users can see what's coming without being shown fake screens.
const UPCOMING: { key: MessageKey; milestone: number }[] = [
  { key: "nav.sources", milestone: 3 },
  { key: "nav.mentions", milestone: 3 },
  { key: "nav.analytics", milestone: 5 },
  { key: "nav.reports", milestone: 5 },
];

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
        <div className="border-b border-line px-5 py-4 text-base font-semibold tracking-tight">{t("app.name")}</div>
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
          <nav className="flex gap-3 text-sm">
            {NAV.slice(0, 3).map((n) => <Link key={n.href} href={n.href}>{t(n.key)}</Link>)}
          </nav>
        </header>
        <main className="mx-auto max-w-6xl p-4 md:p-8">{children}</main>
      </div>
    </div>
  );
}
