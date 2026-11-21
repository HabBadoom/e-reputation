"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useSearchParams } from "next/navigation";
import { Suspense, useState } from "react";
import { useI18n } from "@/components/providers";
import { Badge, Button, Card, ErrorBanner, Spinner } from "@/components/ui";
import { api } from "@/lib/api";
import { useCan } from "@/lib/session";

interface LinkRequest {
  account_email: string;
  account_name: string;
  status: "pending" | "approved" | "declined" | "expired";
  expires_at: string;
  organization_name: string;
  can_decide: boolean;
}

interface ScannerLink {
  id: string;
  account_email: string;
  account_name: string;
  status: "active" | "revoked";
  created_at: string;
  revoked_at: string | null;
  revoked_by: "scanner" | "anypulse" | null;
}

// Consent screen for an Aynpulse Scanner link request (opened from Scanner with ?request=…),
// and the list of Scanner accounts linked to the current organization.
function Consent({ token }: { token: string }) {
  const { t } = useI18n();
  const qc = useQueryClient();
  const [done, setDone] = useState<"approved" | "declined" | null>(null);
  const req = useQuery({
    queryKey: ["scanner-link-request", token],
    queryFn: () => api.post<LinkRequest>("/integrations/scanner/link-requests/inspect", { token }),
    retry: false,
  });
  const decide = useMutation({
    mutationFn: (action: "approve" | "decline") =>
      api.post(`/integrations/scanner/link-requests/${action}`, { token }),
    onSuccess: (_d, action) => {
      setDone(action === "approve" ? "approved" : "declined");
      qc.invalidateQueries({ queryKey: ["scanner-links"] });
    },
  });
  const r = req.data;

  return (
    <Card title={t("scanner.requestTitle")}>
      {req.isLoading && <Spinner />}
      <ErrorBanner error={req.error ?? decide.error} />
      {r && done === "approved" && (
        <p className="text-sm text-signal-pos">
          {t("scanner.approved", { name: r.account_name, org: r.organization_name })}
        </p>
      )}
      {r && done === "declined" && <p className="text-sm text-ink-soft">{t("scanner.declined")}</p>}
      {r && !done && (
        <div className="space-y-4">
          <p className="text-sm">
            {t("scanner.requestBody", { name: r.account_name, email: r.account_email, org: r.organization_name })}
          </p>
          {r.status === "expired" && <p className="text-sm text-signal-warn">{t("scanner.expired")}</p>}
          {(r.status === "approved" || r.status === "declined") && (
            <p className="text-sm text-ink-soft">{t("scanner.alreadyDecided")}</p>
          )}
          {r.status === "pending" && (
            <>
              <p className="rounded border border-signal-warn/30 bg-signal-warn/5 px-3 py-2 text-sm text-signal-warn">
                {t("scanner.requestWarning")}
              </p>
              <p className="text-xs text-ink-mute">{t("scanner.otherOrg")}</p>
              {r.can_decide ? (
                <div className="flex gap-2">
                  <Button disabled={decide.isPending} onClick={() => decide.mutate("approve")}>
                    {t("scanner.approve")}
                  </Button>
                  <Button variant="ghost" disabled={decide.isPending} onClick={() => decide.mutate("decline")}>
                    {t("scanner.decline")}
                  </Button>
                </div>
              ) : (
                <p className="text-sm text-ink-soft">{t("scanner.cannotDecide")}</p>
              )}
            </>
          )}
        </div>
      )}
    </Card>
  );
}

function Links() {
  const { t, fmtDate } = useI18n();
  const qc = useQueryClient();
  const canManage = useCan("scanner:link");
  const links = useQuery({
    queryKey: ["scanner-links"],
    queryFn: () => api.get<ScannerLink[]>("/integrations/scanner/links"),
  });
  const revoke = useMutation({
    mutationFn: (id: string) => api.post<ScannerLink>(`/integrations/scanner/links/${id}/revoke`),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["scanner-links"] }),
  });

  return (
    <Card title={t("scanner.linked")}>
      {links.isLoading && <Spinner />}
      <ErrorBanner error={links.error ?? revoke.error} />
      {links.data && links.data.length === 0 && <p className="text-sm text-ink-mute">{t("scanner.none")}</p>}
      {links.data && links.data.length > 0 && (
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead className="text-xs text-ink-mute">
              <tr>
                <th className="pb-2 text-start font-medium">{t("scanner.colAccount")}</th>
                <th className="pb-2 text-start font-medium">{t("scanner.colSince")}</th>
                <th className="pb-2 text-start font-medium">{t("scanner.colStatus")}</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {links.data.map((l) => (
                <tr key={l.id} className="border-t border-line">
                  <td className="py-2">
                    <div>{l.account_name}</div>
                    <div className="text-xs text-ink-mute" dir="ltr">{l.account_email}</div>
                  </td>
                  <td className="py-2">{fmtDate(l.created_at)}</td>
                  <td className="py-2">
                    <Badge tone={l.status === "active" ? "brand" : "neutral"}>
                      {t(l.status === "active" ? "scanner.statusActive" : "scanner.statusRevoked")}
                    </Badge>
                  </td>
                  <td className="py-2 text-end">
                    {canManage && l.status === "active" && (
                      <Button variant="danger" disabled={revoke.isPending}
                              onClick={() => { if (window.confirm(t("scanner.revokeConfirm"))) revoke.mutate(l.id); }}>
                        {t("scanner.revoke")}
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
  );
}

function ScannerLinksPage() {
  const { t } = useI18n();
  const token = useSearchParams().get("request");
  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-xl font-semibold">{t("scanner.title")}</h1>
        <p className="mt-1 text-sm text-ink-soft">{t("scanner.intro")}</p>
      </div>
      {token && <Consent token={token} />}
      <Links />
    </div>
  );
}

export default function Page() {
  return <Suspense><ScannerLinksPage /></Suspense>;
}
