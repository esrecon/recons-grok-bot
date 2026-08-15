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
