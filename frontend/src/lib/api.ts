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
  email_verified: boolean; mfa_enabled: boolean; is_platform_admin: boolean;
  tenant: TenantSummary; permissions: string[]; tenants: TenantSummary[];
}
export interface Alias { id: string; alias: string; script: string; kind: string; created_at: string }
export interface Brand {
  id: string; name: string; description: string; website: string | null; country: string;
  primary_language: string; status: "active" | "paused" | "archived";
  response_tone: "neutral" | "formal" | "friendly"; response_guidelines: string;
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
  mention_stats: { total: number; last_7_days: number; verified_on_page: number; reposts: number;
    top_domains: CountItem[]; by_entity: CountItem[]; by_method: CountItem[] };
  source_stats: { registered: number; monitored: number; by_coverage: CountItem[] };
  sentiment_stats: { analysed: number; about_brand: number; distribution: CountItem[]; validated: boolean; macro_f1: number | null };
  recent_activity: { action: string; actor_email: string | null; created_at: string }[];
  mentions: Capability; sentiment: Capability; sources: Capability;
}
export interface Organization { id: string; name: string; slug: string; status: string; default_locale: string; nlp_corpus_opt_in: boolean; created_at: string }
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
export type Verification = "verified_on_page" | "snippet_only" | "not_found_on_page" | "page_unavailable" | "blocked_by_robots" | "feed_item" | "pending_render" | "blocked_by_challenge";
export type Coverage = "AVAILABLE" | "PARTIALLY_AVAILABLE" | "PENDING_AUTHORIZATION" | "RESTRICTED" | "UNAVAILABLE" | "CONNECTOR_ERROR";
export interface Mention {
  id: string; brand_id: string; brand_name: string; project_id: string | null;
  entity_type: "brand" | "product" | "competitor"; entity_name: string; url: string; domain: string;
  title: string; original_text: string; matched_terms: string[]; publisher: string | null;
  published_at: string | null; published_at_source: string | null; collected_at: string; last_seen_at: string;
  seen_count: number; collection_method: "search_api" | "news_api" | "rss" | "page_fetch";
  verification_status: Verification; is_repost: boolean; language: string | null; analysed: boolean;
  analysis: Analysis;
}
export interface MentionDetail extends Mention {
  snippet: string; canonical_url: string; connector: string; connector_version: string; content_hash: string;
  duplicate_of: string | null; project_name: string | null; provider_fields: Record<string, unknown>;
  annotators: Record<string, unknown>[]; models: Record<string, unknown>;
  source: { id: string; kind: string; domain: string; name: string; coverage_status: Coverage } | null;
}
export interface Source {
  id: string; kind: "rss" | "website"; url: string; domain: string; name: string; category: string;
  language: string | null; connector: string; enabled: boolean; monitored: boolean;
  discovered_via: "manual" | "search" | "feed_discovery"; coverage_status: Coverage; restrictions: string;
  mentions_count: number; last_collected_at: string | null; last_checked_at: string | null;
  last_error: string | null; created_at: string;
  render_mode: "auto" | "static" | "browser"; challenge_count: number;
  last_challenge_kind: string | null; last_challenge_at: string | null;
}
export interface SourceCheck { ok: boolean; detail: string; source: Source; discovered_feeds: Source[] }
export interface ConnectorInfo {
  key: string; name: string; version: string; category: string; access_method: string; capabilities: string[];
  limitations: string[]; configured: boolean; review_status: string; allowed: boolean; note: string;
  legal_basis: string; data_categories: string[]; retention_days: number;
}
export interface CountItem { key: string; count: number }
export type Sentiment = "positive" | "negative" | "neutral" | "mixed" | "UNCERTAIN";
export interface Analysis {
  status: "pending" | "done" | "failed" | "not_configured" | "blocked_by_policy";
  script?: string | null; heuristic_language?: string | null; code_switching?: boolean | null;
  normalized_text?: string | null; language?: string | null; dialect?: string | null;
  sentiment?: Sentiment | null; sarcasm?: "yes" | "no" | "UNCERTAIN" | null;
  about_target?: "yes" | "no" | "UNCERTAIN" | null; intents?: string[]; emotions?: string[];
  topics?: string[]; translation_fr?: string | null; agreement?: number | null;
  arbitration_used?: boolean; corrected?: boolean; error?: string | null;
}
export interface TaskStatus { validated: boolean; macro_f1: number | null; coverage: number | null; n: number }
export interface NlpStatus {
  run_id: string | null; finished_at: string | null; ai_configured: boolean;
  tasks: Record<"sentiment" | "sarcasm" | "about_target" | "language" | "intents", TaskStatus>;
  thresholds: { macro_f1: number; coverage: number; gold_items: number };
}
export interface CorpusItem {
  id: string; text: string; normalized_text: string | null; source_url: string | null; script: string | null;
  auto_labels: Record<string, unknown>; auto_status: string; labels: Record<string, unknown> | null;
  status: string; split: string; verified_at: string | null;
}
export interface EvalRun {
  id: string; status: string; split: string; n_items: number; prompt_version: string | null;
  analysis_version: string | null; models: Record<string, unknown>; metrics: Record<string, unknown>;
  error: string | null; created_at: string; finished_at: string | null;
}
export interface Expression { canonical: string; arabic: string | null; variants: string[]; meaning_fr: string; polarity: string; status: string }
export interface MetricBlock {
  n: number; macro_f1: number; coverage?: number; accuracy?: number;
  confusion?: Record<string, Record<string, number>>;
}
// ---- Milestone 5: reputation intelligence -----------------------------------------------------
export type Severity = "INFO" | "LOW" | "MEDIUM" | "HIGH" | "CRITICAL";
export interface DayPoint {
  day: string; total: number; positive: number; negative: number; neutral: number; mixed: number;
  uncertain: number; unanalysed: number;
}
export interface Totals {
  total: number; positive: number; negative: number; neutral: number; mixed: number; uncertain: number;
  unanalysed: number; domains: number; undated: number;
}
export interface Reputation { value: number | null; net_sentiment: number | null; sample: number; min_sample: number }
export interface EntityStat {
  entity_type: "brand" | "product" | "competitor"; entity_name: string; total: number; positive: number;
  negative: number; neutral: number; mixed: number; uncertain: number; unanalysed: number;
  share_of_voice: number | null; reputation: Reputation;
}
export interface TrendTopic { brand_id: string; brand: string; topic: string; day: string; count: number; expected: number; z: number }
export interface Crisis {
  id: string; brand_id: string; brand: string; status: "active" | "resolved"; severity: Severity;
  started_at: string; last_detected_at: string; resolved_at: string | null; peak_negative: number;
  metrics: Record<string, number | string>;
}
export interface Overview {
  brand_id: string | null; days: number; timezone: string; period_start: string; period_end: string;
  series: DayPoint[]; totals: Totals; previous_totals: Totals; reputation: Reputation;
  previous_reputation: Reputation; entities: EntityStat[]; topics: TrendTopic[]; active_crises: Crisis[];
}
export interface Alert {
  id: string; rule_id: string | null; kind: string; severity: Severity; title: string; body: string;
  brand_id: string | null; data: Record<string, unknown>; status: "open" | "acknowledged" | "resolved";
  acknowledged_at: string | null; resolved_at: string | null; created_at: string;
}
export interface AlertSummary { open: number; by_severity: Partial<Record<Severity, number>> }
export type RuleKind = "negative_mention" | "negative_spike" | "crisis" | "emerging_topic" | "competitor_spike" | "connector_failure";
export type Channel = "in_app" | "email" | "webhook";
export interface AlertRule {
  id: string; name: string; kind: RuleKind; brand_id: string | null; params: Record<string, number | boolean>;
  min_severity: Severity; channels: Channel[]; notify_roles: RoleKey[]; webhook_id: string | null;
  enabled: boolean; last_evaluated_at: string | null; created_at: string;
}
export interface Webhook {
  id: string; name: string; url: string; enabled: boolean; last_status: number | null;
  last_error: string | null; last_delivery_at: string | null; created_at: string; secret?: string;
}
export type ReportKind = "daily" | "weekly" | "monthly" | "crisis" | "competitor" | "custom";
export type ReportFormat = "pdf" | "xlsx" | "csv" | "html";
export interface Report {
  id: string; kind: ReportKind; format: ReportFormat; locale: "fr" | "ar" | "en"; brand_id: string | null;
  period_start: string; period_end: string; status: "queued" | "running" | "ready" | "failed";
  filename: string | null; size_bytes: number | null; error: string | null; created_at: string; finished_at: string | null;
}
// ---- Milestone 6: browser fleet ---------------------------------------------------------------
export type MissionStatus = "CREATED" | "VALIDATING" | "QUEUED" | "ASSIGNED" | "RUNNING" | "PAUSED"
  | "WAITING_FOR_HUMAN" | "COMPLETED" | "FAILED" | "CANCELLED" | "EXPIRED";
