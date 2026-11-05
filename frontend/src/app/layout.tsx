import type { Metadata } from "next";
import { IBM_Plex_Mono, IBM_Plex_Sans, IBM_Plex_Sans_Arabic } from "next/font/google";
import { cookies } from "next/headers";
import { Providers } from "@/components/providers";
import { DEFAULT_LOCALE, LOCALE_COOKIE, dirOf, isLocale } from "@/lib/i18n";
import "./globals.css";

const sans = IBM_Plex_Sans({ subsets: ["latin"], weight: ["400", "500", "600"], variable: "--font-sans" });
const arabic = IBM_Plex_Sans_Arabic({ subsets: ["arabic"], weight: ["400", "500", "600"], variable: "--font-arabic" });
const mono = IBM_Plex_Mono({ subsets: ["latin"], weight: ["400"], variable: "--font-mono" });

export const metadata: Metadata = {
  title: "RepIntel",
  description: "Reputation intelligence platform",
  robots: { index: false },
};

export default async function RootLayout({ children }: { children: React.ReactNode }) {
  const raw = (await cookies()).get(LOCALE_COOKIE)?.value;
  const locale = isLocale(raw) ? raw : DEFAULT_LOCALE;
  return (
    <html lang={locale} dir={dirOf(locale)} className={`${sans.variable} ${arabic.variable} ${mono.variable}`}>
      <body className="min-h-screen font-sans">
        <Providers locale={locale}>{children}</Providers>
      </body>
    </html>
  );
}
