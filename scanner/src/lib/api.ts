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
