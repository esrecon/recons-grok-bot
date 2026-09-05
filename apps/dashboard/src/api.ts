import type {
  Agent,
  AuditEvent,
  AuditFilters,
  ChatEvent,
  CredentialChange,
  NewAgentInput,
  ProvidersResponse,
  Routine,
  SecurityPosture,
  ServiceStatus,
  SessionDetail,
  SessionInfo,
  SessionSummary,
  Skill,
  SkillDetail,
  SkillFileContent,
} from "./types";

// Single-origin client for the orchestrator (proxied to the mock server in dev).
//
// Auth plumbing: the operator session is an HttpOnly cookie the browser sends
// on its own (same-origin). What the client must do is (1) carry the session's
// CSRF token on every state-changing request and (2) treat a 401 anywhere as
// "signed out" so the shell can show the login screen instead of failing
// quietly. No credential is ever kept in JavaScript beyond the CSRF token.
const BASE = "/api";

type AuthState = "signed-out";
const listeners = new Set<(s: AuthState) => void>();
let csrf: string | null = null;

export function onAuthChange(fn: (s: AuthState) => void): () => void {
  listeners.add(fn);
  return () => {
    listeners.delete(fn);
  };
}

export function resetAuthForTests(): void {
  csrf = null;
  listeners.clear();
}

export class ApiError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

async function readDetail(res: Response): Promise<string> {
  const text = await res.text().catch(() => "");
  try {
    const parsed = JSON.parse(text) as { detail?: unknown };
    if (typeof parsed.detail === "string") return parsed.detail;
    if (parsed.detail) return JSON.stringify(parsed.detail);
  } catch {
    // not JSON
  }
  return text || res.statusText || `HTTP ${res.status}`;
}

function signedOut(): never {
  csrf = null;
  listeners.forEach((fn) => fn("signed-out"));
  throw new ApiError(401, "Signed out — please sign in again.");
}

function buildInit(init: RequestInit & { json?: unknown } = {}): RequestInit {
  const method = (init.method ?? "GET").toUpperCase();
  const headers = new Headers(init.headers);
  let body = init.body;
  if (init.json !== undefined) {
    headers.set("content-type", "application/json");
    body = JSON.stringify(init.json);
  }
  if (method !== "GET" && method !== "HEAD" && csrf) headers.set("x-csrf-token", csrf);
  const { json: _json, ...rest } = init;
  void _json;
  return { ...rest, method, headers, body, credentials: "same-origin" };
}

async function request<T>(path: string, init: RequestInit & { json?: unknown } = {}): Promise<T> {
  const res = await fetch(`${BASE}${path}`, buildInit(init));
  if (res.status === 401) signedOut();
  if (!res.ok) throw new ApiError(res.status, await readDetail(res));
  if (res.status === 204) return undefined as T;
  return res.json() as Promise<T>;
}

function rememberSession(info: SessionInfo): SessionInfo {
  csrf = info.authenticated ? info.csrf_token : null;
  return info;
}

