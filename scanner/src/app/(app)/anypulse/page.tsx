"use client";

import { useMutation, useQuery } from "@tanstack/react-query";
import { useI18n } from "@/components/providers";
import { Badge, Button, Card, ErrorBanner, Spinner } from "@/components/ui";
import { api, type AnyPulseAccess, type InvitationSent } from "@/lib/api";

// The account's own AnyPulse organization, linked automatically at sign-up. Analyses and
// reports are read in AnyPulse (separate sign-in); only a platform administrator can revoke.
export default function AnyPulsePage() {
  const { t, fmtDate } = useI18n();
  const q = useQuery({ queryKey: ["anypulse"], queryFn: () => api.get<AnyPulseAccess>("/anypulse") });
  const resend = useMutation({ mutationFn: () => api.post<InvitationSent>("/anypulse/invitation") });
  const a = q.data;

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-semibold">{t("ap.title")}</h1>
        <p className="mt-1 max-w-2xl text-sm text-ink-soft">{t("ap.intro")}</p>
      </div>
      {q.isLoading && <Spinner />}
      <ErrorBanner error={q.error} />
      {a?.state === "pending" && (
        <Card>
          <p className="text-sm text-ink-soft">{t("ap.pending")}</p>
          <Button className="mt-3" variant="ghost" onClick={() => q.refetch()}>{t("ap.retry")}</Button>
        </Card>
      )}
      {a && a.state !== "pending" && (
        <Card title={
          <span className="flex items-center gap-2">
            {a.organization_name}
            <Badge tone={a.state === "active" ? "brand" : "neg"}>
              {t(a.state === "active" ? "ap.active" : "ap.revoked")}
            </Badge>
          </span>
        }>
          {a.linked_at && <p className="text-xs text-ink-mute">{t("ap.since", { date: fmtDate(a.linked_at) })}</p>}
          {a.state === "revoked" ? (
            <div className="mt-3 space-y-1 text-sm">
              {a.revoke_reason && <p>{t("ap.revokedReason", { reason: a.revoke_reason })}</p>}
              <p className="text-ink-soft">{t("ap.revokedHelp")}</p>
            </div>
          ) : (
            <div className="mt-4 space-y-3">
              <a href={a.anypulse_url} target="_blank" rel="noopener noreferrer"
                 className="inline-flex items-center rounded bg-brand px-3.5 py-2 text-sm font-medium text-white hover:bg-brand-strong">
                {t("ap.open")}
              </a>
              <p className="text-xs text-ink-mute">{t("ap.loginNote")}</p>
              <Button variant="ghost" disabled={resend.isPending} onClick={() => resend.mutate()}>
                {t("ap.resend")}
              </Button>
              {resend.data && <p className="text-sm text-signal-pos">{t("ap.resent", { email: resend.data.email })}</p>}
              <ErrorBanner error={resend.error} />
            </div>
          )}
        </Card>
      )}
    </div>
  );
}
