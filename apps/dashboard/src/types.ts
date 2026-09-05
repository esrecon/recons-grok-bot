// Shared types mirroring the orchestrator's API models (apps/orchestrator).

export type ModelTier = "lead" | "workhorse" | "bulk";
export type AgentStatus = "running" | "paused" | "error";

export interface Agent {
  id: string;
  name: string;
  role: string;
  personality?: string;
  tier: ModelTier;
  avatar_color: string;
  status: AgentStatus;
  is_lead: boolean;
  created_at: string;
}

export interface NewAgentInput {
  name: string;
  role: string;
  personality?: string;
  tier: ModelTier;
  avatar_color: string;
}

// Streamed chat events (SSE) from the orchestrator's chat proxy.
export type ChatEvent =
  | { type: "token"; text: string }
  | { type: "tool_call"; name: string; args?: unknown }
  | { type: "tool_result"; name: string; ok: boolean }
  | {
      type: "approval";
      id: string;
      title: string;
      body: string;
      kind?: string;
    }
  | { type: "done" }
  | { type: "error"; message: string };

// One row in the merged audit ledger (orchestrator /api/audit).
export interface AuditEvent {
  ts: number;
  ts_iso: string;
  seq: number;
  source: "session" | "a2a" | "cron" | "webhook" | "operator";
  agent_id: string;
  kind: string;
  session_id?: string | null;
  session_key?: string | null;
  role?: string | null;
  peer_from?: string | null;
  peer_to?: string | null;
  text: string;
  extra?: Record<string, unknown>;
}

export interface AuditFilters {
  agent?: string;
  source?: string;
  kind?: string;
  a2a_only?: boolean;
  q?: string;
}

export interface Skill {
  slug: string;
  name: string;
  description: string;
  source: "shared" | "pending";
  agent?: string | null;
  version?: string | null;
}

export interface Routine {
  id: string;
  agent: string;
  schedule: string;
  instruction: string;
  enabled: boolean;
  deliver?: string | null;
}

export interface ChatMessage {
  id: string;
  role: "user" | "assistant" | "system";
  text: string;
  pending?: boolean;
  toolCalls?: { name: string; ok?: boolean }[];
  approval?: { id: string; title: string; body: string; kind?: string };
}

// --- operator session -----------------------------------------------------------
export interface SessionInfo {
  authenticated: boolean;
  operator: string | null;
  via: "password" | "proxy" | null;
  csrf_token: string | null;
  mode: "password" | "proxy" | string;
  configured: boolean;
  reason: string | null;
}

// --- settings: providers, credentials, security, services ---------------------
export interface KeyStatus {
  key: string;
  label: string;
  secret: boolean;
  writable: boolean;
  required: boolean;
  hint: string;
  configured: boolean;
  updated_at: string | null;
  updated_by: string | null;
}

export type ProviderHealth = "ok" | "unreachable" | "configured" | "not_configured";

export interface ProviderStatus {
  id: string;
  name: string;
  description: string;
  health: ProviderHealth;
  keys: KeyStatus[];
}

export interface WebhookFeedStatus {
  last_event_at: string | null;
  accepted_count: number;
  rejected_count: number;
}

export interface ProvidersResponse {
  providers: ProviderStatus[];
  integrations: Record<string, WebhookFeedStatus>;
  restart_required: boolean;
}

export interface CredentialChange {
  key: string;
  action: "created" | "replaced" | "removed";
  configured: boolean;
  updated_at?: string | null;
  restart_required: boolean;
}

export interface SecurityPosture {
  mode: string;
  configured: boolean;
  operator: string | null;
  via: string | null;
  cookie_secure: boolean;
  hsts: boolean;
  session_ttl_seconds: number;
  csrf_protection: boolean;
  proxy_identity_header?: string | null;
  allowed_origins: string[];
  rate_limits: Record<string, number>;
}

export interface ServiceStatus {
  agent: string;
  name: string;
  unit: string;
  status: string;
  expected: "running" | "paused";
  healthy: boolean;
}

// --- skills detail ----------------------------------------------------------------
export interface SkillFile {
  path: string;
  size: number;
  kind: "text" | "script" | "binary";
}

export interface SkillDetail {
  skill: Skill;
  frontmatter: Record<string, unknown>;
  body: string;
  truncated: boolean;
  files: SkillFile[];
  warnings: string[];
}

export interface SkillFileContent {
  path: string;
  size: number;
  text: string;
  truncated: boolean;
}

// --- sessions (conversation history) ----------------------------------------------
export interface SessionSummary {
  agent_id: string;
  session_id: string;
  session_key: string | null;
  started_ts: number;
  last_ts: number;
  started_at: string;
  last_at: string;
  message_count: number;
  tool_calls: number;
  preview: string;
}

export interface SessionDetail {
  session: SessionSummary;
  events: AuditEvent[];
}
