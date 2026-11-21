"use client";

import { useMutation, useQuery } from "@tanstack/react-query";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useI18n } from "@/components/providers";
import { LangSwitcher } from "@/components/ui";
import { api, type ScannerMe } from "@/lib/api";

const NAV = [
  { href: "/collect", key: "nav.collect" },
  { href: "/history", key: "nav.history" },
] as const;

export default function AppLayout({ children }: { children: React.ReactNode }) {
  const { t } = useI18n();
  const pathname = usePathname();
  const router = useRouter();
  const me = useQuery({ queryKey: ["me"], queryFn: () => api.get<ScannerMe>("/auth/me"), staleTime: 60_000 });
  const logout = useMutation({
    mutationFn: () => api.post("/auth/logout"),
    onSettled: () => router.replace("/login"),
  });

  return (
    <div className="min-h-screen">
      <header className="border-b border-line bg-paper-raised">
        <div className="mx-auto flex max-w-4xl flex-wrap items-center gap-x-6 gap-y-2 px-4 py-3">
          <Link href="/collect" className="font-semibold tracking-tight">{t("app.name")}</Link>
          <nav className="flex gap-1 text-sm">
            {NAV.map((n) => (
              <Link key={n.href} href={n.href}
                    aria-current={pathname.startsWith(n.href) ? "page" : undefined}
                    className={`rounded px-2.5 py-1.5 ${pathname.startsWith(n.href)
                      ? "bg-brand-tint font-medium text-brand-strong" : "text-ink-soft hover:bg-paper-sunk"}`}>
                {t(n.key)}
              </Link>
            ))}
          </nav>
          <div className="ms-auto flex items-center gap-3 text-sm">
            {me.data && (
              <span className="hidden text-ink-soft sm:inline">
                {me.data.full_name}
              </span>
            )}
            <LangSwitcher />
            <button className="text-ink-soft hover:text-ink" onClick={() => logout.mutate()}>
              {t("auth.logout")}
            </button>
          </div>
        </div>
      </header>
      <main className="mx-auto max-w-4xl px-4 py-8">{children}</main>
    </div>
  );
}
