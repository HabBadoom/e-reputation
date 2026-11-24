// Typed client over the Scanner API (/api/v1/scanner/*). Auth uses httpOnly `sc_*` cookies,
// proxied same-origin through Next.js rewrites. Unsafe methods echo `sc_csrf` in X-CSRF-Token.

export class ApiError extends Error {
  constructor(
    public status: number,
    public code: string,
    message: string,
    public details?: unknown,
    public requestId?: string,
  ) {
    super(message);
  }
}

const BASE = "/api/v1/scanner";

function readCookie(name: string): string | undefined {
  if (typeof document === "undefined") return undefined;
  return document.cookie
    .split("; ")
    .find((c) => c.startsWith(name + "="))
    ?.split("=")[1];
}

let refreshing: Promise<boolean> | null = null;

async function tryRefresh(): Promise<boolean> {
  // Collapse concurrent 401s into a single refresh call.
  refreshing ??= fetch(`${BASE}/auth/refresh`, { method: "POST", credentials: "same-origin" })
    .then((r) => r.ok)
    .catch(() => false)
    .finally(() => setTimeout(() => (refreshing = null), 0));
  return refreshing;
}

async function request<T>(method: string, path: string, body?: unknown, retry = true): Promise<T> {
  const headers: Record<string, string> = { accept: "application/json" };
  if (body !== undefined) headers["content-type"] = "application/json";
  if (method !== "GET") {
    const csrf = readCookie("sc_csrf");
    if (csrf) headers["x-csrf-token"] = decodeURIComponent(csrf);
  }
  const res = await fetch(`${BASE}${path}`, {
    method,
    headers,
    credentials: "same-origin",
    body: body === undefined ? undefined : JSON.stringify(body),
  });

  if (res.status === 401 && retry && !path.startsWith("/auth/")) {
    if (await tryRefresh()) return request<T>(method, path, body, false);
  }
  if (res.status === 204) return undefined as T;

  const data = await res.json().catch(() => null);
  if (!res.ok) {
    const e = data?.error ?? {};
    throw new ApiError(res.status, e.code ?? "http_error", e.message ?? res.statusText, e.details, e.request_id);
  }
  return data as T;
}

export const api = {
  get: <T>(p: string) => request<T>("GET", p),
  post: <T>(p: string, b?: unknown) => request<T>("POST", p, b ?? {}),
  patch: <T>(p: string, b: unknown) => request<T>("PATCH", p, b),
  put: <T>(p: string, b: unknown) => request<T>("PUT", p, b),
  del: <T>(p: string) => request<T>("DELETE", p),
};

// ---- types mirroring backend schemas (app/modules/scanner/schemas.py) ------------------------
export interface ScannerMe {
  account_id: string;
  email: string;
  full_name: string;
  locale: "fr" | "ar" | "en";
  created_at: string;
}

/** Collection metrics only. Scanner never shows analytical figures. */
export interface Counters {
  items: number;
  screenshots: number;
  urls: number;
  imports: number;
  needs_review: number;
  ready: number;
  failed: number;
}

export type SessionStatus =
  | "draft" | "collecting" | "ready" | "validating" | "queued" | "transmitting"
  | "transmitted" | "acknowledged" | "failed" | "retrying" | "cancelled";

export interface CollectionSession {
  id: string;
  status: SessionStatus;
  title: string;
  created_at: string;
  updated_at: string;
  transmitted_at: string | null;
  anypulse_collection_id: string | null;
  is_open: boolean;
  counters: Counters;
}

/** Whether this account can send its collections to AnyPulse (revoked only by a platform
 * administrator). Scanner never shows AnyPulse data. */
export interface AnyPulseAccess {
  state: "active" | "revoked";
  revoked_at: string | null;
  revoke_reason: string | null;
}

export interface Page<T> { items: T[]; total: number; limit: number; offset: number }

// ---- screenshots (phase 3) ---------------------------------------------------------------------
export interface UploadedFile {
  id: string;
  original_filename: string;
  mime_type: string;
  width: number;
  height: number;
  size_bytes: number;
  url: string;
}

