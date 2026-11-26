"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import Link from "next/link";
import { useEffect, useState } from "react";
import { useI18n } from "@/components/providers";
import { Badge, Button, Card, ErrorBanner, Spinner } from "@/components/ui";
import { RemoteBrowser } from "@/components/remote-browser";
import { ItemsGrid, ScreenshotDropzone } from "@/components/screenshots";
import { api, openLinkInBrowser, type AynPulseAccess, type CollectionItem, type CollectionSession, type LinkResult } from "@/lib/api";
import type { MessageKey } from "@/lib/i18n";

// Screenshot first. Main action: the built-in browser (the agent signs in to the dedicated
// account and captures what they see); importing screenshots already taken is the fallback; a
// pasted link is recorded and opened in the browser, its captures attached to it (phase 5).
// "Envoyer à AynPulse" (phase 7) closes the collection; unvalidated screenshots may be sent and
// arrive marked as such. Scanner users never see what AynPulse does with them.
export default function CollectPage() {
  const { t, fmtDate } = useI18n();
  const [sent, setSent] = useState(false);
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
  // Only whether sending is possible; Scanner users never see AynPulse data.
  const access = useQuery({ queryKey: ["anypulse"], queryFn: () => api.get<AynPulseAccess>("/anypulse") });
  const s = session.data;
  const items = useQuery({
    queryKey: ["items", s?.id],
    queryFn: () => api.get<CollectionItem[]>(`/sessions/${s!.id}/items`),
    enabled: !!s,
    // Screenshots being read update by themselves.
    refetchInterval: (q) => (q.state.data?.some((i) => i.status === "processing") ? 3000 : false),
  });
  const extractionOn = useQuery({ queryKey: ["extraction"], queryFn: () => api.get<{ enabled: boolean }>("/extraction") });
  const extractPending = useMutation({
    mutationFn: (id: string) => api.post<{ queued: number }>(`/sessions/${id}/extract`),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["items"] }),
  });
  useEffect(() => {        // counters follow the screenshots being read
    if (items.dataUpdatedAt) qc.invalidateQueries({ queryKey: ["session", "current"] });
  }, [items.dataUpdatedAt, qc]);

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-semibold">{t("collect.title")}</h1>
        <p className="mt-1 text-sm text-ink-soft">{t("collect.subtitle")}</p>
      </div>

      {s && <RemoteBrowser session={s} disabled={!s.is_open} />}

      {s && (
        <div className="space-y-2">
          <h2 className="text-sm font-medium text-ink-soft">{t("collect.orImport")}</h2>
          <ScreenshotDropzone session={s} disabled={!s.is_open} />
        </div>
      )}

      {s && <LinkForm session={s} />}

      {sent && (
        <div role="status" className="rounded border border-signal-pos/40 bg-signal-pos/10 px-4 py-3 text-sm">
          {t("collect.sent")}
        </div>
      )}

      {s?.expires_soon && s.expires_at && (
        <div role="alert" className="rounded border border-signal-warn/40 bg-signal-warn/10 px-4 py-3 text-sm">
          {t("collect.expiresSoon", { date: fmtDate(s.expires_at) })}
        </div>
      )}

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
            {(() => {
              const read = (items.data ?? []).filter((i) => i.extraction?.status === "done");
              const done = read.filter((i) => i.extraction?.validated_at);
              const next = read.find((i) => !i.extraction?.validated_at);
              if (!read.length) return null;
              return (
                <div className="mb-3 flex flex-wrap items-center gap-3 text-sm">
                  <span className={done.length === read.length ? "font-medium text-signal-pos" : "text-ink-soft"}>
                    {done.length === read.length ? "✓ " : ""}{t("collect.validatedCount", { n: done.length, total: read.length })}
                  </span>
                  {next && (
                    <Link href={`/collect/items/${next.id}?s=${s.id}&n=${(items.data ?? []).indexOf(next) + 1}`}
                          className="font-medium text-brand hover:underline">
                      {t("collect.reviewNext")} →
                    </Link>
                  )}
                </div>
              );
            })()}
            {items.isLoading ? <Spinner /> : <ItemsGrid session={s} items={items.data ?? []} />}
            <ErrorBanner error={items.error ?? extractPending.error} />
            {s.is_open && extractionOn.data?.enabled &&
              items.data?.some((i) => i.source_type === "screenshot" && (i.status === "pending" || i.status === "failed")) && (
              <Button variant="ghost" className="mt-3" disabled={extractPending.isPending}
                      onClick={() => extractPending.mutate(s.id)}>
                {t("collect.extractPending")}
              </Button>
            )}
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
          <SendBox session={s} items={items.data ?? []} revoked={access.data?.state === "revoked"}
                   onSent={() => setSent(true)} />
        </Card>
      )}
    </div>
  );
}

