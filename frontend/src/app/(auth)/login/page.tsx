"use client";

import { useMutation } from "@tanstack/react-query";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useState } from "react";
import { useI18n } from "@/components/providers";
import { Button, ErrorBanner, Field } from "@/components/ui";
import { api, type LoginResult } from "@/lib/api";

function safeNext(n: string | null) {
  return n && n.startsWith("/") && !n.startsWith("//") ? n : "/dashboard";
}

function LoginForm() {
  const { t } = useI18n();
  const router = useRouter();
  const next = safeNext(useSearchParams().get("next"));
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [mfaToken, setMfaToken] = useState<string | null>(null);
  const [code, setCode] = useState("");

  const login = useMutation({
    mutationFn: () => api.post<LoginResult>("/auth/login", { email, password }),
    onSuccess: (r) => {
      if (r.mfa_required) setMfaToken(r.mfa_token);
      else router.replace(next);
    },
  });
  const mfa = useMutation({
    mutationFn: () => api.post("/auth/login/mfa", { mfa_token: mfaToken, code }),
    onSuccess: () => router.replace(next),
  });

  if (mfaToken) {
    return (
      <>
        <h1 className="mb-2 text-2xl font-semibold">{t("auth.mfaTitle")}</h1>
        <p className="mb-6 text-sm text-ink-soft">{t("auth.mfaPrompt")}</p>
        <form className="space-y-4" onSubmit={(e) => { e.preventDefault(); mfa.mutate(); }}>
          <ErrorBanner error={mfa.error} />
          <Field label={t("auth.mfaCode")} required autoFocus dir="ltr" inputMode="text"
                 autoComplete="one-time-code" maxLength={20} value={code}
                 className="font-mono tracking-widest" onChange={(e) => setCode(e.target.value)} />
          <Button type="submit" className="w-full" disabled={mfa.isPending || code.length < 6}>
            {t("auth.verify")}
          </Button>
        </form>
        <button className="mt-6 text-sm text-brand hover:underline"
                onClick={() => { setMfaToken(null); setCode(""); mfa.reset(); }}>
          {t("auth.backToLogin")}
        </button>
      </>
    );
  }

  return (
    <>
      <h1 className="mb-6 text-2xl font-semibold">{t("auth.login")}</h1>
      <form className="space-y-4" onSubmit={(e) => { e.preventDefault(); login.mutate(); }}>
        <ErrorBanner error={login.error} />
        <Field label={t("auth.email")} type="email" autoComplete="email" required dir="ltr"
               value={email} onChange={(e) => setEmail(e.target.value)} />
        <Field label={t("auth.password")} type="password" autoComplete="current-password" required
               value={password} onChange={(e) => setPassword(e.target.value)} />
        <Button type="submit" className="w-full" disabled={login.isPending}>{t("auth.login")}</Button>
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
