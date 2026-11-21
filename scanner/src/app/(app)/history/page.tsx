"use client";

import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { useI18n } from "@/components/providers";
import { Badge, Button, Card, ErrorBanner, Spinner } from "@/components/ui";
import { api, type CollectionSession, type Page } from "@/lib/api";
import type { MessageKey } from "@/lib/i18n";

const LIMIT = 20;

// Collection history: collection metadata and transmission status only. The analysis of a
// collection lives in AnyPulse, never here.
export default function HistoryPage() {
  const { t, fmtDate } = useI18n();
  const [offset, setOffset] = useState(0);
  const q = useQuery({
    queryKey: ["sessions", offset],
    queryFn: () => api.get<Page<CollectionSession>>(`/sessions?limit=${LIMIT}&offset=${offset}`),
  });

  return (
    <div className="space-y-6">
      <h1 className="text-2xl font-semibold">{t("history.title")}</h1>
      {q.isLoading && <Spinner />}
      <ErrorBanner error={q.error} />
      {q.data && (
        <Card>
          {q.data.items.length === 0 ? (
            <p className="text-sm text-ink-mute">{t("history.empty")}</p>
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead className="text-start text-xs text-ink-mute">
                  <tr>
                    <th className="pb-2 text-start font-medium">{t("history.date")}</th>
                    <th className="pb-2 text-start font-medium">{t("history.items")}</th>
                    <th className="pb-2 text-start font-medium">{t("history.status")}</th>
                    <th className="pb-2 text-start font-medium">{t("history.anypulseRef")}</th>
                  </tr>
                </thead>
                <tbody>
                  {q.data.items.map((s) => (
                    <tr key={s.id} className="border-t border-line">
                      <td className="py-2">
                        <div>{s.title || t("collect.untitled")}</div>
                        <div className="text-xs text-ink-mute">{fmtDate(s.created_at)}</div>
                      </td>
                      <td className="py-2 tabular-nums">{s.counters.items}</td>
                      <td className="py-2">
                        <Badge tone={s.is_open ? "brand" : s.status === "failed" ? "neg" : "neutral"}>
                          {t(`status.${s.status}` as MessageKey)}
                        </Badge>
                      </td>
                      <td className="py-2 font-mono text-xs text-ink-mute">
                        {s.anypulse_collection_id ? s.anypulse_collection_id.slice(0, 8) : "—"}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
          {q.data.total > LIMIT && (
            <div className="mt-4 flex justify-between">
              <Button variant="ghost" disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - LIMIT))}>
                {t("common.previous")}
              </Button>
              <Button variant="ghost" disabled={offset + LIMIT >= q.data.total} onClick={() => setOffset(offset + LIMIT)}>
                {t("common.next")}
              </Button>
            </div>
          )}
        </Card>
      )}
    </div>
  );
}