export interface CollectionItem {
  id: string;
  source_type: "screenshot" | "url" | "import";
  status: "pending" | "processing" | "ready" | "needs_review" | "failed";
  platform: string | null;
  content_type: string | null;
  position: number;
  source_url: string | null;
  possible_duplicate_of: string | null;
  created_at: string;
  file: UploadedFile | null;
  error_code: string | null;
  extraction: ExtractionSummary | null;
  link_item_id: string | null;
  captures: number | null;
}

// Phase 5: a pasted link, recorded in the collection and opened in the built-in browser.
export interface LinkResult {
  item_id: string;
  url: string;
  platform: string | null;
  created: boolean;
}

/** Ask the built-in browser to open a pasted link and attach the next captures to it. */
export const OPEN_LINK_EVENT = "scanner:open-link";
export function openLinkInBrowser(itemId: string) {
  window.dispatchEvent(new CustomEvent(OPEN_LINK_EVENT, { detail: { itemId } }));
}

// Phase 4: what was read from a screenshot (collection data, no analysis).
export interface ExtractionSummary {
  status: "queued" | "running" | "done" | "failed";
  confidence: number | null;
  issues: string[];
  context: { view?: string; page_or_group?: string; dialog_title?: string };
  entries: number;
  model: string | null;
  validated_at: string | null;
}

export interface ExtractedEntry {
  id: string;
  item_id: string;
  position: number;
  kind: "post" | "comment" | "reply";
  parent_id: string | null;
  author_name: string;
  author_is_page: boolean;
  text: string;
  image_text: string;
  script: "arabic" | "latin" | "mixed" | "none";
  text_truncated: boolean;
  partially_visible: boolean;
  relative_time: string;
  published_at: string | null;
  published_precision: "minute" | "hour" | "day" | "week" | "month" | "year" | null;
  reactions_count: number | null;
  comments_count: number | null;
  shares_count: number | null;
  duplicate_of: string | null;
  edited_by_agent: boolean;
}

export interface FileResult {
  filename: string;
  status: "added" | "duplicate" | "rejected";
  code: string | null;
  item_id: string | null;
}

export interface UploadResult {
  results: FileResult[];
  items: CollectionItem[];
  session: CollectionSession;
}

export const ACCEPTED_TYPES = ["image/png", "image/jpeg", "image/webp"];

/** Upload screenshots with progress (fetch has no upload progress). Same auth, CSRF and
 *  refresh-on-401 behaviour as `api`. */
export function uploadScreenshots(
  sessionId: string,
  files: File[],
  onProgress: (fraction: number) => void,
  retry = true,
): Promise<UploadResult> {
  return new Promise((resolve, reject) => {
    const form = new FormData();
    for (const f of files) form.append("files", f, f.name);
    const xhr = new XMLHttpRequest();
    xhr.open("POST", `${BASE}/sessions/${sessionId}/screenshots`);
    xhr.withCredentials = true;
    xhr.setRequestHeader("accept", "application/json");
    const csrf = readCookie("sc_csrf");
    if (csrf) xhr.setRequestHeader("x-csrf-token", decodeURIComponent(csrf));
    xhr.upload.onprogress = (e) => { if (e.lengthComputable) onProgress(e.loaded / e.total); };
    xhr.onerror = () => reject(new ApiError(0, "network_error", "Network error"));
    xhr.onload = async () => {
      if (xhr.status === 401 && retry && (await tryRefresh())) {
        uploadScreenshots(sessionId, files, onProgress, false).then(resolve, reject);
        return;
      }
      let data: { error?: { code?: string; message?: string; details?: unknown; request_id?: string } } | null = null;
      try { data = JSON.parse(xhr.responseText); } catch { data = null; }
      if (xhr.status >= 200 && xhr.status < 300) resolve(data as unknown as UploadResult);
      else {
        const e = data?.error ?? {};
        reject(new ApiError(xhr.status, e.code ?? "http_error", e.message ?? xhr.statusText, e.details, e.request_id));
      }
    };
    xhr.send(form);
  });
}

// Built-in browser (scanner-browser service, WebSocket). `ws_url` empty = same origin.
export interface BrowserInfo {
  enabled: boolean;
  ws_url: string;
  has_saved_session: boolean;
  saved_at: string | null;
}

export function browserSocketUrl(info: BrowserInfo): string {
  if (info.ws_url) return info.ws_url;
  const proto = window.location.protocol === "https:" ? "wss" : "ws";
  return `${proto}://${window.location.host}/api/v1/scanner/browser/ws`;
}
