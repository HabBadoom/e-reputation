"use client";

import { useMutation } from "@tanstack/react-query";
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { Suspense, useEffect, useRef } from "react";
import { useI18n } from "@/components/providers";
import { Button } from "@/components/ui";
import { api } from "@/lib/api";

function Verify() {
  const { t } = useI18n();
  const token = useSearchParams().get("token") ?? "";
  const m = useMutation({ mutationFn: () => api.post("/auth/email/verify", { token }) });
  const started = useRef(false);
  useEffect(() => {
    // Tokens are single-use: guard against React strict-mode double effects.
    if (token && !started.current) { started.current = true; m.mutate(); }
  }, [token, m]);

  return (
    <>
      <h1 className="mb-6 text-2xl font-semibold">{t("auth.verifyTitle")}</h1>
      {m.isPending && <p className="text-sm text-ink-soft">{t("auth.verifying")}</p>}
      {m.isSuccess && <p role="status" className="text-sm text-signal-pos">{t("auth.verified")}</p>}
      {(m.isError || !token) && <p role="alert" className="text-sm text-signal-neg">{t("auth.linkInvalid")}</p>}
      {!m.isPending && (
        <Link href="/dashboard" className="mt-6 inline-block"><Button>{t("auth.continue")}</Button></Link>
      )}
    </>
  );
}

export default function VerifyEmailPage() {
  return <Suspense><Verify /></Suspense>;
}
