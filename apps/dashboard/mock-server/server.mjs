// Mock backend implementing the dashboard's API contract in-memory. Lets the UI
// run (npm run dev) and Playwright smoke-test without the Python orchestrator.
// In production the real orchestrator serves the identical contract — including
// the operator session, CSRF check and security headers, which are mirrored here
// so the e2e suite exercises the same flow the real thing enforces.
//
// Usage:
//   node mock-server/server.mjs               # API only on :8330
//   node mock-server/server.mjs --serve dist  # also serve built SPA (Playwright)
//
// Mock-only operator credentials (never used by the real orchestrator, which
// reads a hash from the environment): tony / recons-dev.
import http from "node:http";
import { randomBytes } from "node:crypto";
import { readFile } from "node:fs/promises";
import { existsSync } from "node:fs";
import { extname, join, normalize } from "node:path";

const PORT = Number(process.env.PORT || 8330);
const serveArg = process.argv.indexOf("--serve");
const distDir = serveArg !== -1 ? process.argv[serveArg + 1] : null;
const OPERATOR_USER = process.env.MOCK_OPERATOR_USER || "tony";
const OPERATOR_PASSWORD = process.env.MOCK_OPERATOR_PASSWORD || "recons-dev";

const COLORS = ["#8b5cf6", "#3b82f6", "#2fbf5f", "#17a398", "#f5c542"];
let seq = 0;

// --- security headers (same set the orchestrator sends) -----------------------
const SECURITY_HEADERS = {
  "x-content-type-options": "nosniff",
  "x-frame-options": "DENY",
  "referrer-policy": "same-origin",
  "permissions-policy": "camera=(), microphone=(), geolocation=(), payment=(), usb=()",
  "cross-origin-opener-policy": "same-origin",
  "cross-origin-resource-policy": "same-origin",
  "content-security-policy":
    "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; " +
    "img-src 'self' data:; font-src 'self' data:; connect-src 'self'; manifest-src 'self'; " +
    "worker-src 'self'; frame-ancestors 'none'; base-uri 'self'; form-action 'self'; object-src 'none'",
};

// --- operator sessions ----------------------------------------------------------
/** @type {Map<string, {user: string, csrf: string}>} */
const sessions = new Map();
const PUBLIC = new Set(["/api/health", "/api/hooks", "/api/auth/login", "/api/auth/session"]);
const CSRF_EXEMPT = new Set(["/api/auth/login", "/api/hooks"]);

function parseCookies(req) {
  const out = {};
  for (const part of (req.headers.cookie || "").split(";")) {
    const [k, ...v] = part.trim().split("=");
    if (k) out[k] = decodeURIComponent(v.join("="));
  }
  return out;
}
function sessionOf(req) {
  const id = parseCookies(req).recons_session;
  return id ? sessions.get(id) || null : null;
}
function sessionInfo(sess, csrf = sess?.csrf ?? null) {
  return {
    authenticated: !!sess,
    operator: sess?.user ?? null,
    via: sess ? "password" : null,
    csrf_token: sess ? csrf : null,
    mode: "password",
    configured: true,
    reason: null,
  };
}

/** @type {Map<string, any>} */
const agents = new Map();
function seed(name, role, tier, isLead) {
  const id = name.toLowerCase().replace(/[^a-z0-9]+/g, "-");
  agents.set(id, {
    id,
    name,
    role,
    tier,
    avatar_color: COLORS[agents.size % COLORS.length],
    status: "running",
    is_lead: isLead,
    created_at: "2026-08-15T12:00:00+00:00",
  });
}
seed("Recon", "Lead assistant — coordinates the team", "lead", true);
seed("Scout", "Researches suppliers and drafts outreach", "workhorse", false);
seed("Clerk", "Handles admin, invoices and bulk data entry", "bulk", false);

