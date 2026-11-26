"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useCallback, useEffect, useRef, useState, type FormEvent, type KeyboardEvent, type PointerEvent } from "react";
import { useI18n } from "@/components/providers";
import { Button } from "@/components/ui";
import { api, browserSocketUrl, OPEN_LINK_EVENT, type BrowserInfo, type CollectionSession } from "@/lib/api";
import { hasMessage, type MessageKey } from "@/lib/i18n";

// Scanner's built-in browser: a real Chromium on our servers, streamed as JPEG frames over a
// WebSocket. The agent signs in to the dedicated account inside it and presses "Capture"; the
// capture lands in the current collection like an imported screenshot (with its page address).
// Nothing is automated: every click, scroll and keystroke comes from the agent.

type Phase = "idle" | "connecting" | "live";
type Remote = { width: number; height: number; mobile: boolean };
const KEYS: Record<string, string> = {
  Enter: "Enter", Backspace: "Backspace", Tab: "Tab", Escape: "Escape", Delete: "Delete",
  ArrowUp: "ArrowUp", ArrowDown: "ArrowDown", ArrowLeft: "ArrowLeft", ArrowRight: "ArrowRight",
  Home: "Home", End: "End", PageUp: "PageUp", PageDown: "PageDown",
};

function isTouch() {
  return typeof window !== "undefined" && window.matchMedia("(pointer: coarse)").matches;
}

