import { useState } from "react";

// Bottom composer pill: `+` attach on the left, "Message <Bot>" placeholder,
// send on the right. "/" and "@" hints match Grok Bot's skill/mention syntax.
export function Composer({
  botName,
  disabled,
  locked,
  onSend,
}: {
  botName: string;
  disabled?: boolean; // sending in progress: keep typing, can't send yet
  locked?: boolean; // agent paused: nothing can be sent
  onSend: (text: string) => void;
}) {
  const [text, setText] = useState("");

  function submit() {
    const t = text.trim();
    if (!t || disabled || locked) return;
    onSend(t);
    setText("");
  }

  return (
    <div className="border-t border-hairline bg-bg px-3 py-2.5">
      <div className="flex items-end gap-2">
        <button
          type="button"
          aria-label="Attach"
          className="grid h-9 w-9 shrink-0 place-items-center rounded-full bg-surface-2 text-lg text-text-secondary"
        >
          +
        </button>
        <div className="flex flex-1 items-end rounded-3xl bg-surface-2 px-3.5 py-1.5">
          <textarea
            value={text}
            onChange={(e) => setText(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && !e.shiftKey) {
                e.preventDefault();
                submit();
              }
            }}
            rows={1}
            disabled={locked}
            placeholder={locked ? `${botName} is paused` : `Message ${botName}`}
            aria-label={`Message ${botName}`}
            className="max-h-32 flex-1 resize-none bg-transparent py-1 text-[15px] text-text-primary outline-none placeholder:text-text-secondary"
          />
        </div>
        <button
          type="button"
          onClick={submit}
          disabled={disabled || locked || !text.trim()}
          aria-label="Send"
          className="grid h-9 w-9 shrink-0 place-items-center rounded-full bg-ink text-ink-contrast disabled:opacity-40"
        >
          ↑
        </button>
      </div>
    </div>
  );
}
