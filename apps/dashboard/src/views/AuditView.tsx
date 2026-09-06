import { useEffect, useMemo, useState } from "react";
import type { Agent, AuditEvent } from "../types";
import { api } from "../api";
import { formatTs } from "../lib/format";

const SOURCE_LABEL: Record<string, string> = {
  session: "chat",
  a2a: "agent → agent",
  cron: "routine",
  webhook: "event",
  operator: "operator",
};

// The source filter's choices (label → API value).
const SOURCE_OPTIONS: [string, string][] = [
  ["All sources", ""],
  ["Chats", "session"],
  ["Agent → agent", "a2a"],
  ["Routine runs", "cron"],
  ["Lifecycle events", "webhook"],
  ["Operator actions", "operator"],
];

const KIND_ICON: Record<string, string> = {
  message: "💬",
  tool_call: "⚙",
  tool_result: "✓",
  a2a: "↔",
  cron_run: "◷",
  lifecycle: "•",
  auth: "🔐",
  credential: "🔑",
  skill: "◇",
  routine: "◷",
  agent: "●",
  chat: "💬",
};

// The merged audit log: every conversation turn, tool call, agent-to-agent
// message, routine run and lifecycle event across all agents, in one filterable
// timeline. This is the transcript Grok Bot doesn't give you.
export function AuditView({ agents }: { agents: Agent[] }) {
  const [events, setEvents] = useState<AuditEvent[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [agent, setAgent] = useState<string>("");
  const [source, setSource] = useState<string>("");
  const [a2aOnly, setA2aOnly] = useState(false);
  const [q, setQ] = useState("");
  const [expanded, setExpanded] = useState<string | null>(null);

  const nameOf = useMemo(() => {
    const m = new Map(agents.map((a) => [a.id, a.name]));
    return (id: string) => m.get(id) ?? id;
  }, [agents]);

  useEffect(() => {
    let live = true;
    setLoading(true);
    api
      .audit({ agent: agent || undefined, source: source || undefined, a2a_only: a2aOnly, q: q || undefined })
      .then((r) => {
        if (live) {
          setEvents(r.events);
          setError(null);
        }
      })
      .catch((e) => live && setError(e instanceof Error ? e.message : "Failed to load"))
      .finally(() => live && setLoading(false));
    return () => {
      live = false;
    };
  }, [agent, source, a2aOnly, q]);

  return (
    <section className="flex h-full flex-1 flex-col bg-bg">
      <header className="border-b border-hairline px-4 py-3">
        <div className="flex items-center justify-between">
          <h1 className="text-[17px] font-semibold text-text-primary">Audit log</h1>
          <a
            href={api.auditExportUrl()}
            className="rounded-full bg-surface-2 px-3 py-1.5 text-sm font-medium text-text-primary"
          >
            Export
          </a>
        </div>
        <p className="mt-0.5 text-[13px] text-text-secondary">
          Every message, tool call and agent-to-agent hand-off, in order — plus what you did.
        </p>

        <div className="mt-3 flex flex-wrap items-center gap-2">
          <select
            value={agent}
            onChange={(e) => setAgent(e.target.value)}
            aria-label="Filter by agent"
            className="rounded-full bg-surface-2 px-3 py-1.5 text-sm text-text-primary outline-none"
          >
            <option value="">All agents</option>
            {agents.map((a) => (
              <option key={a.id} value={a.id}>
                {a.name}
              </option>
            ))}
          </select>
          <select
            value={source}
            onChange={(e) => setSource(e.target.value)}
            aria-label="Filter by source"
            className="rounded-full bg-surface-2 px-3 py-1.5 text-sm text-text-primary outline-none"
          >
            {SOURCE_OPTIONS.map(([label, value]) => (
              <option key={value} value={value}>
                {label}
              </option>
            ))}
          </select>
          <button
            type="button"
            onClick={() => setA2aOnly((v) => !v)}
            className={`rounded-full px-3 py-1.5 text-sm ${
              a2aOnly ? "bg-ink text-ink-contrast" : "bg-surface-2 text-text-primary"
            }`}
          >
            Agent-to-agent only
          </button>
          <input
            value={q}
            onChange={(e) => setQ(e.target.value)}
            placeholder="Search…"
            aria-label="Search audit log"
            className="min-w-0 flex-1 rounded-full bg-surface-2 px-3 py-1.5 text-sm text-text-primary outline-none"
          />
        </div>
      </header>

      <div className="flex-1 overflow-y-auto px-4 py-3" data-testid="audit-list">
        {loading && <p className="text-sm text-text-secondary">Loading…</p>}
        {error && <p className="text-sm text-recording">{error}</p>}
        {!loading && !error && events.length === 0 && (
          <p className="text-sm text-text-secondary">
            No activity yet. Events appear here as your agents work and talk to each
            other.
          </p>
        )}

        <ol className="space-y-1.5">
          {events.map((e) => {
            const key = `${e.source}-${e.agent_id}-${e.seq}-${e.ts}`;
            const isOpen = expanded === key;
            return (
              <li key={key}>
                <button
                  type="button"
                  onClick={() => setExpanded(isOpen ? null : key)}
                  className="flex w-full items-start gap-3 rounded-xl px-2 py-2 text-left hover:bg-surface-2/60"
                >
                  <span className="mt-0.5 w-4 text-center" aria-hidden>
                    {KIND_ICON[e.kind] ?? "•"}
                  </span>
                  <span className="min-w-0 flex-1">
                    <span className="flex flex-wrap items-center gap-x-2 text-[13px] text-text-secondary">
                      <span className="font-medium text-text-primary">
                        {e.source === "a2a" && e.peer_from
                          ? `${nameOf(e.peer_from)} → ${nameOf(e.peer_to ?? "")}`
                          : e.source === "operator"
                            ? "Operator"
                            : nameOf(e.agent_id)}
                      </span>
                      <span className="rounded-full bg-surface-2 px-1.5 text-[11px]">
                        {SOURCE_LABEL[e.source] ?? e.source}
                      </span>
                      {e.role && <span className="text-[11px]">{e.role}</span>}
                      <time className="ml-auto tabular-nums">
                        {formatTs(e.ts_iso)}
                      </time>
                    </span>
                    <span className="mt-0.5 block truncate text-[14px] text-text-primary">
                      {e.text || <span className="text-text-secondary">(no text)</span>}
                    </span>
                  </span>
                </button>
                {isOpen && (
                  <pre className="mx-2 mb-2 overflow-x-auto rounded-xl bg-surface-2 p-3 text-[12px] text-text-primary">
                    {JSON.stringify(e, null, 2)}
                  </pre>
                )}
              </li>
            );
          })}
        </ol>
      </div>
    </section>
  );
}
