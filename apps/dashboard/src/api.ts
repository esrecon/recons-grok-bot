import type { Agent, ChatEvent, NewAgentInput } from "./types";

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

  deleteAgent(id: string): Promise<void> {
    return fetch(`${BASE}/agents/${id}`, { method: "DELETE" }).then((r) => {
      if (!r.ok && r.status !== 204) throw new Error(`HTTP ${r.status}`);
    });
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
