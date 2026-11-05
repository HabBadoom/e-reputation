"use client";

import { useMutation } from "@tanstack/react-query";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useState } from "react";
import { useI18n } from "@/components/providers";
import { Button, ErrorBanner, Field } from "@/components/ui";
import { api } from "@/lib/api";

function safeNext(n: string | null) {
  return n && n.startsWith("/") && !n.startsWith("//") ? n : "/dashboard";
}

function LoginForm() {
  const { t } = useI18n();
  const router = useRouter();
  const next = safeNext(useSearchParams().get("next"));
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const m = useMutation({
    mutationFn: () => api.post("/auth/login", { email, password }),
    onSuccess: () => router.replace(next),
  });
  return (
    <>
      <h1 className="mb-6 text-2xl font-semibold">{t("auth.login")}</h1>
      <form className="space-y-4" onSubmit={(e) => { e.preventDefault(); m.mutate(); }}>
        <ErrorBanner error={m.error} />
        <Field label={t("auth.email")} type="email" autoComplete="email" required dir="ltr"
               value={email} onChange={(e) => setEmail(e.target.value)} />
        <Field label={t("auth.password")} type="password" autoComplete="current-password" required
               value={password} onChange={(e) => setPassword(e.target.value)} />
        <Button type="submit" className="w-full" disabled={m.isPending}>{t("auth.login")}</Button>
      </form>
      <div className="mt-6 flex justify-between text-sm">
        <Link className="text-brand hover:underline" href="/forgot-password">{t("auth.forgot")}</Link>
        <span className="text-ink-soft">
          {t("auth.noAccount")}{" "}
          <Link className="text-brand hover:underline" href="/register">{t("auth.register")}</Link>
        </span>
      </div>
    </>
  );
}

export default function LoginPage() {
  return <Suspense><LoginForm /></Suspense>;
}