// A small synthetic audit trail so the Audit + Sessions tabs render in dev/e2e.
const BASE = 1786000000;
const AUDIT = [
  { source: "session", agent_id: "recon", kind: "message", role: "user", ts: BASE + 1, session_id: "recon-sess-1", session_key: "agent:recon:main", text: "Find three suppliers for brake calipers." },
  { source: "session", agent_id: "recon", kind: "message", role: "assistant", ts: BASE + 2, session_id: "recon-sess-1", session_key: "agent:recon:main", text: "Searching now." },
  { source: "session", agent_id: "recon", kind: "tool_call", role: "assistant", ts: BASE + 2, session_id: "recon-sess-1", session_key: "agent:recon:main", text: "browser_navigate", extra: { tool: "browser_navigate" } },
  { source: "session", agent_id: "recon", kind: "tool_result", role: "tool", ts: BASE + 3, session_id: "recon-sess-1", session_key: "agent:recon:main", text: "OK 200", extra: { tool: "browser_navigate" } },
  { source: "session", agent_id: "recon", kind: "message", role: "assistant", ts: BASE + 4, session_id: "recon-sess-1", session_key: "agent:recon:main", text: "Here are three suppliers." },
  { source: "a2a", agent_id: "recon", kind: "a2a", ts: BASE + 3, peer_from: "recon", peer_to: "scout", session_key: "agent:scout:job42", text: "Please price these three suppliers.", extra: { direction: "outbound" } },
  { source: "a2a", agent_id: "recon", kind: "a2a", ts: BASE + 6, peer_from: "scout", peer_to: "recon", session_key: "agent:scout:job42", text: "Priced. Cheapest is Acme.", extra: { direction: "inbound" } },
  { source: "session", agent_id: "scout", kind: "message", role: "user", ts: BASE + 5, session_id: "scout-sess-1", session_key: "agent:scout:main", text: "Price these three suppliers." },
  { source: "session", agent_id: "scout", kind: "message", role: "assistant", ts: BASE + 6, session_id: "scout-sess-1", session_key: "agent:scout:main", text: "Priced. Cheapest is Acme." },
  { source: "cron", agent_id: "clerk", kind: "cron_run", ts: BASE + 7, text: "morning-brief", extra: { status: "ok" } },
  { source: "webhook", agent_id: "scout", kind: "lifecycle", ts: BASE + 8, text: "post_tool_call" },
].map((e, i) => ({ seq: i, ts_iso: new Date(e.ts * 1000).toISOString(), role: null, ...e }));

function operatorEvent(actor, category, action, target, extra = {}) {
  const ts = Date.now() / 1000;
  AUDIT.push({
    seq: AUDIT.length, ts, ts_iso: new Date(ts * 1000).toISOString(), source: "operator",
    agent_id: "orchestrator", kind: category, role: actor, text: `${action} ${target}`,
    extra: { actor, result: "ok", ...extra },
  });
}

// In-memory skills + routines so those tabs work in dev/e2e.
let sharedSkills = [
  { slug: "invoice-chase", name: "Invoice Chase", description: "Chase overdue invoices politely.", source: "shared", version: "1.0.0" },
];
let pendingSkills = [
  { slug: "supplier-onboard", name: "Supplier Onboard", description: "Add a new supplier to the sheet and email them the forms.", source: "pending", agent: "scout" },
];
const SKILL_BODIES = {
  "invoice-chase": {
    frontmatter: { name: "Invoice Chase", description: "Chase overdue invoices politely.", version: "1.0.0" },
    body: "---\nname: Invoice Chase\ndescription: Chase overdue invoices politely.\nversion: 1.0.0\n---\n\n# Invoice Chase\n\n1. Open the ledger.\n2. Draft the reminder.\n\n## Guardrails\n\nAsk before sending anything.\n",
    files: [{ path: "SKILL.md", size: 180, kind: "text" }],
    warnings: [],
  },
  "supplier-onboard": {
    frontmatter: { name: "Supplier Onboard", description: "Add a new supplier to the sheet and email them the forms." },
    body: "---\nname: Supplier Onboard\ndescription: Add a new supplier to the sheet and email them the forms.\n---\n\n# Supplier Onboard\n\n1. Open the supplier sheet.\n2. Add the row.\n3. Email the forms via https://portal.example/send\n",
    files: [
      { path: "SKILL.md", size: 220, kind: "text" },
      { path: "send.sh", size: 40, kind: "script" },
    ],
    fileText: { "send.sh": "curl -X POST https://portal.example/send" },
    warnings: [
      "no Guardrails section — every taught skill must say where the agent stops and asks",
      "contains scripts: send.sh — read them before approving",
      "references 1 external URL(s) — check where data would be sent",
    ],
  },
};
let routines = [
  { id: "routine-1", agent: "clerk", schedule: "every weekday at 8:00am", instruction: "Summarise overnight emails and post the brief.", enabled: true, deliver: null },
];

