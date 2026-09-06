import { useEffect, useMemo, useState } from "react";
import type { Agent, AuditEvent, SessionDetail, SessionSummary } from "../types";
import { api } from "../api";
import { BotAvatar } from "../components/BotAvatar";
import { MessageBubble } from "../components/MessageBubble";
import { formatTs } from "../lib/format";

// Conversation history across every agent: a list of sessions (newest first,
// filterable by agent, searchable) and the full transcript of any of them,
// rendered with the same bubbles as live chat. Read-only — it is the record.
export function SessionsView({ agents }: { agents: Agent[] }) {
  const [sessions, setSessions] = useState<SessionSummary[]>([]);
  const [agent, setAgent] = useState("");
  const [q, setQ] = useState("");
  const [selected, setSelected] = useState<SessionSummary | null>(null);
  const [detail, setDetail] = useState<SessionDetail | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  const byId = useMemo(() => new Map(agents.map((a) => [a.id, a])), [agents]);
  const nameOf = (id: string) => byId.get(id)?.name ?? id;
  const colorOf = (id: string) => byId.get(id)?.avatar_color ?? "#8e8e93";

  useEffect(() => {
    let live = true;
    setLoading(true);
    api
      .sessions(agent || undefined)
      .then((r) => {
        if (live) {
          setSessions(r.sessions);
          setError(null);
        }
      })
      .catch((e) => live && setError(e instanceof Error ? e.message : "Failed to load"))
      .finally(() => live && setLoading(false));
    return () => {
      live = false;
    };
  }, [agent]);

  useEffect(() => {
    if (!selected) {
      setDetail(null);
      return;
    }
    let live = true;
    api
      .sessionDetail(selected.agent_id, selected.session_id)
      .then((d) => live && setDetail(d))
      .catch((e) => live && setError(e instanceof Error ? e.message : "Failed to load"));
    return () => {
      live = false;
    };
  }, [selected]);

  const needle = q.trim().toLowerCase();
  const visible = needle
    ? sessions.filter(
        (s) =>
          s.preview.toLowerCase().includes(needle) ||
          nameOf(s.agent_id).toLowerCase().includes(needle) ||
          (s.session_key ?? "").toLowerCase().includes(needle),
      )
    : sessions;

  return (
    <section className="flex h-full flex-1 flex-col bg-bg">
      <header className="border-b border-hairline px-4 py-3">
        <h1 className="text-[17px] font-semibold text-text-primary">Sessions</h1>
        <p className="mt-0.5 text-[13px] text-text-secondary">
          Every conversation each agent has had, with its tool activity.
        </p>
        <div className="mt-3 flex flex-wrap items-center gap-2">
          <select
            value={agent}
            onChange={(e) => {
              setAgent(e.target.value);
              setSelected(null);
            }}
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
          <input
            value={q}
            onChange={(e) => setQ(e.target.value)}
            placeholder="Search…"
            aria-label="Search sessions"
            className="min-w-0 flex-1 rounded-full bg-surface-2 px-3 py-1.5 text-sm text-text-primary outline-none"
          />
        </div>
      </header>

      <div className="flex min-h-0 flex-1">
        {/* List (hidden on phones while a transcript is open). */}
        <div
          className={`${selected ? "hidden md:block" : "block"} w-full overflow-y-auto border-hairline px-2 py-2 md:w-[340px] md:shrink-0 md:border-r`}
        >
          {loading && <p className="px-2 py-2 text-sm text-text-secondary">Loading…</p>}
          {error && <p className="px-2 py-2 text-sm text-recording">{error}</p>}
          {!loading && !error && visible.length === 0 && (
            <p className="px-2 py-2 text-sm text-text-secondary">No sessions yet.</p>
          )}
          <ul className="space-y-0.5">
            {visible.map((s) => {
              const active =
                selected?.agent_id === s.agent_id && selected?.session_id === s.session_id;
              return (
                <li key={`${s.agent_id}-${s.session_id}`}>
                  <button
                    type="button"
                    onClick={() => setSelected(s)}
                    className={`flex w-full items-start gap-3 rounded-xl px-2.5 py-2 text-left ${
                      active ? "bg-surface-2" : "hover:bg-surface-2/60"
                    }`}
                  >
                    <BotAvatar id={s.agent_id} color={colorOf(s.agent_id)} size={34} title={nameOf(s.agent_id)} />
                    <span className="min-w-0 flex-1">
                      <span className="flex items-center justify-between gap-2">
                        <span className="truncate text-[14px] font-semibold text-text-primary">
                          {nameOf(s.agent_id)}
                        </span>
                        <time className="shrink-0 text-[11px] tabular-nums text-text-secondary">
                          {formatTs(s.last_at)}
                        </time>
                      </span>
                      <span className="block truncate text-[13px] text-text-primary">
                        {s.preview || <span className="text-text-secondary">(no text)</span>}
                      </span>
                      <span className="block text-[11px] text-text-secondary">
                        {s.message_count} messages · {s.tool_calls} tool call{s.tool_calls === 1 ? "" : "s"}
                      </span>
                    </span>
                  </button>
                </li>
              );
            })}
          </ul>
        </div>

        {/* Transcript */}
        <div className={`${selected ? "flex" : "hidden md:flex"} min-w-0 flex-1 flex-col`}>
          {!selected && (
            <div className="grid flex-1 place-items-center p-6 text-center text-sm text-text-secondary">
              Pick a session to read the transcript.
            </div>
          )}
          {selected && (
            <>
              <div className="flex items-center gap-2 border-b border-hairline px-4 py-2">
                <button
                  type="button"
                  onClick={() => setSelected(null)}
                  className="text-sm font-medium text-text-secondary md:hidden"
                >
                  <span aria-hidden>‹</span> Sessions
                </button>
                <div className="min-w-0 flex-1">
                  <p className="truncate text-[14px] font-semibold text-text-primary">
                    {nameOf(selected.agent_id)} · {formatTs(selected.started_at)}
                  </p>
                  {selected.session_key && (
                    <p className="truncate font-mono text-[11px] text-text-secondary">
                      {selected.session_key}
                    </p>
                  )}
                </div>
              </div>
              <div className="flex-1 space-y-2.5 overflow-y-auto px-4 py-4" data-testid="transcript">
                {!detail && <p className="text-sm text-text-secondary">Loading…</p>}
                {detail?.events.map((e, i) => (
                  <TranscriptEvent key={`${e.seq}-${e.kind}-${i}`} event={e} />
                ))}
              </div>
            </>
          )}
        </div>
      </div>
    </section>
  );
}

