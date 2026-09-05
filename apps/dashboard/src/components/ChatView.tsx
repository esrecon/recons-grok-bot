import { useEffect, useRef, useState } from "react";
import type { Agent, ChatMessage } from "../types";
import { api } from "../api";
import { BotAvatar } from "./BotAvatar";
import { MessageBubble } from "./MessageBubble";
import { Composer } from "./Composer";

let counter = 0;
const nextId = () => `m${Date.now()}-${counter++}`;

// Survives unmounts: navigating to Skills/Audit and back must not wipe an
// in-flight conversation. Keyed by agent id; server history remains the
// durable record underneath.
const threadCache = new Map<string, ChatMessage[]>();

// The conversation column: header pill (avatar + name + head-of-staff control
// + agent menu), the message list, and the composer. Streams assistant replies
// token-by-token through the orchestrator; approval cards record the decision
// the same way.
export function ChatView({
  agent,
  onChanged,
}: {
  agent: Agent;
  onChanged?: () => void;
}) {
  const [messages, setMessages] = useState<ChatMessage[]>(
    () => threadCache.get(agent.id) ?? [],
  );
  const [streaming, setStreaming] = useState(false);
  const [menuOpen, setMenuOpen] = useState(false);
  const [confirmRemove, setConfirmRemove] = useState(false);
  const [menuError, setMenuError] = useState<string | null>(null);
  const scrollRef = useRef<HTMLDivElement>(null);
  const paused = agent.status === "paused";

  // On agent switch: show the cached in-memory thread instantly, then load the
  // durable history from the agent's own state.db. History wins when it is
  // longer (it includes turns finished after we navigated away).
  useEffect(() => {
    setMessages(threadCache.get(agent.id) ?? []);
    setMenuOpen(false);
    setConfirmRemove(false);
    let live = true;
    api
      .history(agent.id)
      .then((h) => {
        if (!live || h.messages.length === 0) return;
        const loaded: ChatMessage[] = h.messages.map((m) => ({
          id: nextId(),
          role: m.role === "user" ? "user" : "assistant",
          text: m.text,
        }));
        setMessages((cur) => (loaded.length >= cur.length ? loaded : cur));
      })
      .catch(() => {
        /* no history endpoint or no db yet — the cached thread stands */
      });
    return () => {
      live = false;
    };
  }, [agent.id]);

  // Mirror every change into the cache so unmounting loses nothing.
  useEffect(() => {
    threadCache.set(agent.id, messages);
  }, [agent.id, messages]);

  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight });
  }, [messages]);

  function system(text: string) {
    setMessages((m) => [...m, { id: nextId(), role: "system", text }]);
  }

  async function send(text: string) {
    const userMsg: ChatMessage = { id: nextId(), role: "user", text };
    const assistantId = nextId();
    setMessages((m) => [
      ...m,
      userMsg,
      { id: assistantId, role: "assistant", text: "", pending: true },
    ]);
    setStreaming(true);

    try {
      for await (const ev of api.streamChat(agent.id, text)) {
        setMessages((m) =>
          m.map((msg) => {
            if (msg.id !== assistantId) return msg;
            switch (ev.type) {
              case "token":
                return { ...msg, text: msg.text + ev.text };
              case "tool_call":
                return {
                  ...msg,
                  toolCalls: [...(msg.toolCalls ?? []), { name: ev.name }],
                };
              case "tool_result":
                return {
                  ...msg,
                  toolCalls: (msg.toolCalls ?? []).map((t) =>
                    t.name === ev.name && t.ok === undefined
                      ? { ...t, ok: ev.ok }
                      : t,
                  ),
                };
              case "approval":
                return {
                  ...msg,
                  approval: {
                    id: ev.id,
                    title: ev.title,
                    body: ev.body,
                    kind: ev.kind,
                  },
                };
              case "error":
                // Server-side detail beats a generic failure line: it names
                // the command that failed and how to fix it.
                return {
                  ...msg,
                  text: (msg.text ? msg.text + "\n" : "") + `⚠ ${ev.message}`,
                  pending: false,
                };
              default:
                return msg;
            }
          }),
        );
      }
    } catch (e) {
      const detail = e instanceof Error ? e.message : "";
      setMessages((m) =>
        m.map((msg) =>
          msg.id === assistantId
            ? {
                ...msg,
                text: msg.text || `⚠ Could not reach the agent${detail ? ` (${detail})` : ""}.`,
                pending: false,
              }
            : msg,
        ),
      );
    } finally {
      setMessages((m) =>
        m.map((msg) => (msg.id === assistantId ? { ...msg, pending: false } : msg)),
      );
      setStreaming(false);
    }
  }

  async function decide(id: string, decision: "approve" | "deny") {
    try {
      await api.decideApproval(agent.id, id, decision);
      system(decision === "approve" ? "You approved the action." : "You denied the action.");
    } catch (e) {
      system(`Couldn't record your decision: ${e instanceof Error ? e.message : "unknown error"}`);
    }
  }

  async function promoteToLead() {
    const ok = window.confirm(
      `Make ${agent.name} head of staff? The current lead steps down, and every agent's managed "Your team" section is updated.`,
    );
    if (!ok) return;
    try {
      await api.makeLead(agent.id);
      onChanged?.();
    } catch (e) {
      window.alert(e instanceof Error ? e.message : "Could not change the lead.");
    }
  }

  async function setStatus(action: "pause" | "resume") {
    setMenuError(null);
    try {
      await api.setStatus(agent.id, action);
      setMenuOpen(false);
      onChanged?.();
    } catch (e) {
      setMenuError(e instanceof Error ? e.message : "Failed");
    }
  }

  async function remove() {
    setMenuError(null);
    try {
      await api.deleteAgent(agent.id);
      setMenuOpen(false);
      setConfirmRemove(false);
      onChanged?.();
    } catch (e) {
      setMenuError(e instanceof Error ? e.message : "Failed");
    }
  }

  return (
    <section className="flex h-full flex-1 flex-col bg-bg">
      <header className="relative flex items-center justify-between border-b border-hairline px-4 py-2.5">
        <div className="flex min-w-0 items-center gap-2">
          <div className="flex items-center gap-2 rounded-full bg-surface px-2.5 py-1">
            <BotAvatar
              id={agent.id}
              color={agent.avatar_color}
              size={26}
              title={agent.name}
              imageVersion={agent.avatar_version}
            />
            <span className="text-[15px] font-semibold text-text-primary">
              {agent.name}
            </span>
          </div>
          {agent.is_lead ? (
            <span className="shrink-0 rounded-full bg-surface-2 px-2 py-0.5 text-[10px] font-medium uppercase text-text-secondary">
              head of staff
            </span>
          ) : (
            <button
              type="button"
              onClick={promoteToLead}
              className="shrink-0 text-[12px] text-text-secondary hover:text-text-primary"
            >
              Make head of staff
            </button>
          )}
        </div>
        <button
          type="button"
          aria-label="Agent menu"
          aria-haspopup="menu"
          aria-expanded={menuOpen}
          onClick={() => {
            setMenuOpen((v) => !v);
            setConfirmRemove(false);
          }}
          className="grid h-8 w-8 place-items-center rounded-full text-text-secondary hover:bg-surface-2"
        >
          ⋯
        </button>
        {menuOpen && (
          <div
            role="menu"
            className="absolute right-3 top-12 z-30 w-56 rounded-card bg-bg p-1.5 shadow-xl ring-1 ring-hairline"
          >
            {agent.imported ? (
              <p className="px-3 py-2 text-[12px] text-text-secondary">
                Imported — its gateway runs outside this platform until promoted.
              </p>
            ) : (
              <button
                type="button"
                role="menuitem"
                onClick={() => setStatus(paused ? "resume" : "pause")}
                className="block w-full rounded-lg px-3 py-2 text-left text-sm text-text-primary hover:bg-surface-2"
              >
                {paused ? "Resume agent" : "Pause agent"}
              </button>
            )}
            {!confirmRemove ? (
              <button
                type="button"
                role="menuitem"
                onClick={() => setConfirmRemove(true)}
                className="block w-full rounded-lg px-3 py-2 text-left text-sm text-text-primary hover:bg-surface-2"
              >
                Remove agent…
              </button>
            ) : (
              <button
                type="button"
                role="menuitem"
                onClick={remove}
                className="block w-full rounded-lg px-3 py-2 text-left text-sm font-medium text-recording hover:bg-surface-2"
              >
                Confirm remove (keeps its files)
              </button>
            )}
            {menuError && <p className="px-3 py-1 text-[12px] text-recording">{menuError}</p>}
          </div>
        )}
      </header>

      {paused && (
        <p className="border-b border-hairline bg-amber-bg px-4 py-1.5 text-[13px] text-amber">
          {agent.name} is paused — resume it from the menu to chat.
        </p>
      )}

      <div ref={scrollRef} className="flex-1 space-y-2.5 overflow-y-auto px-4 py-4">
        {messages.length === 0 && (
          <div className="grid h-full place-items-center text-center">
            <div>
              <BotAvatar
                id={agent.id}
                color={agent.avatar_color}
                size={64}
                title={agent.name}
                imageVersion={agent.avatar_version}
              />
              <p className="mt-3 text-[15px] font-semibold text-text-primary">
                {agent.name}
              </p>
              <p className="mx-auto mt-1 max-w-xs text-sm text-text-secondary">
                {agent.role}
              </p>
            </div>
          </div>
        )}
        {messages.map((m) => (
          <MessageBubble key={m.id} message={m} onApprove={decide} />
        ))}
      </div>

      <Composer botName={agent.name} disabled={streaming} locked={paused} onSend={send} />
    </section>
  );
}