export const api = {
  // --- operator session -----------------------------------------------------
  async session(): Promise<SessionInfo> {
    return rememberSession(await request<SessionInfo>("/auth/session"));
  },

  async login(username: string, password: string): Promise<SessionInfo> {
    // A wrong password is a 401 too, but it must not read as "signed out".
    const res = await fetch(`${BASE}/auth/login`, buildInit({ method: "POST", json: { username, password } }));
    if (!res.ok) throw new ApiError(res.status, await readDetail(res));
    return rememberSession((await res.json()) as SessionInfo);
  },

  async logout(): Promise<void> {
    try {
      await request<void>("/auth/logout", { method: "POST" });
    } finally {
      csrf = null;
    }
  },

  // --- agents ---------------------------------------------------------------
  listAgents(): Promise<Agent[]> {
    return request<Agent[]>("/agents");
  },

  createAgent(input: NewAgentInput): Promise<Agent> {
    return request<Agent>("/agents", { method: "POST", json: input });
  },

  setStatus(id: string, action: "pause" | "resume"): Promise<Agent> {
    return request<Agent>(`/agents/${enc(id)}/${action}`, { method: "POST" });
  },

  deleteAgent(id: string): Promise<void> {
    return request<void>(`/agents/${enc(id)}`, { method: "DELETE" });
  },

  decideApproval(agent: string, approvalId: string, decision: "approve" | "deny"): Promise<void> {
    return request<void>(`/agents/${enc(agent)}/approvals/${enc(approvalId)}`, {
      method: "POST",
      json: { decision },
    });
  },

  // --- audit ----------------------------------------------------------------
  audit(filters: AuditFilters = {}): Promise<{ events: AuditEvent[]; count: number }> {
    const p = new URLSearchParams();
    if (filters.agent) p.set("agent", filters.agent);
    if (filters.source) p.set("source", filters.source);
    if (filters.kind) p.set("kind", filters.kind);
    if (filters.a2a_only) p.set("a2a_only", "true");
    if (filters.q) p.set("q", filters.q);
    const qs = p.toString();
    return request(`/audit${qs ? `?${qs}` : ""}`);
  },

  auditExportUrl(): string {
    return `${BASE}/audit/export.jsonl`;
  },

  // --- sessions (history) ---------------------------------------------------
  sessions(agent?: string): Promise<{ sessions: SessionSummary[] }> {
    return request(`/sessions${agent ? `?agent=${enc(agent)}` : ""}`);
  },

  sessionDetail(agent: string, sessionId: string): Promise<SessionDetail> {
    return request(`/sessions/${enc(agent)}/${enc(sessionId)}`);
  },

  // --- skills ---------------------------------------------------------------
  skills(): Promise<{ shared: Skill[]; pending: Skill[] }> {
    return request("/skills");
  },

  skillDetail(source: "shared" | "pending", slug: string, agent?: string | null): Promise<SkillDetail> {
    const path =
      source === "pending" ? `/skills/pending/${enc(agent ?? "")}/${enc(slug)}` : `/skills/shared/${enc(slug)}`;
    return request(path);
  },

  skillFile(
    source: "shared" | "pending",
    slug: string,
    filePath: string,
    agent?: string | null,
  ): Promise<SkillFileContent> {
    const base =
      source === "pending" ? `/skills/pending/${enc(agent ?? "")}/${enc(slug)}` : `/skills/shared/${enc(slug)}`;
    return request(`${base}/file?path=${enc(filePath)}`);
  },

  approveSkill(agent: string, slug: string): Promise<Skill> {
    return request(`/skills/${enc(agent)}/${enc(slug)}/approve`, { method: "POST" });
  },

  rejectSkill(agent: string, slug: string): Promise<void> {
    return request(`/skills/${enc(agent)}/${enc(slug)}/reject`, { method: "POST" });
  },

  // --- routines -------------------------------------------------------------
  routines(): Promise<{ routines: Routine[] }> {
    return request("/routines");
  },

  createRoutine(input: {
    agent: string;
    schedule: string;
    instruction: string;
    deliver?: string;
  }): Promise<Routine> {
    return request("/routines", { method: "POST", json: input });
  },

  toggleRoutine(agent: string, id: string, action: "enable" | "pause"): Promise<Routine> {
    return request(`/routines/${enc(agent)}/${enc(id)}/${action}`, { method: "POST" });
  },

  deleteRoutine(agent: string, id: string): Promise<void> {
    return request(`/routines/${enc(agent)}/${enc(id)}`, { method: "DELETE" });
  },

  // --- settings -------------------------------------------------------------
  providers(): Promise<ProvidersResponse> {
    return request("/settings/providers");
  },

  // Write-only by design: the value goes up, only a status comes back.
  setCredential(key: string, value: string): Promise<CredentialChange> {
    return request(`/settings/credentials/${enc(key)}`, { method: "PUT", json: { value } });
  },

  removeCredential(key: string): Promise<CredentialChange> {
    return request(`/settings/credentials/${enc(key)}`, { method: "DELETE" });
  },

  securityPosture(): Promise<SecurityPosture> {
    return request("/settings/security");
  },

  services(): Promise<{ services: ServiceStatus[] }> {
    return request("/settings/services");
  },

  // --- live chat --------------------------------------------------------------
  // Streams a chat turn as Server-Sent Events. Yields parsed ChatEvents until
  // the stream closes. Uses fetch streaming (not EventSource) so we can POST
  // with the CSRF token.
  async *streamChat(
    agentId: string,
    text: string,
    signal?: AbortSignal,
    sessionId?: string | null,
  ): AsyncGenerator<ChatEvent> {
    const res = await fetch(
      `${BASE}/agents/${enc(agentId)}/messages`,
      buildInit({
        method: "POST",
        headers: { accept: "text/event-stream" },
        json: { text, session_id: sessionId ?? null },
        signal,
      }),
    );
    if (res.status === 401) signedOut();
    if (!res.ok || !res.body) {
      throw new ApiError(res.status, await readDetail(res));
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

function enc(s: string): string {
  return encodeURIComponent(s);
}