// Credentials: the mock keeps values in memory to mirror "configured", but
// never returns them — exactly like the real store.
const PROVIDERS = [
  { id: "claude_wrapper", name: "Claude (wrapper)", description: "Lead tier. claude-code-openai-wrapper on this VPS.",
    keys: [
      { key: "CLAUDE_WRAPPER_BASE_URL", label: "Wrapper base URL", secret: false, writable: true, required: false, hint: "e.g. http://127.0.0.1:8600/v1" },
      { key: "CLAUDE_WRAPPER_API_KEY", label: "Wrapper API key", secret: true, writable: true, required: false, hint: "Anything non-empty for a local wrapper" },
    ] },
  { id: "anthropic", name: "Anthropic API", description: "Optional pay-as-you-go fallback for the lead tier.",
    keys: [{ key: "ANTHROPIC_API_KEY", label: "API key", secret: true, writable: true, required: false, hint: "" }] },
  { id: "openai", name: "OpenAI API", description: "Workhorse tier. Prefer the ChatGPT subscription login.",
    keys: [{ key: "OPENAI_API_KEY", label: "API key", secret: true, writable: true, required: false, hint: "" }] },
  { id: "nous", name: "Nous Portal", description: "Bulk tier.",
    keys: [{ key: "NOUS_API_KEY", label: "API key", secret: true, writable: true, required: true, hint: "" }] },
  { id: "telegram", name: "Telegram gateway", description: "Optional messaging fallback.",
    keys: [
      { key: "TELEGRAM_BOT_TOKEN", label: "Bot token", secret: true, writable: true, required: false, hint: "" },
      { key: "TELEGRAM_ALLOWED_USERS", label: "Allowed user ids", secret: false, writable: true, required: false, hint: "Comma-separated Telegram user ids" },
    ] },
  { id: "orchestrator", name: "Orchestrator", description: "Audit feed signing and the operator login.",
    keys: [
      { key: "RECONS_WEBHOOK_SECRET", label: "Webhook signing secret", secret: true, writable: true, required: true, hint: "openssl rand -hex 32" },
      { key: "RECONS_SESSION_SECRET", label: "Session signing secret", secret: true, writable: false, required: false, hint: "Managed on the server" },
      { key: "RECONS_OPERATOR_USER", label: "Operator username", secret: false, writable: false, required: false, hint: "Managed on the server" },
      { key: "RECONS_OPERATOR_PASSWORD_HASH", label: "Operator password hash", secret: true, writable: false, required: false, hint: "Managed on the server" },
    ] },
];
const credentials = new Map([
  ["CLAUDE_WRAPPER_BASE_URL", { value: "http://127.0.0.1:8600/v1", updated_at: "2026-08-15T12:00:00+00:00", updated_by: "tony" }],
  ["NOUS_API_KEY", { value: "mock-not-real", updated_at: "2026-08-15T12:00:00+00:00", updated_by: "tony" }],
  ["RECONS_WEBHOOK_SECRET", { value: "mock-not-real", updated_at: null, updated_by: null }],
  ["RECONS_SESSION_SECRET", { value: "mock", updated_at: null, updated_by: null }],
  ["RECONS_OPERATOR_USER", { value: "tony", updated_at: null, updated_by: null }],
  ["RECONS_OPERATOR_PASSWORD_HASH", { value: "mock", updated_at: null, updated_by: null }],
]);
let credentialsChanged = false;
const KEY_SPEC = new Map(PROVIDERS.flatMap((p) => p.keys.map((k) => [k.key, k])));
const VALUE_RE = /^[\x21-\x7e]{1,4096}$/;

function providersResponse() {
  return {
    providers: PROVIDERS.map((p) => {
      const keys = p.keys.map((k) => {
        const c = credentials.get(k.key);
        return { ...k, configured: !!c, updated_at: c?.updated_at ?? null, updated_by: c?.updated_by ?? null };
      });
      const primary = keys[0];
      const health = !primary.configured ? "not_configured" : p.id === "claude_wrapper" ? "unreachable" : "configured";
      return { id: p.id, name: p.name, description: p.description, health, keys };
    }),
    integrations: { webhook_feed: { last_event_at: new Date((BASE + 8) * 1000).toISOString(), accepted_count: 12, rejected_count: 1 } },
    restart_required: credentialsChanged,
  };
}

