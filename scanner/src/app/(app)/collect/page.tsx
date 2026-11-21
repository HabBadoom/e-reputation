"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useI18n } from "@/components/providers";
import { DestinationPicker } from "@/components/destination";
import { Badge, Button, Card, ErrorBanner, Spinner } from "@/components/ui";
import { api, type CollectionSession } from "@/lib/api";
import type { MessageKey } from "@/lib/i18n";

// Screenshot first: the upload area is the dominant action, the URL option secondary.
// Upload and URL collection are built in phases 3 and 5; until then they are shown disabled
// with an explicit note rather than as buttons that would do nothing.
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
  const s = session.data;

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-semibold">{t("collect.title")}</h1>
        <p className="mt-1 text-sm text-ink-soft">{t("collect.subtitle")}</p>
      </div>

      <section aria-labelledby="add-screenshot"
               className="rounded border-2 border-dashed border-brand/40 bg-brand-tint/40 px-6 py-10 text-center">
        <h2 id="add-screenshot" className="text-lg font-semibold text-brand-strong">
          {t("collect.importScreenshot")}
        </h2>
        <p className="mx-auto mt-2 max-w-md text-sm text-ink-soft">{t("collect.screenshotKinds")}</p>
        <Button className="mt-5" disabled aria-describedby="upload-not-yet">{t("collect.importScreenshot")}</Button>
        <p id="upload-not-yet" className="mt-2 text-xs text-ink-mute">{t("collect.notYet")}</p>
      </section>

      <div className="flex flex-wrap items-center gap-3">
        <Button variant="ghost" disabled aria-describedby="url-not-yet">{t("collect.addUrl")}</Button>
        <span id="url-not-yet" className="text-xs text-ink-mute">{t("collect.notYet")}</span>
      </div>

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
            <DestinationPicker session={s} />
          </div>
          <div className="mt-5 border-t border-line pt-4">
            <Button className="w-full sm:w-auto" disabled aria-describedby="send-not-yet">{t("collect.send")}</Button>
            <p id="send-not-yet" className="mt-2 text-xs text-ink-mute">{t("collect.sendNotYet")}</p>
          </div>
        </Card>
      )}
    </div>
  );
}
