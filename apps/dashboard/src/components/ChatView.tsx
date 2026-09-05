import { useEffect, useRef, useState } from "react";
import type { Agent, ChatMessage } from "../types";
import { api } from "../api";
import { BotAvatar } from "./BotAvatar";
import { MessageBubble } from "./MessageBubble";
import { Composer } from "./Composer";

let counter = 0;
const nextId = () => `m${Date.now()}-${counter++}`;

// The conversation column: header pill (avatar + name + live-view button), the
// message list, and the composer. Streams assistant replies token-by-token.
export function ChatView({ agent, onChanged }: { agent: Agent; onChanged?: () => void }) {
  void onChanged;
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [streaming, setStreaming] = useState(false);
  const scrollRef = useRef<HTMLDivElement>(null);

  // Reset the thread when switching agents (kept client-side for the shell;
  // real history load lands with the chat proxy).
  useEffect(() => {
    setMessages([]);
  }, [agent.id]);

  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight });
  }, [messages]);

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
              default:
                return msg;
            }
          }),
        );
      }
    } catch {
      setMessages((m) =>
        m.map((msg) =>
          msg.id === assistantId
            ? { ...msg, text: msg.text || "⚠ Could not reach the agent.", pending: false }
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

  function decide(id: string, decision: "approve" | "deny") {
    // Optimistic: record the decision inline. Wiring to the backend approval
    // endpoint arrives with the chat proxy.
    setMessages((m) => [
      ...m,
      {
        id: nextId(),
        role: "system",
        text: decision === "approve" ? "You approved the action." : "You denied the action.",
      },
    ]);
    void id;
  }

  return (
    <section className="flex h-full flex-1 flex-col bg-bg">
      <header className="flex items-center justify-between border-b border-hairline px-4 py-2.5">
        <div className="flex items-center gap-2 rounded-full bg-surface px-2.5 py-1">
          <BotAvatar id={agent.id} color={agent.avatar_color} size={26} title={agent.name} />
          <span className="text-[15px] font-semibold text-text-primary">
            {agent.name}
          </span>
        </div>
        <button
          type="button"
          aria-label="Agent computer"
          title="Live view (coming with the node runbook)"
          className="grid h-8 w-8 place-items-center rounded-full text-text-secondary hover:bg-surface-2"
        >
          ▭
        </button>
      </header>

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

      <Composer botName={agent.name} disabled={streaming} onSend={send} />
    </section>
  );
}
