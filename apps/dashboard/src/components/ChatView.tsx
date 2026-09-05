import { useEffect, useRef, useState } from "react";
import type { Agent, ChatMessage } from "../types";
import { api } from "../api";
import { BotAvatar } from "./BotAvatar";
import { MessageBubble } from "./MessageBubble";
import { Composer } from "./Composer";

let counter = 0;
const nextId = () => `m${Date.now()}-${counter++}`;

// The conversation column: header pill (avatar + name + agent menu), the
// message list, and the composer. Streams assistant replies token-by-token
// through the orchestrator; approval cards post the decision back the same way.
export function ChatView({ agent, onChanged }: { agent: Agent; onChanged?: () => void }) {
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [streaming, setStreaming] = useState(false);
  const [menuOpen, setMenuOpen] = useState(false);
  const [confirmRemove, setConfirmRemove] = useState(false);
  const [menuError, setMenuError] = useState<string | null>(null);
  const scrollRef = useRef<HTMLDivElement>(null);
  const paused = agent.status === "paused";

  // Reset the thread when switching agents (history lives in Sessions).
  useEffect(() => {
    setMessages([]);
    setMenuOpen(false);
    setConfirmRemove(false);
  }, [agent.id]);

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
                return {
                  ...msg,
                  text: msg.text ? `${msg.text}\n⚠ ${ev.message}` : `⚠ ${ev.message}`,
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
      system(`Couldn't deliver your decision: ${e instanceof Error ? e.message : "unknown error"}`);
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
        <div className="flex items-center gap-2 rounded-full bg-surface px-2.5 py-1">
          <BotAvatar id={agent.id} color={agent.avatar_color} size={26} title={agent.name} />
          <span className="text-[15px] font-semibold text-text-primary">
            {agent.name}
          </span>
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
            <button
              type="button"
              role="menuitem"
              onClick={() => setStatus(paused ? "resume" : "pause")}
              className="block w-full rounded-lg px-3 py-2 text-left text-sm text-text-primary hover:bg-surface-2"
            >
              {paused ? "Resume agent" : "Pause agent"}
            </button>
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
              <BotAvatar id={agent.id} color={agent.avatar_color} size={64} title={agent.name} />
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
