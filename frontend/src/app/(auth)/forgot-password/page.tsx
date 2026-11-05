"use client";

import Link from "next/link";
import { useI18n } from "@/components/providers";

// Password reset by email ships with Milestone 1.1 (needs the email delivery provider).
// Until then this page says so plainly rather than pretending to send an email.
export default function ForgotPasswordPage() {
  const { t } = useI18n();
  return (
    <>
      <h1 className="mb-4 text-2xl font-semibold">{t("auth.forgot")}</h1>
      <p className="text-sm leading-relaxed text-ink-soft">{t("auth.forgotUnavailable")}</p>
      <Link className="mt-6 inline-block text-sm text-brand hover:underline" href="/login">
        {t("auth.backToLogin")}
      </Link>
    </>
  );
}