function send(res, code, body, headers = {}) {
  const data = typeof body === "string" ? body : JSON.stringify(body);
  res.writeHead(code, { "content-type": "application/json", "cache-control": "no-store", ...SECURITY_HEADERS, ...headers });
  res.end(data);
}

async function readBody(req) {
  const chunks = [];
  for await (const c of req) chunks.push(c);
  return chunks.length ? JSON.parse(Buffer.concat(chunks).toString()) : {};
}

// Simulate a streamed agent reply with a tool call and an approval card.
function streamReply(res, agent, text) {
  res.writeHead(200, {
    "content-type": "text/event-stream",
    "cache-control": "no-store",
    connection: "keep-alive",
    ...SECURITY_HEADERS,
  });
  const frame = (obj) => res.write(`data: ${JSON.stringify(obj)}\n\n`);

  const words = `On it. Working on: ${text}`.split(" ");
  let i = 0;
  frame({ type: "tool_call", name: "browser_navigate" });
  const timer = setInterval(() => {
    if (i < words.length) {
      frame({ type: "token", text: (i ? " " : "") + words[i] });
      i++;
      if (i === 2) frame({ type: "tool_result", name: "browser_navigate", ok: true });
      return;
    }
    clearInterval(timer);
    if (/send|email|buy|delete|pay/i.test(text)) {
      frame({
        type: "approval",
        id: `appr-${seq++}`,
        title: "New email",
        body: `Draft from ${agent.name}:\n\nHi — following up on your enquiry…`,
        kind: "send",
      });
    }
    frame({ type: "done" });
    res.end();
  }, 40);

  res.on("close", () => clearInterval(timer));
}

async function serveStatic(req, res) {
  if (!distDir) return false;
  let path = decodeURIComponent(new URL(req.url, "http://x").pathname);
  if (path === "/") path = "/index.html";
  let file = normalize(join(distDir, path));
  if (!file.startsWith(normalize(distDir))) return send(res, 403, { error: "no" }), true;
  if (!existsSync(file)) file = join(distDir, "index.html"); // SPA fallback
  if (!existsSync(file)) return false;
  const types = {
    ".html": "text/html",
    ".js": "text/javascript",
    ".css": "text/css",
    ".svg": "image/svg+xml",
    ".png": "image/png",
    ".json": "application/json",
    ".webmanifest": "application/manifest+json",
  };
  const body = await readFile(file);
  res.writeHead(200, { "content-type": types[extname(file)] || "application/octet-stream", ...SECURITY_HEADERS });
  res.end(body);
  return true;
}