export function RemoteBrowser({ session, disabled }: { session: CollectionSession; disabled?: boolean }) {
  const { t, fmtDate } = useI18n();
  const qc = useQueryClient();
  const info = useQuery({ queryKey: ["browser"], queryFn: () => api.get<BrowserInfo>("/browser") });

  const [phase, setPhase] = useState<Phase>("idle");
  const [remote, setRemote] = useState<Remote | null>(null);
  const [frame, setFrame] = useState<string | null>(null);
  const [url, setUrl] = useState("");
  const [address, setAddress] = useState("");
  const [notice, setNotice] = useState<{ tone: "ok" | "err"; text: string } | null>(null);
  const [capturing, setCapturing] = useState(false);
  const [link, setLink] = useState<{ id: string; url: string } | null>(null);
  const [thread, setThread] = useState<{ n: number; stopping: boolean } | null>(null);

  const ws = useRef<WebSocket | null>(null);
  const screen = useRef<HTMLImageElement | null>(null);
  const keyboard = useRef<HTMLInputElement | null>(null);
  const drag = useRef<{ x: number; y: number; moved: boolean } | null>(null);
  const frameUrl = useRef<string | null>(null);
  const box = useRef<HTMLElement | null>(null);

  const send = useCallback((msg: Record<string, unknown>) => {
    if (ws.current?.readyState === WebSocket.OPEN) ws.current.send(JSON.stringify(msg));
  }, []);

  const say = useCallback((tone: "ok" | "err", key: string) => {
    const k = (hasMessage(key) ? key : "browser.err.unknown") as MessageKey;
    setNotice({ tone, text: t(k) });
  }, [t]);

  const close = useCallback(() => {
    ws.current?.close(1000);
    ws.current = null;
  }, []);

  useEffect(() => () => {           // leaving the page closes the browser (its session is saved)
    ws.current?.close(1000);
    if (frameUrl.current) URL.revokeObjectURL(frameUrl.current);
  }, []);

  async function open(startUrl?: string, linkItemId?: string) {
    setNotice(null);
    setPhase("connecting");
    let i: BrowserInfo;
    try {
      i = await api.get<BrowserInfo>("/browser");   // also refreshes an expired access cookie
    } catch {
      setPhase("idle");
      say("err", "browser.err.connection");
      return;
    }
    const touch = isTouch();
    const avail = (box.current?.clientWidth ?? window.innerWidth) - 32;
    const width = touch ? Math.round(Math.min(480, Math.max(320, avail))) : 1280;
    const height = touch ? Math.round(Math.min(1000, Math.max(480, window.innerHeight - 200))) : 800;
    const sock = new WebSocket(browserSocketUrl(i));
    sock.binaryType = "blob";
    ws.current = sock;
    sock.onopen = () => sock.send(JSON.stringify({
      type: "start", session_id: session.id, width, height, mobile: touch, url: startUrl,
      link_item_id: linkItemId,
    }));
    sock.onmessage = (ev) => {
      if (ev.data instanceof Blob) {
        const next = URL.createObjectURL(ev.data);
        if (frameUrl.current) URL.revokeObjectURL(frameUrl.current);
        frameUrl.current = next;
        setFrame(next);
        return;
      }
      const m = JSON.parse(ev.data as string);
      if (m.type === "ready") {
        setRemote({ width: m.width, height: m.height, mobile: m.mobile });
        setPhase("live");
      } else if (m.type === "thread_progress") {
        setThread((th) => ({ n: m.n, stopping: th?.stopping ?? false }));
        qc.invalidateQueries({ queryKey: ["items", session.id] });
      } else if (m.type === "thread_done") {
        setThread(null);
        const why = `browser.threadEnd.${m.reason}`;
        setNotice({
          tone: m.added ? "ok" : "err",
          text: `${t("browser.threadDone", { n: m.added })} ${hasMessage(why) ? t(why as MessageKey) : ""}`.trim(),
        });
        qc.invalidateQueries({ queryKey: ["items", session.id] });
        qc.invalidateQueries({ queryKey: ["session"] });
      } else if (m.type === "linked") {
        setLink(m.item_id ? { id: m.item_id, url: m.url } : null);
      } else if (m.type === "url") {
        setUrl(m.url);
        if (document.activeElement?.id !== "browser-address") setAddress(m.url);
      } else if (m.type === "captured") {
        setCapturing(false);
        const st = m.result.status;
        if (st === "added") say("ok", "browser.captured");
        else if (st === "duplicate") say("ok", "browser.capturedDuplicate");
        else say("err", `upload.err.${m.result.code ?? "unknown"}`);
        qc.invalidateQueries({ queryKey: ["items", session.id] });
        qc.invalidateQueries({ queryKey: ["session"] });
        qc.invalidateQueries({ queryKey: ["sessions"] });
      } else if (m.type === "error") {
        setCapturing(false);
        const code = String(m.code);
        say("err", hasMessage(`browser.err.${code}`) ? `browser.err.${code}`
          : hasMessage(`upload.err.${code}`) ? `upload.err.${code}` : "browser.err.unknown");
      } else if (m.type === "closed") {
        if (m.reason !== "closed") {
          const k = `browser.closed.${m.reason}`;
          say("err", hasMessage(k) ? k : "browser.closed.other");
        }
      }
    };
    sock.onclose = (ev) => {
      if (ws.current === sock) ws.current = null;
      setPhase("idle");
      setCapturing(false);
      setRemote(null);
      setFrame(null);
      setLink(null);
      setThread(null);
      if (ev.code === 4401) say("err", "browser.closed.session_revoked");
      else if (ev.code !== 1000 && ev.code !== 1005) setNotice((n) => n ?? { tone: "err", text: t("browser.err.connection") });
      qc.invalidateQueries({ queryKey: ["browser"] });
    };
  }

  // A pasted link (collect page or link card) opens here; captures are attached to it.
  const phaseRef = useRef(phase);
  phaseRef.current = phase;
  useEffect(() => {
    const onOpen = (ev: Event) => {
      const { itemId } = (ev as CustomEvent<{ itemId: string }>).detail;
      box.current?.scrollIntoView({ behavior: "smooth", block: "start" });
      if (phaseRef.current === "live") send({ type: "link", item_id: itemId });
      else if (phaseRef.current === "idle") void open(undefined, itemId);
    };
    window.addEventListener(OPEN_LINK_EVENT, onOpen);
    return () => window.removeEventListener(OPEN_LINK_EVENT, onOpen);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [send, session.id]);

  const forget = useMutation({
    mutationFn: () => api.del<void>("/browser/profile"),
    onSuccess: () => {
      say("ok", "browser.forgotten");
      qc.invalidateQueries({ queryKey: ["browser"] });
    },
  });

  // ---- input: page coordinates are scaled to the remote viewport ---------------------------
  function point(e: { clientX: number; clientY: number }) {
    const r = screen.current!.getBoundingClientRect();
    return { x: ((e.clientX - r.left) * remote!.width) / r.width, y: ((e.clientY - r.top) * remote!.height) / r.height };
  }
  function onPointerDown(e: PointerEvent<HTMLImageElement>) {
    drag.current = { x: e.clientX, y: e.clientY, moved: false };
    e.currentTarget.setPointerCapture(e.pointerId);
  }
  function onPointerMove(e: PointerEvent<HTMLImageElement>) {
    const d = drag.current;
    if (!d || !remote) return;
    const dy = e.clientY - d.y, dx = e.clientX - d.x;
    if (!d.moved && Math.hypot(dx, dy) < 8) return;
    d.moved = true;
    if (e.pointerType === "mouse") return;    // mouse drags do nothing; use the wheel
    const r = screen.current!.getBoundingClientRect();
    const scale = remote.height / r.height;
    send({ type: "scroll", dx: -dx * scale, dy: -dy * scale, ...point(e) });
    d.x = e.clientX;
    d.y = e.clientY;
  }
  function onPointerUp(e: PointerEvent<HTMLImageElement>) {
    const d = drag.current;
    drag.current = null;
    if (!d || d.moved || !remote) return;
    send({ type: "click", ...point(e) });
    keyboard.current?.focus({ preventScroll: true });   // typing then goes to the page
  }
  useEffect(() => {
    const el = screen.current;
    if (!el || phase !== "live" || !remote) return;
    const onWheel = (e: WheelEvent) => {
      e.preventDefault();
      send({ type: "scroll", dx: e.deltaX, dy: e.deltaY, ...point(e) });
    };
    el.addEventListener("wheel", onWheel, { passive: false });
    return () => el.removeEventListener("wheel", onWheel);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [phase, remote, send]);

  function onKeyDown(e: KeyboardEvent<HTMLInputElement>) {
    if (e.ctrlKey || e.metaKey || e.altKey) return;
    const k = KEYS[e.key];
    if (k) {
      e.preventDefault();
      send({ type: "key", key: k });
    }
  }
  function onInput(e: FormEvent<HTMLInputElement>) {
    const v = e.currentTarget.value;
    if (v) send({ type: "type", text: v });
    e.currentTarget.value = "";
  }

  function capture() {
    setCapturing(true);
    setNotice(null);
    send({ type: "capture" });
  }

  if (info.data && !info.data.enabled) {
    return <p className="rounded border border-line bg-paper-sunk px-4 py-3 text-sm text-ink-soft">{t("browser.unavailable")}</p>;
  }
  const live = phase === "live" && remote;
  const fbHome = isTouch() ? "https://m.facebook.com/" : "https://www.facebook.com/";

  return (
    <section ref={box} aria-labelledby="browser-title" className="rounded-lg border border-line bg-paper-raised p-4 shadow-sm">
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div>
          <h2 id="browser-title" className="text-lg font-semibold">{t("browser.title")}</h2>
          <p className="mt-1 max-w-prose text-sm text-ink-soft">{t("browser.intro")}</p>
        </div>
        {live && <Button variant="ghost" onClick={close}>{t("browser.close")}</Button>}
      </div>

      {!live && (
        <div className="mt-4 space-y-3">
          <div className="flex flex-wrap gap-2">
            <Button className="px-5 py-3 text-base" disabled={disabled || phase === "connecting"} onClick={() => open(fbHome)}>
              {phase === "connecting" ? t("browser.connecting") : t("browser.openFacebook")}
            </Button>
            <Button variant="ghost" disabled={disabled || phase === "connecting"} onClick={() => open()}>
              {t("browser.open")}
            </Button>
          </div>
          <p className="text-xs text-ink-mute">{t("browser.accountNote")}</p>
          <p className="text-xs text-ink-mute">{t("browser.verifyNote")}</p>
          {info.data?.has_saved_session && (
            <p className="flex flex-wrap items-center gap-2 text-xs text-ink-soft">
              {info.data.saved_at && t("browser.savedSession", { date: fmtDate(info.data.saved_at) })}
              <button className="underline hover:text-signal-neg" disabled={forget.isPending}
                      onClick={() => { if (window.confirm(t("browser.forgetConfirm"))) forget.mutate(); }}>
                {t("browser.forget")}
              </button>
            </p>
          )}
        </div>
      )}

      {notice && (
        <p role="status" className={`mt-3 text-sm ${notice.tone === "ok" ? "text-signal-pos" : "text-signal-neg"}`}>{notice.text}</p>
      )}

      <div className={live ? "mt-3 space-y-2" : "hidden"}>
        {thread && (
          <p role="status" className="rounded bg-signal-info/10 px-3 py-1.5 text-xs text-signal-info">
            {t("browser.threadRunning", { n: thread.n })}
          </p>
        )}
        {link && (
          <div className="flex flex-wrap items-center gap-2 rounded border border-brand/30 bg-brand/5 px-3 py-1.5 text-xs">
            <span>🔗 {t("browser.linkedTo")}</span>
            <span className="min-w-0 flex-1 truncate font-medium" dir="ltr" title={link.url}>{link.url}</span>
            <button className="text-ink-mute underline hover:text-ink" onClick={() => send({ type: "link", item_id: null })}>
              {t("browser.unlink")}
            </button>
          </div>
        )}
        <form className="flex items-center gap-1" dir="ltr"
              onSubmit={(e) => { e.preventDefault(); send({ type: "navigate", url: address }); }}>
          <button type="button" className="rounded px-2 py-1.5 hover:bg-paper-sunk" aria-label={t("browser.back")} onClick={() => send({ type: "back" })}>←</button>
          <button type="button" className="rounded px-2 py-1.5 hover:bg-paper-sunk" aria-label={t("browser.forward")} onClick={() => send({ type: "forward" })}>→</button>
          <button type="button" className="rounded px-2 py-1.5 hover:bg-paper-sunk" aria-label={t("browser.reload")} onClick={() => send({ type: "reload" })}>↻</button>
          <label htmlFor="browser-address" className="sr-only">{t("browser.address")}</label>
          <input id="browser-address" className="field min-w-0 flex-1 py-1.5 text-sm" inputMode="url" autoCapitalize="off"
                 autoCorrect="off" spellCheck={false} value={address} onChange={(e) => setAddress(e.target.value)} />
          <Button type="submit" variant="ghost" className="py-1.5">{t("browser.go")}</Button>
        </form>

        <div className="relative mx-auto overflow-hidden rounded border border-line bg-paper-sunk"
             style={remote ? { maxWidth: remote.mobile ? remote.width : "100%", aspectRatio: `${remote.width} / ${remote.height}` } : undefined}>
          {/* eslint-disable-next-line @next/next/no-img-element */}
          <img ref={screen} src={frame ?? undefined} alt={url} draggable={false}
               className="block h-full w-full touch-none select-none"
               onPointerDown={onPointerDown} onPointerMove={onPointerMove} onPointerUp={onPointerUp}
               onContextMenu={(e) => e.preventDefault()} />
          <input ref={keyboard} aria-label={t("browser.typing")} className="absolute bottom-0 start-0 h-px w-px opacity-0"
                 autoCapitalize="off" autoCorrect="off" autoComplete="off" spellCheck={false}
                 onKeyDown={onKeyDown} onInput={onInput} />
        </div>

        <div className="sticky bottom-2 flex flex-wrap items-center gap-2">
          <Button className="flex-1 px-5 py-3 text-base shadow-md sm:flex-none" disabled={capturing || !!thread || disabled} onClick={capture}>
            {capturing ? t("browser.capturing") : t("browser.capture")}
          </Button>
          {thread ? (
            <Button variant="ghost" className="flex-1 py-3 sm:flex-none" disabled={thread.stopping}
                    onClick={() => { setThread({ ...thread, stopping: true }); send({ type: "stop_thread" }); }}>
              ■ {t("browser.threadStop", { n: thread.n })}
            </Button>
          ) : (
            <Button variant="ghost" className="flex-1 py-3 sm:flex-none" disabled={capturing || disabled}
                    title={t("browser.threadHint")}
                    onClick={() => { setNotice(null); setThread({ n: 0, stopping: false }); send({ type: "capture_thread" }); }}>
              ⇣ {t("browser.thread")}
            </Button>
          )}
          <Button variant="ghost" onClick={() => keyboard.current?.focus()}>{t("browser.keyboard")}</Button>
          <Button variant="ghost" onClick={() => send({ type: "key", key: "Backspace" })}>{t("browser.erase")}</Button>
          <Button variant="ghost" onClick={() => send({ type: "key", key: "Enter" })}>{t("browser.enter")}</Button>
        </div>
      </div>
    </section>
  );
}