function TranscriptEvent({ event }: { event: AuditEvent }) {
  if (event.kind === "message") {
    const role = (event.role ?? "").toLowerCase();
    if (role === "user" || role === "assistant") {
      return (
        <MessageBubble
          message={{ id: `${event.seq}-${event.ts}`, role, text: event.text }}
        />
      );
    }
    return (
      <div className="my-1 text-center text-xs text-text-secondary">
        {event.role ? `${event.role}: ` : ""}
        {event.text}
      </div>
    );
  }
  if (event.kind === "tool_call") {
    return (
      <div className="inline-flex items-center gap-1.5 rounded-full bg-surface-2 px-3 py-1 text-xs text-text-secondary">
        <span aria-hidden>⚙</span>
        <span className="font-medium">{event.text || String(event.extra?.tool ?? "tool")}</span>
      </div>
    );
  }
  if (event.kind === "tool_result") {
    return (
      <div
        className="max-w-[78%] truncate rounded-xl bg-surface px-3 py-1 text-xs text-text-secondary"
        title={String(event.extra?.tool ?? "")}
      >
        <span aria-hidden>✓ </span>
        {event.text}
      </div>
    );
  }
  return (
    <div className="my-1 text-center text-xs text-text-secondary">
      {event.kind}: {event.text}
    </div>
  );
}
