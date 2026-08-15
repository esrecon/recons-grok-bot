import { useState } from "react";
import type { Agent, ModelTier, NewAgentInput } from "../types";
import { AVATAR_COLORS, suggestColor } from "../theme";
import { BotAvatar } from "./BotAvatar";

const TIERS: { value: ModelTier; label: string; hint: string }[] = [
  { value: "lead", label: "Lead", hint: "Claude — for your right-hand agent" },
  { value: "workhorse", label: "Workhorse", hint: "GPT — the everyday default" },
  { value: "bulk", label: "Bulk", hint: "Hermes — cheap, high-volume work" },
];

// One-click permanent-agent creation. Name + job role are all that's required;
// each new agent gets its own SOUL.md (from the role) and shares skills + keys
// with the rest of the team (handled server-side by the orchestrator).
export function NewAgentModal({
  existing,
  onClose,
  onCreate,
}: {
  existing: Agent[];
  onClose: () => void;
  onCreate: (input: NewAgentInput) => Promise<void>;
}) {
  const [name, setName] = useState("");
  const [role, setRole] = useState("");
  const [personality, setPersonality] = useState("");
  const [tier, setTier] = useState<ModelTier>("workhorse");
  const [color, setColor] = useState(() =>
    suggestColor(existing.map((a) => a.avatar_color)),
  );
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const previewId = name || "preview";

  async function submit() {
    setError(null);
    if (!name.trim() || !role.trim()) {
      setError("Give your agent a name and a one-line job.");
      return;
    }
    setBusy(true);
    try {
      await onCreate({
        name: name.trim(),
        role: role.trim(),
        personality: personality.trim() || undefined,
        tier,
        avatar_color: color,
      });
      onClose();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not create agent.");
      setBusy(false);
    }
  }

  return (
    <div
      className="fixed inset-0 z-50 grid place-items-center bg-black/40 p-4"
      onClick={onClose}
      role="dialog"
      aria-modal="true"
      aria-label="Create a new agent"
    >
      <div
        className="w-full max-w-md rounded-card bg-bg p-5 shadow-xl"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="mb-4 flex items-center gap-3">
          <BotAvatar id={previewId} color={color} size={44} title={name} />
          <div>
            <h2 className="text-lg font-semibold text-text-primary">New agent</h2>
            <p className="text-sm text-text-secondary">
              A permanent teammate with its own job.
            </p>
          </div>
        </div>

        <label className="mb-1 block text-sm font-medium text-text-primary">
          Name
        </label>
        <input
          value={name}
          onChange={(e) => setName(e.target.value)}
          placeholder="e.g. Scout"
          className="mb-3 w-full rounded-xl bg-surface-2 px-3 py-2 text-[15px] text-text-primary outline-none"
        />

        <label className="mb-1 block text-sm font-medium text-text-primary">
          Job (one line)
        </label>
        <input
          value={role}
          onChange={(e) => setRole(e.target.value)}
          placeholder="e.g. Researches suppliers and drafts outreach"
          className="mb-3 w-full rounded-xl bg-surface-2 px-3 py-2 text-[15px] text-text-primary outline-none"
        />

        <label className="mb-1 block text-sm font-medium text-text-primary">
          How it should work <span className="text-text-secondary">(optional)</span>
        </label>
        <textarea
          value={personality}
          onChange={(e) => setPersonality(e.target.value)}
          rows={2}
          placeholder="Standing rules, tone, what to always/never do…"
          className="mb-3 w-full resize-none rounded-xl bg-surface-2 px-3 py-2 text-[15px] text-text-primary outline-none"
        />

        <label className="mb-1 block text-sm font-medium text-text-primary">
          Model
        </label>
        <div className="mb-3 grid grid-cols-3 gap-2">
          {TIERS.map((t) => (
            <button
              key={t.value}
              type="button"
              onClick={() => setTier(t.value)}
              title={t.hint}
              className={`rounded-xl px-2 py-2 text-sm ${
                tier === t.value
                  ? "bg-ink text-ink-contrast"
                  : "bg-surface-2 text-text-primary"
              }`}
            >
              {t.label}
            </button>
          ))}
        </div>

        <label className="mb-1 block text-sm font-medium text-text-primary">
          Colour
        </label>
        <div className="mb-4 flex flex-wrap gap-2">
          {AVATAR_COLORS.map((c) => (
            <button
              key={c}
              type="button"
              aria-label={`colour ${c}`}
              onClick={() => setColor(c)}
              className={`h-7 w-7 rounded-full ${
                color === c ? "ring-2 ring-offset-2 ring-offset-bg ring-ink" : ""
              }`}
              style={{ background: c }}
            />
          ))}
        </div>

        {error && <p className="mb-3 text-sm text-recording">{error}</p>}

        <div className="flex justify-end gap-2">
          <button
            type="button"
            onClick={onClose}
            className="rounded-full bg-surface-2 px-4 py-2 text-sm font-medium text-text-primary"
          >
            Cancel
          </button>
          <button
            type="button"
            onClick={submit}
            disabled={busy}
            className="rounded-full bg-ink px-4 py-2 text-sm font-medium text-ink-contrast disabled:opacity-50"
          >
            {busy ? "Creating…" : "Create agent"}
          </button>
        </div>
      </div>
    </div>
  );
}