export interface BrowserMission {
  id: string; kind: "render_page" | "check_source"; url: string; domain: string; status: MissionStatus;
  priority: number; mention_id: string | null; source_id: string | null; attempts: number; max_attempts: number;
  error: string | null; result: Record<string, unknown>; created_at: string; started_at: string | null;
  finished_at: string | null; deadline_at: string;
}
export interface MissionEvent { kind: string; from_status: string | null; to_status: string | null; detail: Record<string, unknown>; created_at: string }
export interface BrowserSession { id: string; isolation: Record<string, unknown>; opened_at: string; closed_at: string | null; cleanup_verified: boolean; requests_blocked: number }
export interface Challenge {
  id: string; mission_id: string | null; source_id: string | null; mention_id: string | null; domain: string;
  url: string; via: "http" | "browser"; kind: string; confidence: number; signals: string[];
  state: "DETECTED" | "PAUSED" | "FALLBACK" | "SOURCE_RESTRICTED" | "CANCELLED" | "EXPIRED";
  history: { state: string; at: string }[]; response: { cooldown_until?: string; fallback?: string }; detected_at: string;
}
export interface MissionDetail extends BrowserMission {
  authorization_record: Record<string, unknown>; events: MissionEvent[]; sessions: BrowserSession[]; challenges: Challenge[];
}
export interface MissionPage extends Page<BrowserMission> { by_status: Partial<Record<MissionStatus, number>> }
export interface ChallengePage extends Page<Challenge> { by_kind: Record<string, number> }
export interface Fleet {
  workers: { id: string; name: string; hostname: string; version: string; status: string; max_contexts: number;
    active_contexts: number; metrics: Record<string, number | null>; started_at: string; last_heartbeat_at: string }[];
  cooling_domains: { domain: string; cooldown_until: string; consecutive_challenges: number; last_challenge_kind: string }[];
  capacity: number; busy: number;
}
// ---- Milestone 7: AI agents ------------------------------------------------------------------
export type TaskKind = "brand_health" | "crisis_response" | "competitor_review" | "source_discovery"
  | "respond_to_mention" | "report" | "ask";