function LinkForm({ session }: { session: CollectionSession }) {
  const { t } = useI18n();
  const qc = useQueryClient();
  const [url, setUrl] = useState("");
  const [note, setNote] = useState<string | null>(null);
  const add = useMutation({
    mutationFn: (u: string) => api.post<LinkResult>(`/sessions/${session.id}/links`, { url: u }),
    onSuccess: (r) => {
      setUrl("");
      setNote(r.created ? null : t("collect.linkAlready"));
      qc.invalidateQueries({ queryKey: ["items", session.id] });
      qc.invalidateQueries({ queryKey: ["session", "current"] });
      openLinkInBrowser(r.item_id);
    },
  });
  return (
    <form className="space-y-1" onSubmit={(e) => { e.preventDefault(); if (url.trim()) add.mutate(url.trim()); }}>
      <label htmlFor="link-input" className="text-sm font-medium text-ink-soft">{t("collect.linkLabel")}</label>
      <div className="flex flex-wrap gap-2">
        <input id="link-input" className="field min-w-0 flex-1" dir="ltr" inputMode="url" autoComplete="off"
               placeholder="https://www.facebook.com/…" value={url} disabled={!session.is_open}
               onChange={(e) => { setUrl(e.target.value); setNote(null); }} />
        <Button type="submit" variant="ghost" disabled={!session.is_open || !url.trim() || add.isPending}>
          {t("collect.linkAdd")}
        </Button>
      </div>
      <p className="text-xs text-ink-mute">{note ?? t("collect.linkHint")}</p>
      <ErrorBanner error={add.error} />
    </form>
  );
}

function SendBox({ session, items, revoked, onSent }: {
  session: CollectionSession; items: CollectionItem[]; revoked: boolean; onSent: () => void;
}) {
  const { t } = useI18n();
  const qc = useQueryClient();
  const shots = items.filter((i) => i.source_type === "screenshot");
  const reading = shots.some((i) => i.status === "processing");
  const unvalidated = shots.filter((i) => !i.extraction?.validated_at).length;
  const send = useMutation({
    mutationFn: () => api.post<CollectionSession>(`/sessions/${session.id}/send`),
    onSuccess: () => {
      onSent();
      qc.invalidateQueries({ queryKey: ["session"] });
      qc.invalidateQueries({ queryKey: ["sessions"] });
      qc.invalidateQueries({ queryKey: ["items"] });
    },
  });
  const why = revoked ? t("collect.sendRevoked") : !shots.length ? t("collect.sendEmpty")
    : reading ? t("collect.sendReading") : null;
  return (
    <div className="mt-5 border-t border-line pt-4">
      <Button className="w-full sm:w-auto" disabled={!!why || !session.is_open || send.isPending}
              onClick={() => {
                const msg = t("collect.sendConfirm", { n: shots.length })
                  + (unvalidated ? "\n\n" + t("collect.sendUnvalidated", { n: unvalidated }) : "");
                if (window.confirm(msg)) send.mutate();
              }}>
        {send.isPending ? t("collect.sending") : t("collect.send")}
      </Button>
      <p className="mt-2 text-xs text-ink-mute">{why ?? t("collect.sendHint")}</p>
      <ErrorBanner error={send.error} />
    </div>
  );
}
