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

export interface ChatMessage {
  id: string;
  role: "user" | "assistant" | "system";
  text: string;
  pending?: boolean;
  toolCalls?: { name: string; ok?: boolean }[];
  approval?: { id: string; title: string; body: string; kind?: string };
}