export type AiTaskStatus = "queued" | "running" | "waiting_approval" | "completed" | "failed" | "cancelled";
export type ActionStatus = "proposed" | "rejected_by_policy" | "pending_approval" | "approved" | "rejected"
  | "executed" | "failed" | "expired";
export interface AgentFinding {
  agent: string; code: string; kind: "fact" | "inference" | "limitation"; level: "info" | "warning" | "critical";
  values: Record<string, unknown>;
}
export interface AiTask {
  id: string; kind: TaskKind; resolved_kind: TaskKind | null; goal: string; brand_id: string | null;
  status: AiTaskStatus; error: string | null; plan: { agent: string; version: string }[];
  result: { findings?: AgentFinding[]; steps?: Record<string, string>; actions?: Partial<Record<ActionStatus, number>> };
  locale: string; created_at: string; finished_at: string | null;
}
export interface AgentRunOut {
  id: string; position: number; agent: string; agent_version: string;
  status: "running" | "completed" | "failed" | "timeout" | "skipped"; output: Record<string, unknown>;
  tools_used: string[]; llm_calls: number; llm_model: string | null; prompt_version: string | null;
  error: string | null; duration_ms: number | null;
}
export interface AiActionOut {
  id: string; task_id: string; kind: string; params: Record<string, unknown>; rationale: Record<string, unknown>;
  risk: "low" | "medium" | "high"; requires_approval: boolean; status: ActionStatus;
  compliance: Record<string, unknown>; result: Record<string, unknown>; error: string | null;
  created_at: string; executed_at: string | null;
  approval: { id: string; status: string; expires_at: string; decided_at: string | null; comment: string } | null;
}
export interface AiTaskDetail extends AiTask { runs: AgentRunOut[]; actions: AiActionOut[] }
export interface AgentCatalog {
  agents: { name: string; version: string; responsibility: string; tools: string[]; timeout_seconds: number }[];
  playbooks: Record<string, string[]>;
  actions: { kind: string; permission: string; risk: string; requires_approval: boolean }[];
  permission_ceiling: string[];
}
