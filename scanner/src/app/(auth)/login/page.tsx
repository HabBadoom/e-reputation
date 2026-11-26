"use client";

import { useMutation } from "@tanstack/react-query";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useState } from "react";
import { useI18n } from "@/components/providers";
import { Button, ErrorBanner, Field } from "@/components/ui";
import { api } from "@/lib/api";

function safeNext(n: string | null) {
  return n && n.startsWith("/") && !n.startsWith("//") ? n : "/collect";
}

function LoginForm() {
  const { t } = useI18n();
  const router = useRouter();
  const next = safeNext(useSearchParams().get("next"));
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const login = useMutation({
    mutationFn: () => api.post("/auth/login", { email, password }),
    onSuccess: () => router.replace(next),
  });

  return (
    <>
      <h1 className="mb-2 text-2xl font-semibold">{t("auth.login")}</h1>
      <p className="mb-6 text-sm text-ink-soft">{t("auth.separateNote")}</p>
      <form className="space-y-4" onSubmit={(e) => { e.preventDefault(); login.mutate(); }}>
        <ErrorBanner error={login.error} />
        <Field label={t("auth.email")} type="email" autoComplete="email" required dir="ltr"
               value={email} onChange={(e) => setEmail(e.target.value)} />
        <Field label={t("auth.password")} type="password" autoComplete="current-password" required
               value={password} onChange={(e) => setPassword(e.target.value)} />
        <Button type="submit" className="w-full" disabled={login.isPending}>{t("auth.login")}</Button>
      </form>
      <p className="mt-6 text-sm text-ink-soft">
        {t("auth.noAccount")}{" "}
        <Link className="text-brand hover:underline" href="/register">{t("auth.register")}</Link>
      </p>
    </>
  );
}

export default function LoginPage() {
  return <Suspense><LoginForm /></Suspense>;
}
