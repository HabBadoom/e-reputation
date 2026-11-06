"use client";

import { useMutation, useQuery } from "@tanstack/react-query";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense } from "react";
import { useI18n } from "@/components/providers";
import { Button, ErrorBanner, Spinner } from "@/components/ui";
import { api, type Me } from "@/lib/api";

function Invite() {
  const { t } = useI18n();
  const router = useRouter();
  const token = useSearchParams().get("token") ?? "";
  const me = useQuery({ queryKey: ["me"], queryFn: () => api.get<Me>("/auth/me"), retry: false });
  const accept = useMutation({
    mutationFn: () => api.post("/invitations/accept", { token }),
    onSuccess: () => router.replace("/dashboard"),
  });
  const next = encodeURIComponent(`/invite?token=${token}`);
  return (
    <>
      <h1 className="mb-4 text-2xl font-semibold">{t("invite.title")}</h1>
      {me.isLoading ? <Spinner /> : me.isError ? (
        <>
          <p className="text-sm text-ink-soft">{t("invite.needLogin")}</p>
          <div className="mt-4 flex flex-col gap-2 text-sm">
            <Link href={`/login?next=${next}`} className="text-brand hover:underline">{t("auth.login")}</Link>
            <Link href={`/register?invite=${encodeURIComponent(token)}`} className="text-brand hover:underline">
              {t("invite.createAccount")}
            </Link>
          </div>
        </>
      ) : (
        <div className="space-y-4">
          <p className="text-sm text-ink-soft" dir="ltr">{me.data?.email}</p>
          <ErrorBanner error={accept.error} />
          <Button onClick={() => accept.mutate()} disabled={!token || accept.isPending}>
            {t("invite.accept")}
          </Button>
        </div>
      )}
    </>
  );
}

export default function InvitePage() {
  return <Suspense><Invite /></Suspense>;
}
