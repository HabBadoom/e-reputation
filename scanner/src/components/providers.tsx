"use client";

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { useRouter } from "next/navigation";
import { createContext, useCallback, useContext, useState, type ReactNode } from "react";
import { ApiError } from "@/lib/api";
import { LOCALE_COOKIE, translate, type Locale, type MessageKey } from "@/lib/i18n";

interface I18n {
  locale: Locale;
  t: (key: MessageKey, vars?: Record<string, string | number>) => string;
  setLocale: (l: Locale) => void;
  fmtDate: (iso: string) => string;
}
const I18nCtx = createContext<I18n | null>(null);

export function useI18n(): I18n {
  const v = useContext(I18nCtx);
  if (!v) throw new Error("useI18n outside provider");
  return v;
}

export function Providers({ locale, children }: { locale: Locale; children: ReactNode }) {
  const router = useRouter();
  const [client] = useState(
    () =>
      new QueryClient({
        defaultOptions: {
          queries: {
            staleTime: 15_000,
            retry: (n, e) => !(e instanceof ApiError && e.status < 500) && n < 2,
          },
        },
      }),
  );
  const setLocale = useCallback(
    (l: Locale) => {
      document.cookie = `${LOCALE_COOKIE}=${l}; path=/; max-age=31536000; samesite=lax`;
      router.refresh();
    },
    [router],
  );
  const intlLocale = locale === "ar" ? "ar-DZ" : locale === "fr" ? "fr-DZ" : "en-GB";
  const value: I18n = {
    locale,
    t: (k, v) => translate(locale, k, v),
    setLocale,
    fmtDate: (iso) =>
      new Intl.DateTimeFormat(intlLocale, { dateStyle: "medium", timeStyle: "short" }).format(new Date(iso)),
  };
  return (
    <I18nCtx.Provider value={value}>
      <QueryClientProvider client={client}>{children}</QueryClientProvider>
    </I18nCtx.Provider>
  );
}
