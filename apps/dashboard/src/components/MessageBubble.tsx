import type { ChatMessage } from "../types";
import { ApprovalCard } from "./ApprovalCard";

// Bubble chat, Grok Bot style: incoming = light-gray bubble on the left,
// outgoing = near-black bubble (white text) on the right.
export function MessageBubble({
  message,
  onApprove,
}: {
  message: ChatMessage;
  onApprove?: (id: string, decision: "approve" | "deny") => void;
}) {
  const isUser = message.role === "user";

  if (message.role === "system") {
    return (
      <div className="my-2 text-center text-xs text-text-secondary">
        {message.text}
      </div>
    );
  }

  return (
    <div
      className={`flex w-full ${isUser ? "justify-end" : "justify-start"}`}
      data-role={message.role}
    >
      <div className="max-w-[78%] space-y-1.5">
        {message.toolCalls?.map((t, i) => (
          <div
            key={i}
            className="inline-flex items-center gap-1.5 rounded-full bg-surface-2 px-3 py-1 text-xs text-text-secondary"
          >
            <span className="font-medium">{t.name}</span>
            {t.ok === true && <span aria-label="done">✓</span>}
            {t.ok === false && <span aria-label="failed">✕</span>}
            {t.ok === undefined && <span className="opacity-60">running…</span>}
          </div>
        ))}

        {message.text && (
          <div
            className={
              isUser
                ? "rounded-bubble bg-bubble-out px-3.5 py-2 text-[15px] leading-snug text-bubble-out-text"
                : "rounded-bubble bg-bubble-in px-3.5 py-2 text-[15px] leading-snug text-bubble-in-text"
            }
          >
            <span className="whitespace-pre-wrap break-words">{message.text}</span>
            {message.pending && <span className="ml-0.5 animate-pulse">▍</span>}
          </div>
        )}

        {message.approval && (
          <ApprovalCard approval={message.approval} onDecision={onApprove} />
        )}
      </div>
    </div>
  );
}