const server = http.createServer(async (req, res) => {
  const url = new URL(req.url, "http://x");
  const { pathname } = url;
  const method = req.method || "GET";
  const m = pathname.match(/^\/api\/agents\/([^/]+)(?:\/(\w+))?$/);

  try {
    if (pathname === "/api/health") return send(res, 200, { status: "ok" });

    // --- operator session gate (mirrors the orchestrator's middleware) ---
    const isApi = pathname.startsWith("/api/");
    const sess = sessionOf(req);
    if (isApi && !PUBLIC.has(pathname) && !sess) {
      return send(res, 401, { detail: "authentication required" });
    }
    if (isApi && !["GET", "HEAD", "OPTIONS"].includes(method) && !CSRF_EXEMPT.has(pathname)) {
      const site = (req.headers["sec-fetch-site"] || "").toLowerCase();
      if (site && site !== "same-origin" && site !== "none") {
        return send(res, 403, { detail: "cross-site request blocked" });
      }
      const tok = req.headers["x-csrf-token"];
      if (!sess || !tok || tok !== sess.csrf) return send(res, 403, { detail: "csrf token missing or invalid" });
    }
    const actor = sess?.user ?? "anonymous";

    if (pathname === "/api/auth/session") return send(res, 200, sessionInfo(sess));
    if (pathname === "/api/auth/login" && method === "POST") {
      if (!(req.headers["content-type"] || "").startsWith("application/json")) {
        return send(res, 415, { detail: "login body must be application/json" });
      }
      const body = await readBody(req);
      if (body.username !== OPERATOR_USER || body.password !== OPERATOR_PASSWORD) {
        operatorEvent(String(body.username || "").slice(0, 64), "auth", "login", "denied", { result: "denied" });
        return send(res, 401, { detail: "invalid credentials" });
      }
      const id = randomBytes(24).toString("base64url");
      const csrf = randomBytes(24).toString("base64url");
      sessions.set(id, { user: OPERATOR_USER, csrf });
      operatorEvent(OPERATOR_USER, "auth", "login", OPERATOR_USER);
      return send(res, 200, sessionInfo({ user: OPERATOR_USER, csrf }), {
        "set-cookie": `recons_session=${id}; Path=/; HttpOnly; SameSite=Strict; Max-Age=43200`,
      });
    }
    if (pathname === "/api/auth/logout" && method === "POST") {
      const id = parseCookies(req).recons_session;
      if (id) sessions.delete(id);
      operatorEvent(actor, "auth", "logout", actor);
      res.writeHead(204, { "set-cookie": "recons_session=; Path=/; HttpOnly; SameSite=Strict; Max-Age=0", ...SECURITY_HEADERS });
      res.end();
      return;
    }

    if (pathname === "/api/agents" && method === "GET") {
      return send(res, 200, [...agents.values()]);
    }

    if (pathname === "/api/audit/agents") {
      return send(res, 200, { agents: [...agents.keys()] });
    }

    if (pathname === "/api/audit/export.jsonl") {
      return send(
        res,
        200,
        AUDIT.map((e) => JSON.stringify(e)).join("\n"),
        { "content-type": "application/x-ndjson", "content-disposition": "attachment; filename=audit-export.jsonl" },
      );
    }

    if (pathname === "/api/audit") {
      const agent = url.searchParams.get("agent");
      const a2aOnly = url.searchParams.get("a2a_only") === "true";
      const source = url.searchParams.get("source");
      const kind = url.searchParams.get("kind");
      const q = (url.searchParams.get("q") || "").toLowerCase();
      let rows = AUDIT.slice().sort((a, b) => a.ts - b.ts || a.seq - b.seq);
      if (agent) rows = rows.filter((e) => e.agent_id === agent);
      if (source) rows = rows.filter((e) => e.source === source);
      if (kind) rows = rows.filter((e) => e.kind === kind);
      if (a2aOnly) rows = rows.filter((e) => e.source === "a2a");
      if (q)
        rows = rows.filter((e) =>
          `${e.text} ${e.peer_from || ""} ${e.peer_to || ""} ${e.agent_id}`
            .toLowerCase()
            .includes(q),
        );
      return send(res, 200, { events: rows, count: rows.length });
    }

    // --- sessions ---
    if (pathname === "/api/sessions") {
      const agent = url.searchParams.get("agent");
      const groups = new Map();
      for (const e of AUDIT) {
        if (e.source !== "session" || !e.session_id) continue;
        if (agent && e.agent_id !== agent) continue;
        const key = `${e.agent_id}/${e.session_id}`;
        if (!groups.has(key)) groups.set(key, []);
        groups.get(key).push(e);
      }
      const out = [...groups.values()].map((events) => {
        const messages = events.filter((e) => e.kind === "message");
        const first = messages.find((e) => e.role === "user") || messages[0];
        return {
          agent_id: events[0].agent_id, session_id: events[0].session_id,
          session_key: events[0].session_key || null,
          started_ts: events[0].ts, last_ts: events[events.length - 1].ts,
          started_at: events[0].ts_iso, last_at: events[events.length - 1].ts_iso,
          message_count: messages.length, tool_calls: events.filter((e) => e.kind === "tool_call").length,
          preview: (first?.text || "").slice(0, 140),
        };
      }).sort((a, b) => b.last_ts - a.last_ts);
      return send(res, 200, { sessions: out });
    }
    const sm = pathname.match(/^\/api\/sessions\/([^/]+)\/([^/]+)$/);
    if (sm) {
      const [, agent, sid] = sm;
      const events = AUDIT.filter((e) => e.source === "session" && e.agent_id === agent && e.session_id === sid);
      if (!events.length) return send(res, 404, { detail: "no such session" });
      const messages = events.filter((e) => e.kind === "message");
      return send(res, 200, {
        session: {
          agent_id: agent, session_id: sid, session_key: events[0].session_key || null,
          started_ts: events[0].ts, last_ts: events[events.length - 1].ts,
          started_at: events[0].ts_iso, last_at: events[events.length - 1].ts_iso,
          message_count: messages.length, tool_calls: events.filter((e) => e.kind === "tool_call").length,
          preview: (messages[0]?.text || "").slice(0, 140),
        },
        events,
      });
    }

    if (pathname === "/api/hooks" && method === "POST") {
      return send(res, 200, { status: "accepted" });
    }

    // --- settings ---
    if (pathname === "/api/settings/providers") return send(res, 200, providersResponse());
    if (pathname === "/api/settings/security") {
      return send(res, 200, {
        mode: "password", configured: true, operator: actor, via: "password", cookie_secure: true, hsts: false,
        session_ttl_seconds: 43200, csrf_protection: true, proxy_identity_header: null, allowed_origins: [],
        rate_limits: { login_per_minute: 10, api_per_minute: 600 },
      });
    }
    if (pathname === "/api/settings/services") {
      return send(res, 200, {
        services: [...agents.values()].map((a) => ({
          agent: a.id, name: a.name, unit: `hermes-gateway@${a.id}.service`,
          status: a.status === "paused" ? "inactive" : "active",
          expected: a.status === "paused" ? "paused" : "running", healthy: true,
        })),
      });
    }
    const cm = pathname.match(/^\/api\/settings\/credentials\/([A-Z0-9_]+)$/);
    if (cm) {
      const key = cm[1];
      const spec = KEY_SPEC.get(key);
      if (!spec) return send(res, 404, { detail: "unknown credential key" });
      if (!spec.writable) return send(res, 403, { detail: `${key} is managed on the server` });
      if (method === "PUT") {
        const body = await readBody(req);
        const value = typeof body.value === "string" ? body.value.trim() : "";
        if (!value || !VALUE_RE.test(value) || /["'`\\#]/.test(value)) {
          return send(res, 422, { detail: "value contains unsupported characters (printable ASCII only; no spaces, quotes, backslashes or #)" });
        }
        const action = credentials.has(key) ? "replaced" : "created";
        credentials.set(key, { value, updated_at: new Date().toISOString(), updated_by: actor });
        credentialsChanged = true;
        operatorEvent(actor, "credential", action, key, { provider: PROVIDERS.find((p) => p.keys.some((k) => k.key === key))?.id });
        return send(res, 200, { key, action, configured: true, updated_at: credentials.get(key).updated_at, restart_required: true });
      }
      if (method === "DELETE") {
        if (!credentials.has(key)) return send(res, 404, { detail: "credential is not set" });
        credentials.delete(key);
        credentialsChanged = true;
        operatorEvent(actor, "credential", "removed", key);
        return send(res, 200, { key, action: "removed", configured: false, restart_required: true });
      }
      return send(res, 405, { detail: "method not allowed" });
    }

    // --- skills ---
    if (pathname === "/api/skills" && method === "GET") {
      return send(res, 200, { shared: sharedSkills, pending: pendingSkills });
    }
    const sd = pathname.match(/^\/api\/skills\/(shared\/([^/]+)|pending\/([^/]+)\/([^/]+))(\/file)?$/);
    if (sd && method === "GET") {
      const [, , sharedSlug, pendAgent, pendSlug, isFile] = sd;
      const slug = sharedSlug || pendSlug;
      const list = sharedSlug ? sharedSkills : pendingSkills.filter((s) => s.agent === pendAgent);
      const skill = list.find((s) => s.slug === slug);
      const body = SKILL_BODIES[slug];
      if (!skill || !body) return send(res, 404, { detail: "no such skill" });
      if (isFile) {
        const path = url.searchParams.get("path") || "";
        if (path === "SKILL.md") return send(res, 200, { path, size: body.body.length, text: body.body, truncated: false });
        const text = body.fileText?.[path];
        if (text === undefined) return send(res, 404, { detail: "no such file in skill" });
        return send(res, 200, { path, size: text.length, text, truncated: false });
      }
      return send(res, 200, { skill, frontmatter: body.frontmatter, body: body.body, truncated: false, files: body.files, warnings: body.warnings });
    }
    const sk = pathname.match(/^\/api\/skills\/([^/]+)\/([^/]+)\/(approve|reject)$/);
    if (sk && method === "POST") {
      const [, agent, slug, action] = sk;
      const idx = pendingSkills.findIndex((s) => s.agent === agent && s.slug === slug);
      if (idx === -1) return send(res, 404, { detail: "no such pending skill" });
      const [draft] = pendingSkills.splice(idx, 1);
      operatorEvent(actor, "skill", action === "approve" ? "approved" : "rejected", `${agent}/${slug}`);
      if (action === "approve") {
        const approved = { ...draft, source: "shared", agent: null };
        sharedSkills.push(approved);
        return send(res, 200, approved);
      }
      res.writeHead(204, SECURITY_HEADERS).end();
      return;
    }

    // --- routines ---
    if (pathname === "/api/routines" && method === "GET") {
      return send(res, 200, { routines });
    }
    if (pathname === "/api/routines" && method === "POST") {
      const body = await readBody(req);
      const r = {
        id: `routine-${routines.length + 1}`,
        agent: body.agent,
        schedule: body.schedule,
        instruction: body.instruction,
        enabled: true,
        deliver: body.deliver ?? null,
      };
      routines.push(r);
      operatorEvent(actor, "routine", "created", `${r.agent}/${r.id}`);
      return send(res, 201, r);
    }
    const rt = pathname.match(/^\/api\/routines\/([^/]+)\/([^/]+)(?:\/(enable|pause))?$/);
    if (rt) {
      const [, agent, id, action] = rt;
      const r = routines.find((x) => x.agent === agent && x.id === id);
      if (!r) return send(res, 404, { detail: "no such routine" });
      if (action === "enable" || action === "pause") {
        r.enabled = action === "enable";
        return send(res, 200, r);
      }
      if (method === "DELETE") {
        routines = routines.filter((x) => !(x.agent === agent && x.id === id));
        res.writeHead(204, SECURITY_HEADERS).end();
        return;
      }
    }

    if (pathname === "/api/agents" && method === "POST") {
      const spec = await readBody(req);
      const id = String(spec.name || "").toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/(^-|-$)/g, "");
      if (!id) return send(res, 422, { detail: "invalid name" });
      if (agents.has(id)) return send(res, 409, { detail: "already exists" });
      const rec = {
        id,
        name: spec.name,
        role: spec.role,
        personality: spec.personality || "",
        tier: spec.tier || "workhorse",
        avatar_color: spec.avatar_color || COLORS[agents.size % COLORS.length],
        status: "running",
        is_lead: agents.size === 0,
        created_at: "2026-08-15T12:00:00+00:00",
      };
      agents.set(id, rec);
      operatorEvent(actor, "agent", "created", id);
      return send(res, 201, rec);
    }

    const ap = pathname.match(/^\/api\/agents\/([^/]+)\/approvals\/([^/]+)$/);
    if (ap && method === "POST") {
      const [, id, apprId] = ap;
      if (!agents.has(id)) return send(res, 404, { detail: "no such agent" });
      const { decision } = await readBody(req);
      if (decision !== "approve" && decision !== "deny") return send(res, 422, { detail: "decision must be approve|deny" });
      operatorEvent(actor, "chat", `approval_${decision}`, `${id}/${apprId}`);
      return send(res, 200, { status: "ok", decision });
    }

    if (m) {
      const [, id, action] = m;
      const agent = agents.get(id);
      if (!agent) return send(res, 404, { detail: "no such agent" });

      if (action === "messages" && method === "POST") {
        if (agent.status === "paused") return send(res, 409, { detail: "agent is paused — resume it to chat" });
        const { text } = await readBody(req);
        return streamReply(res, agent, String(text || ""));
      }
      if (action === "pause") return (agent.status = "paused"), send(res, 200, agent);
      if (action === "resume") return (agent.status = "running"), send(res, 200, agent);
      if (!action && method === "GET") return send(res, 200, agent);
      if (!action && method === "DELETE") {
        agents.delete(id);
        res.writeHead(204, SECURITY_HEADERS).end();
        return;
      }
    }

    if (isApi) return send(res, 404, { detail: "not found" });
    if (await serveStatic(req, res)) return;
    send(res, 404, { detail: "not found" });
  } catch (err) {
    send(res, 500, { detail: String(err) });
  }
});

server.listen(PORT, "127.0.0.1", () => {
  console.log(`mock backend on http://127.0.0.1:${PORT}${distDir ? ` (serving ${distDir})` : ""}`);
});
