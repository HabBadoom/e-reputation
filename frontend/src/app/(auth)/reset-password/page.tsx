"use client";

import { useMutation } from "@tanstack/react-query";
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { Suspense, useState } from "react";
import { useI18n } from "@/components/providers";
import { Button, ErrorBanner, Field, fieldErrors } from "@/components/ui";
import { api } from "@/lib/api";

function Reset() {
  const { t } = useI18n();
  const token = useSearchParams().get("token") ?? "";
  const [password, setPassword] = useState("");
  const m = useMutation({ mutationFn: () => api.post("/auth/password/reset", { token, password }) });
  const fe = fieldErrors(m.error);
  if (!token) return <p className="text-sm text-signal-neg">{t("auth.linkInvalid")}</p>;
  return (
    <>
      <h1 className="mb-6 text-2xl font-semibold">{t("auth.resetTitle")}</h1>
      {m.isSuccess ? (
        <>
          <p role="status" className="text-sm text-ink-soft">{t("auth.resetDone")}</p>
          <Link href="/login" className="mt-6 inline-block"><Button>{t("auth.login")}</Button></Link>
        </>
      ) : (
        <form className="space-y-4" onSubmit={(e) => { e.preventDefault(); m.mutate(); }}>
          {!fe.password && <ErrorBanner error={m.error} />}
          <Field label={t("auth.newPassword")} type="password" required minLength={12}
                 autoComplete="new-password" hint={t("auth.passwordHint")} value={password}
                 onChange={(e) => setPassword(e.target.value)} error={fe.password} />
          <Button type="submit" className="w-full" disabled={m.isPending}>{t("common.save")}</Button>
        </form>
      )}
    </>
  );
}

export default function ResetPasswordPage() {
  return <Suspense><Reset /></Suspense>;
}
