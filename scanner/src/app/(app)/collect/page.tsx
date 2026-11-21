"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useI18n } from "@/components/providers";
import { Badge, Button, Card, ErrorBanner, Spinner } from "@/components/ui";
import { ItemsGrid, ScreenshotDropzone } from "@/components/screenshots";
import { api, type AnyPulseAccess, type CollectionItem, type CollectionSession } from "@/lib/api";
import type { MessageKey } from "@/lib/i18n";

// Screenshot first: the upload area is the dominant action, the URL option secondary. After each
// screenshot the page stays on this add-item screen with the same collection. URL collection
// (phase 5) and sending (phase 7) are shown disabled with a note, never as buttons that do nothing.
export default function CollectPage() {
  const { t, fmtDate } = useI18n();
  const qc = useQueryClient();
  const session = useQuery({
    queryKey: ["session", "current"],
    queryFn: () => api.get<CollectionSession>("/sessions/current"),
  });
  const cancel = useMutation({
    mutationFn: (id: string) => api.post<CollectionSession>(`/sessions/${id}/cancel`),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["session"] });
      qc.invalidateQueries({ queryKey: ["sessions"] });
    },
  });
  // Only whether sending is possible; Scanner users never see AnyPulse data.
  const access = useQuery({ queryKey: ["anypulse"], queryFn: () => api.get<AnyPulseAccess>("/anypulse") });
  const s = session.data;
  const items = useQuery({
    queryKey: ["items", s?.id],
    queryFn: () => api.get<CollectionItem[]>(`/sessions/${s!.id}/items`),
    enabled: !!s,
  });

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-semibold">{t("collect.title")}</h1>
        <p className="mt-1 text-sm text-ink-soft">{t("collect.subtitle")}</p>
      </div>

      {s && <ScreenshotDropzone session={s} disabled={!s.is_open} />}

      <div className="flex flex-wrap items-center gap-3">
        <Button variant="ghost" disabled aria-describedby="url-not-yet">{t("collect.addUrl")}</Button>
        <span id="url-not-yet" className="text-xs text-ink-mute">{t("collect.urlNotYet")}</span>
      </div>

      {access.data?.state === "revoked" && (
        <div role="alert" className="rounded border border-signal-neg/30 bg-signal-neg/5 px-4 py-3 text-sm text-signal-neg">
          <p>{t("access.revoked")}</p>
          {access.data.revoke_reason && <p className="mt-1">{t("access.reason", { reason: access.data.revoke_reason })}</p>}
        </div>
      )}

      {session.isLoading && <Spinner />}
      <ErrorBanner error={session.error ?? cancel.error} />
      {s && (
        <Card
          title={
            <span className="flex items-center gap-2">
              {t("collect.current")}
              <Badge tone="brand">{t(`status.${s.status}` as MessageKey)}</Badge>
            </span>
          }
          action={
            <button className="text-xs text-ink-soft hover:text-signal-neg"
                    disabled={cancel.isPending}
                    onClick={() => { if (window.confirm(t("collect.cancelConfirm"))) cancel.mutate(s.id); }}>
              {t("collect.cancel")}
            </button>
          }
        >
          <p className="text-sm font-medium">{s.title || t("collect.untitled")}</p>
          <p className="text-xs text-ink-mute">{t("collect.started", { date: fmtDate(s.created_at) })}</p>
          <div className="mt-4">
            {items.isLoading ? <Spinner /> : <ItemsGrid session={s} items={items.data ?? []} />}
            <ErrorBanner error={items.error} />
          </div>
          <dl className="mt-4 grid grid-cols-2 gap-3 sm:grid-cols-3">
            {([
              ["counters.screenshots", s.counters.screenshots],
              ["counters.urls", s.counters.urls],
              ["counters.imports", s.counters.imports],
              ["counters.needsReview", s.counters.needs_review],
              ["counters.ready", s.counters.ready],
              ["counters.failed", s.counters.failed],
            ] as [MessageKey, number][]).map(([k, v]) => (
              <div key={k} className="rounded border border-line px-3 py-2">
                <dt className="text-xs text-ink-mute">{t(k)}</dt>
                <dd className="text-lg font-semibold tabular-nums">{v}</dd>
              </div>
            ))}
          </dl>
          <div className="mt-5 border-t border-line pt-4">
            <Button className="w-full sm:w-auto" disabled aria-describedby="send-not-yet">{t("collect.send")}</Button>
            <p id="send-not-yet" className="mt-2 text-xs text-ink-mute">{t("collect.sendNotYet")}</p>
          </div>
        </Card>
      )}
    </div>
  );
}
