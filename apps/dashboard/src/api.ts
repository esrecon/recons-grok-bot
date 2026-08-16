import type {
  Agent,
  AuditEvent,
  AuditFilters,
  ChatEvent,
  DiscoveredAgent,
  LoginSession,
  NewAgentInput,
  Provider,
  Routine,
  SetupStatus,
  Skill,
} from "./types";

// Single-origin client for the orchestrator (proxied to the mock server in dev).
const BASE = "/api";

async function json<T>(res: Response): Promise<T> {
  if (!res.ok) {
    const detail = await res.text().catch(() => res.statusText);
    throw new Error(detail || `HTTP ${res.status}`);
  }
  return res.json() as Promise<T>;
}

export const api = {
  listAgents(): Promise<Agent[]> {
    return fetch(`${BASE}/agents`).then((r) => json<Agent[]>(r));
  },

  createAgent(input: NewAgentInput): Promise<Agent> {
    return fetch(`${BASE}/agents`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(input),
    }).then((r) => json<Agent>(r));
  },

  setStatus(id: string, action: "pause" | "resume"): Promise<Agent> {
    return fetch(`${BASE}/agents/${id}/${action}`, { method: "POST" }).then((r) =>
      json<Agent>(r),
    );
  },

  makeLead(id: string): Promise<Agent> {
    return fetch(`${BASE}/agents/${id}/lead`, { method: "POST" }).then((r) =>
      json<Agent>(r),
    );
  },

  deleteAgent(id: string): Promise<void> {
    return fetch(`${BASE}/agents/${id}`, { method: "DELETE" }).then((r) => {
      if (!r.ok && r.status !== 204) throw new Error(`HTTP ${r.status}`);
    });
  },

  audit(filters: AuditFilters = {}): Promise<{ events: AuditEvent[]; count: number }> {
    const p = new URLSearchParams();
    if (filters.agent) p.set("agent", filters.agent);
    if (filters.source) p.set("source", filters.source);
    if (filters.a2a_only) p.set("a2a_only", "true");
    if (filters.q) p.set("q", filters.q);
    const qs = p.toString();
    return fetch(`${BASE}/audit${qs ? `?${qs}` : ""}`).then((r) =>
      json<{ events: AuditEvent[]; count: number }>(r),
    );
  },

  auditExportUrl(): string {
    return `${BASE}/audit/export.jsonl`;
  },

  importCandidates(): Promise<{ candidates: DiscoveredAgent[] }> {
    return fetch(`${BASE}/import/candidates`).then((r) =>
      json<{ candidates: DiscoveredAgent[] }>(r),
    );
  },

  importAgent(input: { home: string; name?: string; role?: string }): Promise<Agent> {
    return fetch(`${BASE}/import`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(input),
    }).then((r) => json<Agent>(r));
  },

  setup(): Promise<SetupStatus> {
    return fetch(`${BASE}/setup`).then((r) => json<SetupStatus>(r));
  },

  providers(): Promise<{ providers: Provider[] }> {
    return fetch(`${BASE}/providers`).then((r) => json<{ providers: Provider[] }>(r));
  },

  saveProviderKey(id: string, key: string): Promise<Provider> {
    return fetch(`${BASE}/providers/${id}/key`, {
      method: "PUT",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ key }),
    }).then((r) => json<Provider>(r));
  },

  clearProviderKey(id: string): Promise<Provider> {
    return fetch(`${BASE}/providers/${id}/key`, { method: "DELETE" }).then((r) =>
      json<Provider>(r),
    );
  },

  startProviderLogin(id: string): Promise<LoginSession> {
    return fetch(`${BASE}/providers/${id}/login`, { method: "POST" }).then((r) =>
      json<LoginSession>(r),
    );
  },

  pollProviderLogin(loginId: string): Promise<LoginSession> {
    return fetch(`${BASE}/providers/login/${loginId}`).then((r) =>
      json<LoginSession>(r),
    );
  },

  skills(): Promise<{ shared: Skill[]; pending: Skill[] }> {
    return fetch(`${BASE}/skills`).then((r) =>
      json<{ shared: Skill[]; pending: Skill[] }>(r),
    );
  },

  approveSkill(agent: string, slug: string): Promise<Skill> {
    return fetch(`${BASE}/skills/${agent}/${slug}/approve`, { method: "POST" }).then(
      (r) => json<Skill>(r),
    );
  },

  rejectSkill(agent: string, slug: string): Promise<void> {
    return fetch(`${BASE}/skills/${agent}/${slug}/reject`, { method: "POST" }).then(
      (r) => {
        if (!r.ok && r.status !== 204) throw new Error(`HTTP ${r.status}`);
      },
    );
  },

  routines(): Promise<{ routines: Routine[] }> {
    return fetch(`${BASE}/routines`).then((r) => json<{ routines: Routine[] }>(r));
  },

  createRoutine(input: {
    agent: string;
    schedule: string;
    instruction: string;
    deliver?: string;
  }): Promise<Routine> {
    return fetch(`${BASE}/routines`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(input),
    }).then((r) => json<Routine>(r));
  },

  toggleRoutine(agent: string, id: string, action: "enable" | "pause"): Promise<Routine> {
    return fetch(`${BASE}/routines/${agent}/${id}/${action}`, { method: "POST" }).then(
      (r) => json<Routine>(r),
    );
  },

  deleteRoutine(agent: string, id: string): Promise<void> {
    return fetch(`${BASE}/routines/${agent}/${id}`, { method: "DELETE" }).then((r) => {
      if (!r.ok && r.status !== 204) throw new Error(`HTTP ${r.status}`);
    });
  },

  history(agentId: string): Promise<{ messages: { role: string; text: string; ts_iso: string }[] }> {
    return fetch(`${BASE}/agents/${agentId}/history`).then((r) =>
      json<{ messages: { role: string; text: string; ts_iso: string }[] }>(r),
    );
  },

  // Streams a chat turn as Server-Sent Events. Yields parsed ChatEvents until
  // the stream closes. Uses fetch streaming (not EventSource) so we can POST.
  async *streamChat(
    agentId: string,
    text: string,
    signal?: AbortSignal,
  ): AsyncGenerator<ChatEvent> {
    const res = await fetch(`${BASE}/agents/${agentId}/messages`, {
      method: "POST",
      headers: { "content-type": "application/json", accept: "text/event-stream" },
      body: JSON.stringify({ text }),
      signal,
    });
    if (!res.ok || !res.body) {
      throw new Error(`chat failed: HTTP ${res.status}`);
    }
    const reader = res.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      // SSE frames are separated by a blank line; each `data:` line is JSON.
      let sep: number;
      while ((sep = buffer.indexOf("\n\n")) !== -1) {
        const frame = buffer.slice(0, sep);
        buffer = buffer.slice(sep + 2);
        for (const line of frame.split("\n")) {
          const trimmed = line.trim();
          if (!trimmed.startsWith("data:")) continue;
          const payload = trimmed.slice(5).trim();
          if (!payload) continue;
          try {
            yield JSON.parse(payload) as ChatEvent;
          } catch {
            // ignore keep-alive / non-JSON comments
          }
        }
      }
    }
  },
};
