"use client";

import { useMutation } from "@tanstack/react-query";
import Link from "next/link";
import { useState } from "react";
import { useI18n } from "@/components/providers";
import { Button, ErrorBanner, Field } from "@/components/ui";
import { api } from "@/lib/api";

export default function ForgotPasswordPage() {
  const { t } = useI18n();
  const [email, setEmail] = useState("");
  const m = useMutation({ mutationFn: () => api.post("/auth/password/forgot", { email }) });
  return (
    <>
      <h1 className="mb-2 text-2xl font-semibold">{t("auth.forgot")}</h1>
      {m.isSuccess ? (
        <p role="status" className="rounded border border-brand/30 bg-brand-tint p-3 text-sm text-brand-strong">
          {t("auth.forgotSent")}
        </p>
      ) : (
        <>
          <p className="mb-6 text-sm text-ink-soft">{t("auth.forgotIntro")}</p>
          <form className="space-y-4" onSubmit={(e) => { e.preventDefault(); m.mutate(); }}>
            <ErrorBanner error={m.error} />
            <Field label={t("auth.email")} type="email" required autoComplete="email" dir="ltr"
                   value={email} onChange={(e) => setEmail(e.target.value)} />
            <Button type="submit" className="w-full" disabled={m.isPending}>{t("auth.forgotSend")}</Button>
          </form>
        </>
      )}
      <Link className="mt-6 inline-block text-sm text-brand hover:underline" href="/login">
        {t("auth.backToLogin")}
      </Link>
    </>
  );
}
