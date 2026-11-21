"use client";

import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";
import { useI18n } from "@/components/providers";
import { Button } from "@/components/ui";
import {
  ACCEPTED_TYPES, ApiError, api, uploadScreenshots,
  type CollectionItem, type CollectionSession, type FileResult, type UploadResult,
} from "@/lib/api";
import { hasMessage } from "@/lib/i18n";

type QueueState = "uploading" | "added" | "duplicate" | "rejected" | "error";

interface Queued {
  key: string;            // name + size + lastModified: same file picked twice is skipped
  file: File;
  preview: string;        // local object URL, revoked when the entry leaves the queue
  state: QueueState;
  progress: number;
  code: string | null;
}

const fileKey = (f: File) => `${f.name}:${f.size}:${f.lastModified}`;

/** Screenshot-first add zone: drag and drop, file picker, camera on phones. Each file uploads on
 *  its own (progress, retry); the zone stays ready for the next screenshot. */
export function ScreenshotDropzone({ session, disabled }: { session: CollectionSession; disabled?: boolean }) {
  const { t } = useI18n();
  const qc = useQueryClient();
  const picker = useRef<HTMLInputElement>(null);
  const camera = useRef<HTMLInputElement>(null);
  const [queue, setQueue] = useState<Queued[]>([]);
  const [dragging, setDragging] = useState(false);
  const [touch, setTouch] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);

  useEffect(() => setTouch(window.matchMedia("(pointer: coarse)").matches), []);
  useEffect(() => () => queue.forEach((q) => URL.revokeObjectURL(q.preview)), []); // eslint-disable-line react-hooks/exhaustive-deps

  const update = (key: string, patch: Partial<Queued>) =>
    setQueue((qs) => qs.map((q) => (q.key === key ? { ...q, ...patch } : q)));

  const applyResult = (r: UploadResult) => {
    qc.setQueryData(["session", "current"], r.session);
    qc.setQueryData(["items", session.id], r.items);
    qc.invalidateQueries({ queryKey: ["sessions"] });
  };

  const send = async (entry: Queued) => {
    update(entry.key, { state: "uploading", progress: 0, code: null });
    try {
      const r = await uploadScreenshots(session.id, [entry.file], (p) => update(entry.key, { progress: p }));
      const res: FileResult | undefined = r.results[0];
      applyResult(r);
      update(entry.key, { state: res?.status ?? "error", code: res?.code ?? null, progress: 1 });
      if (res?.status === "added") {
        // Confirm, then clear the tile: the screenshot now shows in the collection below.
        setTimeout(() => {
          URL.revokeObjectURL(entry.preview);
          setQueue((qs) => qs.filter((q) => q.key !== entry.key));
        }, 1500);
      }
      return res?.status === "added";
    } catch (e) {
      update(entry.key, { state: "error", code: e instanceof ApiError ? e.code : "network_error" });
      return false;
    }
  };

  const addFiles = async (list: FileList | File[] | null) => {
    if (!list || disabled) return;
    const known = new Set(queue.map((q) => q.key));
    const fresh: Queued[] = [];
    for (const file of Array.from(list)) {
      const key = fileKey(file);
      if (known.has(key)) continue;
      known.add(key);
      const ok = ACCEPTED_TYPES.includes(file.type);
      fresh.push({ key, file, preview: ok ? URL.createObjectURL(file) : "", state: ok ? "uploading" : "rejected",
                   progress: 0, code: ok ? null : "unsupported_type" });
    }
    if (!fresh.length) return;
    setNotice(null);
    setQueue((qs) => [...qs, ...fresh]);
    let added = 0;
    for (const entry of fresh.filter((f) => f.state === "uploading")) {
      if (await send(entry)) added += 1;   // one at a time: honest progress, gentle on mobile data
    }
    if (added) setNotice(t(added === 1 ? "upload.addedOne" : "upload.addedMany", { n: added }));
  };

  const reason = (code: string | null) => {
    const key = `upload.err.${code ?? "unknown"}`;
    return hasMessage(key) ? t(key) : t("upload.err.unknown");
  };

  return (
    <section aria-labelledby="add-screenshot" className="space-y-3">
      <div
        onDragOver={(e) => { e.preventDefault(); if (!disabled) setDragging(true); }}
        onDragLeave={() => setDragging(false)}
        onDrop={(e) => { e.preventDefault(); setDragging(false); void addFiles(e.dataTransfer.files); }}
        className={`rounded border-2 border-dashed px-6 py-10 text-center transition-colors ${
          dragging ? "border-brand bg-brand-tint" : "border-brand/40 bg-brand-tint/40"} ${disabled ? "opacity-60" : ""}`}
      >
        <h2 id="add-screenshot" className="text-lg font-semibold text-brand-strong">{t("collect.importScreenshot")}</h2>
        <p className="mx-auto mt-2 max-w-md text-sm text-ink-soft">{t("collect.screenshotKinds")}</p>
        <div className="mt-5 flex flex-wrap justify-center gap-2">
          <Button disabled={disabled} onClick={() => picker.current?.click()}>{t("upload.choose")}</Button>
          {touch && (
            <Button variant="ghost" disabled={disabled} onClick={() => camera.current?.click()}>{t("upload.camera")}</Button>
          )}
        </div>
        <p className="mt-3 text-xs text-ink-mute">{t("upload.hint")}</p>
        <input ref={picker} type="file" accept={ACCEPTED_TYPES.join(",")} multiple hidden
               onChange={(e) => { void addFiles(e.target.files); e.target.value = ""; }} />
        <input ref={camera} type="file" accept="image/*" capture="environment" hidden
               onChange={(e) => { void addFiles(e.target.files); e.target.value = ""; }} />
      </div>

      {notice && <p role="status" className="text-sm text-signal-pos">{notice}</p>}

      {queue.length > 0 && (
        <ul className="grid grid-cols-2 gap-3 sm:grid-cols-4" aria-live="polite">
          {queue.map((q) => (
            <li key={q.key} className="overflow-hidden rounded border border-line bg-paper-raised text-xs">
              {q.preview ? (
                // eslint-disable-next-line @next/next/no-img-element
                <img src={q.preview} alt="" className="h-28 w-full object-cover object-top" />
              ) : <div className="h-28 bg-paper-sunk" />}
              <div className="space-y-1 p-2">
                <p className="truncate" title={q.file.name}>{q.file.name}</p>
                {q.state === "uploading" && (
                  <div className="h-1.5 rounded bg-paper-sunk" role="progressbar" aria-valuemin={0} aria-valuemax={100}
                       aria-valuenow={Math.round(q.progress * 100)}>
                    <div className="h-1.5 rounded bg-brand" style={{ width: `${Math.round(q.progress * 100)}%` }} />
                  </div>
                )}
                {q.state === "added" && <p className="text-signal-pos">{t("upload.added")}</p>}
                {q.state === "duplicate" && <p className="text-signal-warn">{t("upload.duplicate")}</p>}
                {(q.state === "rejected" || q.state === "error") && <p className="text-signal-neg">{reason(q.code)}</p>}
                <div className="flex gap-2">
                  {q.state === "error" && (
                    <button className="text-brand hover:underline" onClick={() => void send(q)}>{t("upload.retry")}</button>
                  )}
                  {q.state !== "uploading" && q.state !== "added" && (
                    <button className="text-ink-mute hover:underline" onClick={() => {
                      URL.revokeObjectURL(q.preview);
                      setQueue((qs) => qs.filter((x) => x.key !== q.key));
                    }}>{t("upload.dismiss")}</button>
                  )}
                </div>
              </div>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

/** The collection's screenshots, in order: preview, move, remove. */
export function ItemsGrid({ session, items }: { session: CollectionSession; items: CollectionItem[] }) {
  const { t } = useI18n();
  const qc = useQueryClient();
  const reorder = useMutation({
    mutationFn: (ids: string[]) => api.put<CollectionItem[]>(`/sessions/${session.id}/items/order`, { item_ids: ids }),
    onSuccess: (r) => qc.setQueryData(["items", session.id], r),
  });
  const remove = useMutation({
    mutationFn: (id: string) => api.del<CollectionSession>(`/items/${id}`),
    onSuccess: (s) => {
      qc.setQueryData(["session", "current"], s);
      qc.invalidateQueries({ queryKey: ["items", session.id] });
    },
  });
  const move = (i: number, dir: -1 | 1) => {
    const ids = items.map((x) => x.id);
    [ids[i], ids[i + dir]] = [ids[i + dir], ids[i]];
    reorder.mutate(ids);
  };
  const byId = new Map(items.map((x, i) => [x.id, i + 1]));

  if (!items.length) return <p className="text-sm text-ink-mute">{t("items.empty")}</p>;
  return (
    <ol className="grid grid-cols-2 gap-3 sm:grid-cols-4">
      {items.map((it, i) => (
        <li key={it.id} className="overflow-hidden rounded border border-line bg-paper-raised text-xs">
          <div className="relative">
            {it.file && (
              // eslint-disable-next-line @next/next/no-img-element
              <img src={it.file.url} alt={it.file.original_filename} loading="lazy"
                   className="h-36 w-full bg-paper-sunk object-cover object-top" />
            )}
            <span className="absolute start-1.5 top-1.5 rounded bg-ink/80 px-1.5 py-0.5 text-paper">{i + 1}</span>
          </div>
          <div className="space-y-1 p-2">
            <p className="truncate" title={it.file?.original_filename}>{it.file?.original_filename || t("items.screenshot")}</p>
            {it.possible_duplicate_of && (
              <p className="text-signal-warn">{t("items.possibleDuplicate", { n: byId.get(it.possible_duplicate_of) ?? "?" })}</p>
            )}
            {session.is_open && (
              <div className="flex items-center gap-1">
                <button className="rounded px-1.5 py-0.5 hover:bg-paper-sunk disabled:opacity-30" aria-label={t("items.moveUp")}
                        disabled={i === 0 || reorder.isPending} onClick={() => move(i, -1)}>↑</button>
                <button className="rounded px-1.5 py-0.5 hover:bg-paper-sunk disabled:opacity-30" aria-label={t("items.moveDown")}
                        disabled={i === items.length - 1 || reorder.isPending} onClick={() => move(i, 1)}>↓</button>
                <button className="ms-auto text-ink-mute hover:text-signal-neg" disabled={remove.isPending}
                        onClick={() => { if (window.confirm(t("items.removeConfirm"))) remove.mutate(it.id); }}>
                  {t("items.remove")}
                </button>
              </div>
            )}
          </div>
        </li>
      ))}
    </ol>
  );
}
