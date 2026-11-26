"use client";

import { useI18n } from "@/components/providers";

// Public page named in the crawler's user agent: what AynPulseBot reads and how to opt out.
export default function BotPage() {
  const { t } = useI18n();
  return (
    <article className="space-y-4 text-sm leading-relaxed text-ink-soft">
      <h1 className="text-2xl font-semibold text-ink">{t("bot.title")}</h1>
      <p>{t("bot.what")}</p>
      <p>{t("bot.rules")}</p>
      <p>{t("bot.optOut")}</p>
      <pre className="rounded bg-ink/5 p-3 font-mono text-xs text-ink" dir="ltr">
        {"User-agent: AynPulseBot\nDisallow: /"}
      </pre>
      <p>{t("bot.contact")} <a className="underline" href="mailto:contact@aynpulse.com">contact@aynpulse.com</a></p>
    </article>
  );
}
