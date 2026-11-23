"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import Link from "next/link";
import { useParams, useSearchParams } from "next/navigation";
import { Suspense, useState } from "react";
import { useI18n } from "@/components/providers";
import { ItemStatus } from "@/components/screenshots";
import { Badge, Button, ErrorBanner, Spinner } from "@/components/ui";
import { api, type CollectionItem, type ExtractedEntry } from "@/lib/api";
import type { MessageKey } from "@/lib/i18n";

// Review of one screenshot (phase 4): the image next to what was read from it. The agent fixes
// misread names or texts and deletes what is not content. Collection data only: no analysis.
export default function ReviewRoute() {
  return <Suspense fallback={<Spinner />}><ReviewPage /></Suspense>;
}

function ReviewPage() {
  const { t } = useI18n();
  const qc = useQueryClient();
  const { id } = useParams<{ id: string }>();
  const params = useSearchParams();
  const sessionId = params.get("s") ?? "";
  const number = params.get("n") ?? "";

  const items = useQuery({
    queryKey: ["items", sessionId],
    queryFn: () => api.get<CollectionItem[]>(`/sessions/${sessionId}/items`),
    enabled: !!sessionId,
    refetchInterval: (q) => (q.state.data?.find((i) => i.id === id)?.status === "processing" ? 3000 : false),
  });
  const item = items.data?.find((i) => i.id === id);
  const entries = useQuery({
    queryKey: ["entries", id, item?.status],
    queryFn: () => api.get<ExtractedEntry[]>(`/items/${id}/entries`),
    enabled: !!item && item.status !== "processing",
  });
  const readAgain = useMutation({
    mutationFn: () => api.post<CollectionItem[]>(`/items/${id}/extract`),
    onSuccess: (r) => qc.setQueryData(["items", sessionId], r),
  });

  if (items.isLoading) return <Spinner />;
  if (!item) {
    return (
      <div className="space-y-4">
        <BackLink />
        <p className="text-sm text-ink-soft">{t("review.notFound")}</p>
        <ErrorBanner error={items.error} />
      </div>
    );
  }
  const ex = item.extraction;
  const where = [ex?.context.page_or_group, ex?.context.dialog_title].filter(Boolean).join(" · ");
  const byId = new Map((entries.data ?? []).map((e) => [e.id, e]));

  return (
    <div className="space-y-4">
      <BackLink />
      <div>
        <h1 className="text-xl font-semibold">{t("review.title", { n: number || item.position + 1 })}</h1>
        <p className="mt-1 max-w-prose text-sm text-ink-soft">{t("review.intro")}</p>
      </div>

      <div className="grid gap-6 lg:grid-cols-[minmax(0,5fr)_minmax(0,7fr)]">
        <div className="lg:sticky lg:top-4 lg:self-start">
          {item.file && (
            <a href={item.file.url} target="_blank" rel="noreferrer">
              {/* eslint-disable-next-line @next/next/no-img-element */}
              <img src={item.file.url} alt={item.file.original_filename}
                   className="max-h-[80vh] w-full rounded border border-line bg-paper-sunk object-contain" />
            </a>
          )}
          <p className="mt-2 truncate text-xs text-ink-mute">{item.file?.original_filename}</p>
        </div>

        <div className="space-y-3">
          <div className="flex flex-wrap items-center gap-2 text-sm">
            <ItemStatus item={item} />
            {ex?.confidence != null && (
              <span className="text-ink-mute">· {t("review.confidence", { p: Math.round(ex.confidence * 100) })}</span>
            )}
            {(ex?.issues ?? []).map((i) => (
              <Badge key={i} tone={i === "background_ignored" ? "neutral" : "warn"}>
                {t(`review.issue.${i}` as MessageKey)}
              </Badge>
            ))}
            <Button variant="ghost" className="ms-auto py-1 text-xs"
                    disabled={readAgain.isPending || item.status === "processing"}
                    onClick={() => { if (window.confirm(t("review.readAgainConfirm"))) readAgain.mutate(); }}>
              {t("review.readAgain")}
            </Button>
          </div>
          {where && <p className="text-sm text-ink-soft" dir="auto">{t("review.context", { where })}</p>}
          <ErrorBanner error={readAgain.error ?? entries.error} />

          {item.status === "processing" && <p className="text-sm text-ink-soft">{t("review.waiting")}</p>}
          {entries.isLoading && <Spinner />}
          {entries.data && entries.data.length === 0 && (
            <p className="text-sm text-ink-soft">{t("review.empty")}</p>
          )}
          <ol className="space-y-2">
            {(entries.data ?? []).map((e) => (
              <EntryCard key={e.id} entry={e} depth={depth(e, byId)} />
            ))}
          </ol>
        </div>
      </div>
    </div>
  );
}

