// Thin typed client over the backend. Auth is cookie-based (httpOnly), proxied same-origin
// through Next.js rewrites. Unsafe methods echo the CSRF cookie in X-CSRF-Token.

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
  refreshing ??= fetch("/api/v1/auth/refresh", { method: "POST", credentials: "same-origin" })
    .then((r) => r.ok)
    .catch(() => false)
    .finally(() => setTimeout(() => (refreshing = null), 0));
  return refreshing;
}

async function request<T>(method: string, path: string, body?: unknown, retry = true): Promise<T> {
  const headers: Record<string, string> = { accept: "application/json" };
  if (body !== undefined) headers["content-type"] = "application/json";
  if (method !== "GET") {
    const csrf = readCookie("ri_csrf");
    if (csrf) headers["x-csrf-token"] = decodeURIComponent(csrf);
  }
  const res = await fetch(`/api/v1${path}`, {
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
  del: (p: string) => request<void>("DELETE", p),
};

// ---- types mirroring backend schemas --------------------------------------------------------
export type RoleKey = "owner" | "admin" | "analyst" | "viewer";
export interface TenantSummary { id: string; name: string; slug: string; role: RoleKey }
export interface Me {
  user_id: string; email: string; full_name: string; locale: string;
  email_verified: boolean; mfa_enabled: boolean;
  tenant: TenantSummary; permissions: string[]; tenants: TenantSummary[];
}
export interface Alias { id: string; alias: string; script: string; kind: string; created_at: string }
export interface Brand {
  id: string; name: string; description: string; website: string | null; country: string;
  primary_language: string; status: "active" | "paused" | "archived";
  created_at: string; updated_at: string; aliases: Alias[];
}
export interface Page<T> { items: T[]; total: number; limit: number; offset: number }
export interface Member {
  membership_id: string; user_id: string; email: string; full_name: string;
  role: RoleKey; status: string; joined_at: string;
}
export interface Invitation {
  id: string; email: string; role: RoleKey; expires_at: string; created_at: string; token?: string | null;
}
export interface Capability { available: boolean; reason: string | null }
export interface Dashboard {
  brands_total: number; brands_active: number; aliases_total: number; members_total: number;
  products_total: number; competitors_total: number; projects_total: number; projects_active: number;
  last_run: { project_name: string; status: RunStatus; finished_at: string | null; reason: string | null } | null;
  recent_activity: { action: string; actor_email: string | null; created_at: string }[];
  mentions: Capability; sentiment: Capability; sources: Capability;
}
export interface Organization { id: string; name: string; slug: string; status: string; default_locale: string; created_at: string }
export interface AuditEntry {
  id: string; action: string; actor_email: string | null; target_type: string | null;
  target_id: string | null; metadata: Record<string, unknown>; ip: string | null; created_at: string;
}
export interface MfaChallenge { mfa_required: true; mfa_token: string; expires_in: number }
export interface SessionTokens { mfa_required?: undefined; expires_in: number; tenant_id: string }
export type LoginResult = MfaChallenge | SessionTokens;
export interface MfaStatus { enabled: boolean; recovery_codes_remaining: number }
export interface MfaSetup { secret: string; otpauth_uri: string }
export interface RecoveryCodes { recovery_codes: string[] }
export interface Product { id: string; name: string; category: string; description: string; aliases: string[]; created_at: string }
export interface Competitor { id: string; name: string; website: string | null; aliases: string[]; notes: string; created_at: string }
export interface Keyword { id: string; expression: string; kind: "include" | "exclude"; language: string | null; created_at: string }
export type RunStatus = "queued" | "running" | "completed" | "skipped" | "failed";
export interface MonitoringRun {
  id: string; trigger: "schedule" | "manual"; status: RunStatus; queued_at: string;
  started_at: string | null; finished_at: string | null; summary: Record<string, unknown>; error: string | null;
}
export interface MonitoringProject {
  id: string; name: string; brand_id: string; brand_name: string; status: "active" | "paused";
  frequency_minutes: number; include_competitors: boolean; languages: string[];
  next_run_at: string | null; last_run_at: string | null; last_run: MonitoringRun | null; created_at: string;
}
export interface QueryGroup { entity_type: "brand" | "product" | "competitor"; entity_name: string; terms: string[]; exclude: string[]; search_string: string }
export interface UserSession { id: string; current: boolean; tenant_id: string; user_agent: string | null; ip: string | null; started_at: string; last_active_at: string }
export interface PlanUsage {
  plan_key: string; plan_name: string; status: string; trial_ends_at: string | null;
  limits: Record<string, number | null>; usage: Record<string, number>;
}
