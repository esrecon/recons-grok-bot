import { useEffect, useState } from "react";
import type { ImageSettings } from "../types";
import { api } from "../api";

const PRESETS = [
  {
    label: "xAI Grok",
    base_url: "https://api.x.ai/v1",
    model: "grok-imagine-image-2.0",
  },
  {
    label: "OpenAI",
    base_url: "https://api.openai.com/v1",
    model: "gpt-image-2",
  },
];

// Image-generation key for the Customize tab's headshots. One key, either
// provider — the endpoint is OpenAI-compatible, so a preset just fills the
// base URL + model. Write-only, like every credential here.
export function ImageCard({ onChanged }: { onChanged?: () => void }) {
  const [settings, setSettings] = useState<ImageSettings | null>(null);
  const [key, setKey] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let alive = true;
    api
      .imageSettings()
      .then((s) => alive && setSettings(s))
      .catch((e) => alive && setError(e instanceof Error ? e.message : String(e)));
    return () => {
      alive = false;
    };
  }, []);

  async function apply(input: { key?: string; base_url?: string; model?: string }) {
    setBusy(true);
    setError(null);
    try {
      setSettings(await api.setImageSettings(input));
      setKey("");
      onChanged?.();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
    setBusy(false);
  }

  const connected = settings?.key_set === true;
  return (
    <div className="rounded-card bg-surface-2 p-3" data-testid="image-card">
      <div className="flex items-center justify-between gap-2">
        <div className="min-w-0">
          <p className="text-[15px] font-semibold text-text-primary">
            Image generation
          </p>
          <p className="text-[13px] text-text-secondary">
            Powers the Pixar-style headshots in Customize.
          </p>
        </div>
        {connected && (
          <span className="shrink-0 rounded-full bg-bg px-2.5 py-1 text-xs font-medium text-text-primary">
            Connected
          </span>
        )}
      </div>

      <div className="mt-2 flex flex-wrap items-center gap-2">
        {PRESETS.map((p) => (
          <button
            key={p.label}
            type="button"
            onClick={() => apply({ base_url: p.base_url, model: p.model })}
            disabled={busy}
            className={`rounded-full px-3 py-1 text-xs font-medium ${
              settings?.base_url === p.base_url
                ? "bg-ink text-ink-contrast"
                : "bg-bg text-text-primary"
            }`}
          >
            {p.label}
          </button>
        ))}
        {settings && (
          <span className="font-mono text-[12px] text-text-secondary">
            {settings.base_url} · {settings.model}
          </span>
        )}
      </div>

      <div className="mt-2 flex items-center gap-2">
        <input
          type="password"
          value={key}
          onChange={(e) => setKey(e.target.value)}
          placeholder={connected ? "Replace API key" : "API key"}
          aria-label="Image API key"
          className="w-full rounded-xl bg-bg px-3 py-2 text-sm text-text-primary outline-none"
        />
        <button
          type="button"
          onClick={() => apply({ key })}
          disabled={busy || !key.trim()}
          className="shrink-0 rounded-full bg-ink px-4 py-1.5 text-sm font-medium text-ink-contrast disabled:opacity-50"
        >
          Save
        </button>
        {connected && (
          <button
            type="button"
            onClick={() => apply({ key: "" })}
            disabled={busy}
            className="shrink-0 rounded-full bg-bg px-3 py-1.5 text-sm font-medium text-text-secondary"
          >
            Disconnect
          </button>
        )}
      </div>
      {error && <p className="mt-2 text-sm text-recording">{error}</p>}
    </div>
  );
}
