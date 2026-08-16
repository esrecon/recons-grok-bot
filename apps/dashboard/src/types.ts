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
  status_detail?: string;
  is_lead: boolean;
  created_at: string;
  // Adopted from an existing Hermes install: owns its home and, until
  // promoted, runs its gateway outside this platform.
  imported?: boolean;
  // Explicit model override (Customize tab / captured at promotion). Both or
  // neither; null means "the tier's default".
  model_provider?: string | null;
  model_name?: string | null;
  // Cache-buster for the generated headshot; null/absent = no image yet.
  avatar_version?: number | null;
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
  source: "session" | "a2a" | "cron" | "webhook";
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
  a2a_only?: boolean;
  q?: string;
}

// Per-agent Telegram gateway state. The bot token is write-only: the API
// reports whether one is stored, never the value.
export interface TelegramStatus {
  enabled: boolean;
  allowed_users: string;
  token_set: boolean;
  imported: boolean;
}

export type ProviderState = "configured" | "not_configured" | "error";

export interface Provider {
  id: "nous" | "openai" | "anthropic" | string;
  label: string;
  tier: string;
  method: "api_key" | "oauth" | "service";
  state: ProviderState;
  detail: string;
  docs?: string;
}

// A Hermes agent already on the machine that can be adopted into the roster.
export interface DiscoveredAgent {
  id: string;
  name: string;
  home: string;
  role: string;
  model: string;
  already_imported: boolean;
}

export interface SetupStatus {
  providers: Provider[];
  has_provider: boolean;
  has_agents: boolean;
  complete: boolean;
}

export interface LoginSession {
  id: string;
  provider: string;
  status: "starting" | "awaiting_user" | "success" | "failed";
  url: string | null;
  code: string | null;
  message: string;
  command: string;
  output: string[];
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

// One selectable model in the Customize tab's dropdown (/api/models).
export interface ModelOption {
  provider: string;
  model: string;
  label: string;
  tier: ModelTier | null;
  source: "tier" | "custom";
  available: boolean;
}

// Image-generation settings (write-only key, as everywhere).
export interface ImageSettings {
  key_set: boolean;
  base_url: string;
  model: string;
}

export type ImproveField = "name" | "role" | "personality" | "soul";

export interface ChatMessage {
  id: string;
  role: "user" | "assistant" | "system";
  text: string;
  pending?: boolean;
  toolCalls?: { name: string; ok?: boolean }[];
  approval?: { id: string; title: string; body: string; kind?: string };
}
