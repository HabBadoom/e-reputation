"use client";

import { useMutation } from "@tanstack/react-query";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState } from "react";
import { useI18n } from "@/components/providers";
import { Button, ErrorBanner, Field, fieldErrors } from "@/components/ui";
import { api } from "@/lib/api";

export default function RegisterPage() {
  const { t, locale } = useI18n();
  const router = useRouter();
  const [form, setForm] = useState({ full_name: "", email: "", password: "" });
  const set = (k: keyof typeof form) => (e: React.ChangeEvent<HTMLInputElement>) =>
    setForm((f) => ({ ...f, [k]: e.target.value }));

  const register = useMutation({
    mutationFn: () =>
      api.post("/auth/register", {
        email: form.email,
        password: form.password,
        full_name: form.full_name,
        locale,
      }),
    onSuccess: () => router.replace("/collect"),
  });
  const errs = fieldErrors(register.error);

  return (
    <>
      <h1 className="mb-2 text-2xl font-semibold">{t("auth.register")}</h1>
      <p className="mb-6 text-sm text-ink-soft">{t("auth.separateNote")}</p>
      <form className="space-y-4" onSubmit={(e) => { e.preventDefault(); register.mutate(); }}>
        <ErrorBanner error={register.error} />
        <Field label={t("auth.fullName")} autoComplete="name" required value={form.full_name}
               onChange={set("full_name")} error={errs.full_name} />
        <Field label={t("auth.email")} type="email" autoComplete="email" required dir="ltr"
               value={form.email} onChange={set("email")} error={errs.email} />
        <Field label={t("auth.password")} type="password" autoComplete="new-password" required
               minLength={12} hint={t("auth.passwordHint")} value={form.password}
               onChange={set("password")} error={errs.password} />
        <Button type="submit" className="w-full" disabled={register.isPending}>{t("auth.register")}</Button>
      </form>
      <p className="mt-6 text-sm text-ink-soft">
        {t("auth.haveAccount")}{" "}
        <Link className="text-brand hover:underline" href="/login">{t("auth.login")}</Link>
      </p>
    </>
  );
}
