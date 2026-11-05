"use client";

import { LangSwitcher } from "@/components/ui";
import { useI18n } from "@/components/providers";

export default function AuthLayout({ children }: { children: React.ReactNode }) {
  const { t } = useI18n();
  return (
    <div className="grid min-h-screen lg:grid-cols-[1fr_1.1fr]">
      <aside className="hidden flex-col justify-between bg-ink p-10 text-paper lg:flex">
        <div className="text-lg font-semibold tracking-tight">{t("app.name")}</div>
        <div>
          <p className="max-w-sm text-3xl font-medium leading-snug">{t("app.tagline")}</p>
          <p className="mt-6 font-mono text-sm text-paper/50" dir="auto">
            bzaf · بزاف · bezzaf — 3jebni · عجبني
          </p>
        </div>
        <p className="text-xs text-paper/40">© {new Date().getFullYear()}</p>
      </aside>
      <main className="flex flex-col">
        <div className="flex justify-end p-4"><LangSwitcher /></div>
        <div className="flex flex-1 items-center justify-center px-6 pb-16">
          <div className="w-full max-w-sm">{children}</div>
        </div>
      </main>
    </div>
  );
}
