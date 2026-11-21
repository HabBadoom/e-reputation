"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { useI18n } from "@/components/providers";
import { Badge, Button, Card, ErrorBanner, Spinner } from "@/components/ui";
import { api, type AnyPulseLink, type LinkStart } from "@/lib/api";
import type { MessageKey } from "@/lib/i18n";

function statusKey(l: AnyPulseLink): MessageKey {
  if (l.status === "active") return "links.active";
  return l.revoked_by === "anypulse" ? "links.revokedByAnyPulse" : "links.revokedByScanner";
}

// Links between this Scanner account and AnyPulse organizations. A link is requested here and
// approved by an owner or admin of the organization inside AnyPulse.
export default function LinksPage() {
  const { t, fmtDate, locale } = useI18n();
  const qc = useQueryClient();
  const [pending, setPending] = useState<(LinkStart & { requested_at: string }) | null>(null);
  const [copied, setCopied] = useState(false);
  const links = useQuery({
    queryKey: ["links"],
    queryFn: () => api.get<AnyPulseLink[]>("/links"),
    // While a request waits for approval in AnyPulse, look for the new link regularly.
    refetchInterval: pending ? 5000 : false,
  });
  // Once the organization approves, the new link shows up: stop waiting.
  useEffect(() => {
    if (pending && links.data?.some((l) => l.status === "active" && l.created_at > pending.requested_at)) {
      setPending(null);
    }
  }, [links.data, pending]);
  const start = useMutation({
    mutationFn: () => api.post<LinkStart>("/links/start"),
    onSuccess: (r) => {
      setPending({ ...r, requested_at: new Date().toISOString() });
      setCopied(false);
      window.open(r.authorize_url, "_blank", "noopener");
    },
  });
  const revoke = useMutation({
    mutationFn: (id: string) => api.post<AnyPulseLink>(`/links/${id}/revoke`),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["links"] });
      qc.invalidateQueries({ queryKey: ["session"] });
    },
  });
  const time = (iso: string) =>
    new Intl.DateTimeFormat(locale === "ar" ? "ar-DZ" : locale === "fr" ? "fr-DZ" : "en-GB",
                            { timeStyle: "short" }).format(new Date(iso));

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-semibold">{t("links.title")}</h1>
        <p className="mt-1 max-w-2xl text-sm text-ink-soft">{t("links.intro")}</p>
      </div>

      <div className="space-y-3">
        <Button disabled={start.isPending} onClick={() => start.mutate()}>{t("links.start")}</Button>
        <ErrorBanner error={start.error ?? revoke.error} />
        {pending && (
          <div className="rounded border border-brand/30 bg-brand-tint/50 px-4 py-3 text-sm">
            <p>{t("links.pending", { time: time(pending.expires_at) })}</p>
            <div className="mt-2 flex flex-wrap items-center gap-2">
              <code className="max-w-full truncate rounded bg-paper-raised px-2 py-1 text-xs" dir="ltr">
                {pending.authorize_url}
              </code>
              <Button variant="ghost" onClick={async () => {
                await navigator.clipboard.writeText(pending.authorize_url);
                setCopied(true);
              }}>
                {copied ? t("links.copied") : t("links.copy")}
              </Button>
            </div>
          </div>
        )}
      </div>

      <Card>
        {links.isLoading && <Spinner />}
        <ErrorBanner error={links.error} />
        {links.data && links.data.length === 0 && <p className="text-sm text-ink-mute">{t("links.none")}</p>}
        {links.data && links.data.length > 0 && (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead className="text-xs text-ink-mute">
                <tr>
                  <th className="pb-2 text-start font-medium">{t("links.colOrg")}</th>
                  <th className="pb-2 text-start font-medium">{t("links.colSince")}</th>
                  <th className="pb-2 text-start font-medium">{t("links.colStatus")}</th>
                  <th />
                </tr>
              </thead>
              <tbody>
                {links.data.map((l) => (
                  <tr key={l.id} className="border-t border-line">
                    <td className="py-2 font-medium">{l.organization_name}</td>
                    <td className="py-2">{fmtDate(l.created_at)}</td>
                    <td className="py-2">
                      <Badge tone={l.status === "active" ? "brand" : "neutral"}>{t(statusKey(l))}</Badge>
                    </td>
                    <td className="py-2 text-end">
                      {l.status === "active" && (
                        <Button variant="danger" disabled={revoke.isPending}
                                onClick={() => { if (window.confirm(t("links.revokeConfirm"))) revoke.mutate(l.id); }}>
                          {t("links.revoke")}
                        </Button>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>
    </div>
  );
}
