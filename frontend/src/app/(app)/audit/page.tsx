"use client";

import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { useI18n } from "@/components/providers";
import { Button, ErrorBanner, Spinner } from "@/components/ui";
import { api, type AuditEntry, type Page } from "@/lib/api";

const PAGE = 50;

export default function AuditPage() {
  const { t, fmtDate } = useI18n();
  const [offset, setOffset] = useState(0);
  const q = useQuery({
    queryKey: ["audit", offset],
    queryFn: () => api.get<Page<AuditEntry>>(`/audit-logs?limit=${PAGE}&offset=${offset}`),
    placeholderData: keepPreviousData,
  });
  if (q.isLoading) return <Spinner />;
  if (q.error || !q.data) return <ErrorBanner error={q.error} />;
  return (
    <div className="space-y-6">
      <h1 className="text-2xl font-semibold">{t("audit.title")}</h1>
      <div className="card overflow-x-auto">
        <table className="w-full text-sm">
          <thead className="border-b border-line text-start text-xs uppercase tracking-wide text-ink-mute">
            <tr>
              <th className="px-4 py-2 text-start font-medium">{t("audit.when")}</th>
              <th className="px-4 py-2 text-start font-medium">{t("audit.action")}</th>
              <th className="px-4 py-2 text-start font-medium">{t("audit.actor")}</th>
              <th className="px-4 py-2 text-start font-medium">IP</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-line">
            {q.data.items.map((e) => (
              <tr key={e.id}>
                <td className="whitespace-nowrap px-4 py-2 text-ink-soft">{fmtDate(e.created_at)}</td>
                <td className="px-4 py-2"><code className="font-mono text-xs">{e.action}</code></td>
                <td className="px-4 py-2" dir="ltr">{e.actor_email ?? "—"}</td>
                <td className="px-4 py-2 font-mono text-xs text-ink-mute" dir="ltr">{e.ip ?? "—"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <div className="flex gap-2">
        <Button variant="ghost" disabled={offset === 0} onClick={() => setOffset(offset - PAGE)}><span className="flip-rtl">←</span></Button>
        <Button variant="ghost" disabled={offset + PAGE >= q.data.total} onClick={() => setOffset(offset + PAGE)}><span className="flip-rtl">→</span></Button>
      </div>
    </div>
  );
}
