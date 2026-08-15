// Mock backend implementing the dashboard's API contract in-memory. Lets the UI
// run (npm run dev) and Playwright smoke-test without the Python orchestrator.
// In production the real orchestrator serves the identical contract.
//
// Usage:
//   node mock-server/server.mjs            # API only on :8330
//   node mock-server/server.mjs --serve dist  # also serve built SPA (Playwright)
import http from "node:http";
import { readFile } from "node:fs/promises";
import { existsSync } from "node:fs";
import { extname, join, normalize } from "node:path";

const PORT = Number(process.env.PORT || 8330);
const serveArg = process.argv.indexOf("--serve");
const distDir = serveArg !== -1 ? process.argv[serveArg + 1] : null;

const COLORS = ["#8b5cf6", "#3b82f6", "#2fbf5f", "#17a398", "#f5c542"];
let seq = 0;

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

// A small synthetic audit trail so the Audit tab renders in dev/e2e.
const BASE = 1786000000;
const AUDIT = [
  { source: "session", agent_id: "recon", kind: "message", role: "user", ts: BASE + 1, text: "Find three suppliers for brake calipers." },
  { source: "session", agent_id: "recon", kind: "tool_call", role: "assistant", ts: BASE + 2, text: "browser_navigate", extra: { tool: "browser_navigate" } },
  { source: "a2a", agent_id: "recon", kind: "a2a", ts: BASE + 3, peer_from: "recon", peer_to: "scout", session_key: "agent:scout:job42", text: "Please price these three suppliers.", extra: { direction: "outbound" } },
  { source: "a2a", agent_id: "recon", kind: "a2a", ts: BASE + 6, peer_from: "scout", peer_to: "recon", session_key: "agent:scout:job42", text: "Priced. Cheapest is Acme.", extra: { direction: "inbound" } },
  { source: "cron", agent_id: "clerk", kind: "cron_run", ts: BASE + 7, text: "morning-brief", extra: { status: "ok" } },
  { source: "webhook", agent_id: "scout", kind: "lifecycle", ts: BASE + 8, text: "post_tool_call" },
].map((e, i) => ({ seq: i, ts_iso: new Date(e.ts * 1000).toISOString(), role: null, ...e }));

function send(res, code, body, headers = {}) {
  const data = typeof body === "string" ? body : JSON.stringify(body);
  res.writeHead(code, { "content-type": "application/json", ...headers });
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
    "cache-control": "no-cache",
    connection: "keep-alive",
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
  res.writeHead(200, { "content-type": types[extname(file)] || "application/octet-stream" });
  res.end(body);
  return true;
}

const server = http.createServer(async (req, res) => {
  const url = new URL(req.url, "http://x");
  const { pathname } = url;
  const m = pathname.match(/^\/api\/agents\/([^/]+)(?:\/(\w+))?$/);

  try {
    if (pathname === "/api/health") return send(res, 200, { status: "ok" });

    if (pathname === "/api/agents" && req.method === "GET") {
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
      const q = (url.searchParams.get("q") || "").toLowerCase();
      let rows = AUDIT.slice();
      if (agent) rows = rows.filter((e) => e.agent_id === agent);
      if (source) rows = rows.filter((e) => e.source === source);
      if (a2aOnly) rows = rows.filter((e) => e.source === "a2a");
      if (q)
        rows = rows.filter((e) =>
          `${e.text} ${e.peer_from || ""} ${e.peer_to || ""} ${e.agent_id}`
            .toLowerCase()
            .includes(q),
        );
      return send(res, 200, { events: rows, count: rows.length });
    }

    if (pathname === "/api/hooks" && req.method === "POST") {
      return send(res, 200, { status: "accepted" });
    }

    if (pathname === "/api/agents" && req.method === "POST") {
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
      return send(res, 201, rec);
    }

    if (m) {
      const [, id, action] = m;
      const agent = agents.get(id);
      if (!agent) return send(res, 404, { detail: "no such agent" });

      if (action === "messages" && req.method === "POST") {
        const { text } = await readBody(req);
        return streamReply(res, agent, String(text || ""));
      }
      if (action === "pause") return (agent.status = "paused"), send(res, 200, agent);
      if (action === "resume") return (agent.status = "running"), send(res, 200, agent);
      if (!action && req.method === "GET") return send(res, 200, agent);
      if (!action && req.method === "DELETE") {
        agents.delete(id);
        res.writeHead(204).end();
        return;
      }
    }

    if (await serveStatic(req, res)) return;
    send(res, 404, { detail: "not found" });
  } catch (err) {
    send(res, 500, { detail: String(err) });
  }
});

server.listen(PORT, "127.0.0.1", () => {
  console.log(`mock backend on http://127.0.0.1:${PORT}${distDir ? ` (serving ${distDir})` : ""}`);
});
