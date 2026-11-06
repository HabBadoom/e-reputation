"use client";

import { useMutation } from "@tanstack/react-query";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useState } from "react";
import { useI18n } from "@/components/providers";
import { Button, ErrorBanner, Field, fieldErrors } from "@/components/ui";
import { api } from "@/lib/api";

function RegisterForm() {
  const { t, locale } = useI18n();
  const router = useRouter();
  const invite = useSearchParams().get("invite");
  const [f, setF] = useState({ full_name: "", email: "", password: "", organization_name: "" });
  const set = (k: keyof typeof f) => (e: React.ChangeEvent<HTMLInputElement>) =>
    setF({ ...f, [k]: e.target.value });
  const m = useMutation({
    mutationFn: () => api.post("/auth/register", invite
      ? { ...f, organization_name: null, invitation_token: invite, locale }
      : { ...f, locale }),
    onSuccess: () => router.replace("/dashboard"),
  });
  const fe = fieldErrors(m.error);
  return (
    <>
      <h1 className="mb-6 text-2xl font-semibold">{t("auth.register")}</h1>
      <form className="space-y-4" onSubmit={(e) => { e.preventDefault(); m.mutate(); }}>
        {!Object.keys(fe).length && <ErrorBanner error={m.error} />}
        {invite ? (
          <p className="rounded border border-brand/30 bg-brand-tint p-3 text-sm text-brand-strong">{t("register.joining")}</p>
        ) : (
          <Field label={t("auth.orgName")} required value={f.organization_name}
                 onChange={set("organization_name")} error={fe.organization_name} />
        )}
        <Field label={t("auth.fullName")} required autoComplete="name" value={f.full_name}
               onChange={set("full_name")} error={fe.full_name} />
        <Field label={t("auth.email")} type="email" required autoComplete="email" dir="ltr"
               value={f.email} onChange={set("email")} error={fe.email} />
        <Field label={t("auth.password")} type="password" required minLength={12}
               autoComplete="new-password" hint={t("auth.passwordHint")} value={f.password}
               onChange={set("password")} error={fe.password} />
        <Button type="submit" className="w-full" disabled={m.isPending}>{t("auth.register")}</Button>
      </form>
      <p className="mt-6 text-sm text-ink-soft">
        {t("auth.haveAccount")}{" "}
        <Link className="text-brand hover:underline" href="/login">{t("auth.login")}</Link>
      </p>
    </>
  );
}

export default function RegisterPage() {
  return <Suspense><RegisterForm /></Suspense>;
}
