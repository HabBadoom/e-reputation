import type { Metadata, Viewport } from "next";
import { cookies } from "next/headers";
import { Providers } from "@/components/providers";
import { DEFAULT_LOCALE, LOCALE_COOKIE, dirOf, isLocale } from "@/lib/i18n";
import "./globals.css";

// System fonts: no build-time download (Google Fonts fetches made CI and image builds flaky),
// and lighter pages on mobile data. Arabic falls back to the platform's Arabic font.
export const metadata: Metadata = {
  title: "Aynpulse Scanner",
  description: "Collecte de publications et de commentaires pour AnyPulse",
  robots: { index: false },
};

export const viewport: Viewport = { width: "device-width", initialScale: 1 };

export default async function RootLayout({ children }: { children: React.ReactNode }) {
  const raw = (await cookies()).get(LOCALE_COOKIE)?.value;
  const locale = isLocale(raw) ? raw : DEFAULT_LOCALE;
  return (
    <html lang={locale} dir={dirOf(locale)}>
      <body className="min-h-screen font-sans">
        <Providers locale={locale}>{children}</Providers>
      </body>
    </html>
  );
}