function depth(e: ExtractedEntry, byId: Map<string, ExtractedEntry>): number {
  let d = 0;
  for (let p = e.parent_id; p && d < 3; p = byId.get(p)?.parent_id ?? null) d++;
  return d;
}

function BackLink() {
  const { t } = useI18n();
  return <Link href="/collect" className="text-sm text-brand hover:underline">{t("review.back")}</Link>;
}

const BORDER: Record<ExtractedEntry["kind"], string> = {
  post: "border-brand",
  comment: "border-signal-pos",
  reply: "border-signal-warn",
};

function EntryCard({ entry: e, depth }: { entry: ExtractedEntry; depth: number }) {
  const { t, fmtDate } = useI18n();
  const qc = useQueryClient();
  const [editing, setEditing] = useState(false);
  const [author, setAuthor] = useState(e.author_name);
  const [text, setText] = useState(e.text);
  const save = useMutation({
    mutationFn: () => api.patch<ExtractedEntry>(`/entries/${e.id}`, { author_name: author, text }),
    onSuccess: () => {
      setEditing(false);
      qc.invalidateQueries({ queryKey: ["entries", e.item_id] });
    },
  });
  const remove = useMutation({
    mutationFn: () => api.del<void>(`/entries/${e.id}`),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["entries", e.item_id] });
      qc.invalidateQueries({ queryKey: ["items"] });
    },
  });
  const counts = [
    e.reactions_count != null && t("review.reactions", { n: e.reactions_count }),
    e.comments_count != null && t("review.comments", { n: e.comments_count }),
    e.shares_count != null && t("review.shares", { n: e.shares_count }),
  ].filter(Boolean);

  return (
    <li className={`rounded border-s-4 bg-paper-raised p-3 shadow-sm ${BORDER[e.kind]} ${e.duplicate_of ? "opacity-60" : ""}`}
        style={{ marginInlineStart: `${Math.min(depth, 2) * 1.25}rem` }}>
      <div className="flex flex-wrap items-center gap-x-2 gap-y-1 text-xs text-ink-mute">
        <span className="font-semibold uppercase tracking-wide">{t(`review.kind.${e.kind}` as MessageKey)}</span>
        {e.relative_time && (
          <span title={e.published_at ? fmtDate(e.published_at) : undefined} dir="auto">· {e.relative_time}</span>
        )}
        {counts.map((c) => <span key={String(c)}>· {c}</span>)}
        {e.author_is_page && <Badge>{t("review.page")}</Badge>}
        {e.edited_by_agent && <Badge tone="brand">{t("review.edited")}</Badge>}
        {e.text_truncated && <Badge tone="warn">{t("review.truncated")}</Badge>}
        {e.partially_visible && <Badge tone="warn">{t("review.partial")}</Badge>}
        {e.duplicate_of && <Badge>{t("review.duplicate")}</Badge>}
      </div>

      {editing ? (
        <form className="mt-2 space-y-2" onSubmit={(ev) => { ev.preventDefault(); save.mutate(); }}>
          <label className="block text-xs text-ink-soft">
            {t("review.author")}
            <input className="field mt-1 w-full" dir="auto" value={author} maxLength={200}
                   onChange={(ev) => setAuthor(ev.target.value)} />
          </label>
          <label className="block text-xs text-ink-soft">
            {t("review.text")}
            <textarea className="field mt-1 min-h-28 w-full" dir="auto" value={text} maxLength={20000}
                      onChange={(ev) => setText(ev.target.value)} />
          </label>
          <ErrorBanner error={save.error} />
          <div className="flex gap-2">
            <Button type="submit" disabled={save.isPending}>{t("review.save")}</Button>
            <Button type="button" variant="ghost"
                    onClick={() => { setEditing(false); setAuthor(e.author_name); setText(e.text); }}>
              {t("review.cancel")}
            </Button>
          </div>
        </form>
      ) : (
        <>
          <p className="mt-1 text-sm font-semibold" dir="auto">
            {e.author_name || <span className="font-normal italic text-ink-mute">{t("review.noAuthor")}</span>}
          </p>
          {e.text && <p className="mt-1 whitespace-pre-line text-sm" dir="auto">{e.text}</p>}
          {e.image_text && (
            <p className="mt-2 rounded bg-paper-sunk px-2 py-1 text-sm text-ink-soft" dir="auto">
              <span className="text-xs text-ink-mute">{t("review.imageText")} : </span>{e.image_text}
            </p>
          )}
          <div className="mt-2 flex gap-3 text-xs">
            <button className="text-brand hover:underline" onClick={() => setEditing(true)}>{t("review.edit")}</button>
            <button className="text-ink-mute hover:text-signal-neg" disabled={remove.isPending}
                    onClick={() => { if (window.confirm(t("review.deleteConfirm"))) remove.mutate(); }}>
              {t("review.delete")}
            </button>
          </div>
        </>
      )}
    </li>
  );
}
