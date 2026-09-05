import { useEffect, useState } from "react";
import type { Agent, SessionSummary } from "../types";
import { api } from "../api";

// Conversation history across agents (fleshed out in the next step).
export function SessionsView({ agents }: { agents: Agent[] }) {
  const [sessions, setSessions] = useState<SessionSummary[]>([]);
  const nameOf = (id: string) => agents.find((a) => a.id === id)?.name ?? id;
  useEffect(() => {
    api.sessions().then((r) => setSessions(r.sessions)).catch(() => setSessions([]));
  }, []);
  return (
    <section className="flex h-full flex-1 flex-col bg-bg">
      <header className="border-b border-hairline px-4 py-3">
        <h1 className="text-[17px] font-semibold text-text-primary">Sessions</h1>
      </header>
      <ul className="flex-1 overflow-y-auto px-4 py-3">
        {sessions.map((s) => (
          <li key={`${s.agent_id}-${s.session_id}`} className="py-1 text-sm text-text-primary">
            {nameOf(s.agent_id)} · {s.preview}
          </li>
        ))}
      </ul>
    </section>
  );
}
