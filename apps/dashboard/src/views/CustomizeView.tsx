import { useCallback, useEffect, useState } from "react";
import type { Agent, ImproveField, ModelOption } from "../types";
import { api } from "../api";
import { AVATAR_COLORS } from "../theme";
import { BotAvatar } from "../components/BotAvatar";

// The Customize tab: give each teammate a face (generated headshot), a name,
// a job, a personality, a soul, and a brain (model). Every text field carries
// an ✨ Improve button that rewrites it via the cheap model tier server-side.
export function CustomizeView({
  agents,
  onChanged,
}: {
  agents: Agent[];
  onChanged: () => void;
}) {
  const [selectedId, setSelectedId] = useState<string>(agents[0]?.id ?? "");
  useEffect(() => {
    if (!agents.some((a) => a.id === selectedId)) {
      setSelectedId(agents[0]?.id ?? "");
    }
  }, [agents, selectedId]);
  const selected = agents.find((a) => a.id === selectedId) ?? null;

  return (
    <section className="flex h-full flex-1 flex-col bg-bg">
      <header className="border-b border-hairline px-4 py-3">
        <h1 className="text-[17px] font-semibold text-text-primary">
          Customize agents
        </h1>
        <p className="mt-0.5 text-[13px] text-text-secondary">
          Give each teammate a face, a name, a personality — and pick the model
          it thinks with.
        </p>
      </header>

      <div className="flex-1 overflow-y-auto px-4 py-4">
        {agents.length === 0 && (
          <p className="text-sm text-text-secondary">
            No agents yet. Create one from the roster first.
          </p>
        )}

        {agents.length > 0 && (
          <div className="mb-4 flex gap-2 overflow-x-auto pb-1" role="tablist">
            {agents.map((a) => (
              <button
                key={a.id}
                type="button"
                role="tab"
                aria-selected={a.id === selectedId}
                aria-label={`Customize ${a.name}`}
                onClick={() => setSelectedId(a.id)}
                className={`flex min-w-[76px] shrink-0 flex-col items-center gap-1 rounded-xl px-3 py-2 ${
                  a.id === selectedId ? "bg-surface-2" : "hover:bg-surface-2/60"
                }`}
              >
                <BotAvatar
                  id={a.id}
                  color={a.avatar_color}
                  size={40}
                  title={a.name}
                  imageVersion={a.avatar_version}
                />
                <span className="max-w-[88px] truncate text-[12px] font-medium text-text-primary">
                  {a.name}
                </span>
              </button>
            ))}
          </div>
        )}

        {selected && (
          <AgentEditor key={selected.id} agent={selected} onChanged={onChanged} />
        )}
      </div>
    </section>
  );
}

// api.json() throws the raw response body; surface FastAPI's {"detail": …}
// as plain text and fall back to the body itself.
function errText(e: unknown): string {
  const raw = e instanceof Error ? e.message : String(e);
  try {
    const parsed = JSON.parse(raw) as { detail?: unknown };
    if (parsed && typeof parsed.detail === "string") return parsed.detail;
  } catch {
    // not JSON — use as-is
  }
  return raw;
}

function ImproveButton({
  field,
  text,
  agent,
  onResult,
  onError,
}: {
  field: ImproveField;
  text: string;
  agent: Agent;
  onResult: (text: string) => void;
  onError: (err: string | null) => void;
}) {
  const [busy, setBusy] = useState(false);
  return (
    <button
      type="button"
      aria-label={`Improve ${field}`}
      disabled={busy || !text.trim()}
      onClick={async () => {
        setBusy(true);
        onError(null);
        try {
          const r = await api.improve({
            field,
            text,
            agent_context: { name: agent.name, role: agent.role },
          });
          onResult(r.text);
        } catch (e) {
          onError(errText(e));
        }
        setBusy(false);
      }}
      className="shrink-0 rounded-full bg-bg px-2.5 py-1 text-xs font-medium text-text-primary disabled:opacity-50"
    >
      {busy ? "✨ Improving…" : "✨ Improve"}
    </button>
  );
}

function AgentEditor({ agent, onChanged }: { agent: Agent; onChanged: () => void }) {
  return (
    <div className="max-w-2xl space-y-4">
      <AvatarCard agent={agent} onChanged={onChanged} />
      <IdentityCard agent={agent} onChanged={onChanged} />
      <ModelCard agent={agent} onChanged={onChanged} />
      <SoulCard agent={agent} />
    </div>
  );
}

// --- avatar -------------------------------------------------------------------

function AvatarCard({ agent, onChanged }: { agent: Agent; onChanged: () => void }) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [keySet, setKeySet] = useState<boolean | null>(null);

  useEffect(() => {
    let alive = true;
    api
      .imageSettings()
      .then((s) => alive && setKeySet(s.key_set))
      .catch(() => alive && setKeySet(null));
    return () => {
      alive = false;
    };
  }, []);

  async function generate() {
    setBusy(true);
    setError(null);
    try {
      await api.generateAvatar(agent.id);
      onChanged();
    } catch (e) {
      setError(errText(e));
    }
    setBusy(false);
  }

  const has = agent.avatar_version != null;
  return (
    <div className="rounded-card bg-surface-2 p-3">
      <h2 className="mb-2 text-sm font-semibold text-text-primary">Headshot</h2>
      <div className="flex items-center gap-4">
        <div className="avatar-hover-lg">
          <BotAvatar
            id={agent.id}
            color={agent.avatar_color}
            size={96}
            title={agent.name}
            imageVersion={agent.avatar_version}
          />
        </div>
        <div className="min-w-0">
          <p className="text-[13px] text-text-secondary">
            A Pixar-style cartoon portrait, painted from the name and job role.
            It animates when you hover it.
          </p>
          <button
            type="button"
            onClick={generate}
            disabled={busy}
            className="mt-2 rounded-full bg-ink px-4 py-1.5 text-sm font-medium text-ink-contrast disabled:opacity-50"
          >
            {busy ? "Painting…" : has ? "Regenerate headshot" : "Generate headshot"}
          </button>
          {keySet === false && (
            <p className="mt-2 text-[13px] text-text-secondary">
              Needs an image key: add one under Settings → Avatars (xAI or
              OpenAI) to enable headshots.
            </p>
          )}
          {error && <p className="mt-2 text-sm text-recording">{error}</p>}
        </div>
      </div>
    </div>
  );
}

// --- identity -----------------------------------------------------------------

function IdentityCard({ agent, onChanged }: { agent: Agent; onChanged: () => void }) {
  const [name, setName] = useState(agent.name);
  const [role, setRole] = useState(agent.role);
  const [personality, setPersonality] = useState(agent.personality ?? "");
  const [color, setColor] = useState(agent.avatar_color);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const dirty: {
    name?: string;
    role?: string;
    personality?: string;
    avatar_color?: string;
  } = {};
  if (name.trim() !== agent.name) dirty.name = name.trim();
  if (role.trim() !== agent.role) dirty.role = role.trim();
  if (personality.trim() !== (agent.personality ?? "")) {
    dirty.personality = personality.trim();
  }
  if (color !== agent.avatar_color) dirty.avatar_color = color;
  const hasChanges = Object.keys(dirty).length > 0;

  async function save() {
    setBusy(true);
    setError(null);
    try {
      await api.updateAgent(agent.id, dirty);
      onChanged();
    } catch (e) {
      setError(errText(e));
    }
    setBusy(false);
  }

  return (
    <div className="rounded-card bg-surface-2 p-3">
      <h2 className="mb-2 text-sm font-semibold text-text-primary">Identity</h2>

      <label className="mb-1 block text-[13px] font-medium text-text-primary">
        Name
      </label>
      <div className="mb-3 flex items-center gap-2">
        <input
          value={name}
          onChange={(e) => setName(e.target.value)}
          aria-label="Name"
          className="w-full rounded-xl bg-bg px-3 py-2 text-sm text-text-primary outline-none"
        />
        <ImproveButton
          field="name"
          text={name}
          agent={agent}
          onResult={setName}
          onError={setError}
        />
      </div>

      <label className="mb-1 block text-[13px] font-medium text-text-primary">
        Job role
      </label>
      <div className="mb-3 flex items-center gap-2">
        <input
          value={role}
          onChange={(e) => setRole(e.target.value)}
          aria-label="Job role"
          className="w-full rounded-xl bg-bg px-3 py-2 text-sm text-text-primary outline-none"
        />
        <ImproveButton
          field="role"
          text={role}
          agent={agent}
          onResult={setRole}
          onError={setError}
        />
      </div>

      <label className="mb-1 block text-[13px] font-medium text-text-primary">
        How it works
      </label>
      <div className="mb-3 flex items-start gap-2">
        <textarea
          value={personality}
          onChange={(e) => setPersonality(e.target.value)}
          rows={3}
          aria-label="Personality"
          placeholder="Standing rules, tone, what to always/never do…"
          className="w-full resize-none rounded-xl bg-bg px-3 py-2 text-sm text-text-primary outline-none"
        />
        <ImproveButton
          field="personality"
          text={personality}
          agent={agent}
          onResult={setPersonality}
          onError={setError}
        />
      </div>

      <label className="mb-1 block text-[13px] font-medium text-text-primary">
        Colour
      </label>
      <div className="mb-3 flex flex-wrap gap-2">
        {AVATAR_COLORS.map((c) => (
          <button
            key={c}
            type="button"
            aria-label={`colour ${c}`}
            onClick={() => setColor(c)}
            className={`h-7 w-7 rounded-full ${
              color === c ? "ring-2 ring-ink ring-offset-2 ring-offset-surface-2" : ""
            }`}
            style={{ background: c }}
          />
        ))}
      </div>

      {error && <p className="mb-2 text-sm text-recording">{error}</p>}
      <div className="flex items-center justify-between gap-3">
        <p className="text-[12px] text-text-secondary">
          Name and job changes update the agent's soul and restart it.
        </p>
        <button
          type="button"
          aria-label="Save identity"
          onClick={save}
          disabled={!hasChanges || busy}
          className="shrink-0 rounded-full bg-ink px-4 py-1.5 text-sm font-medium text-ink-contrast disabled:opacity-50"
        >
          {busy ? "Saving…" : "Save"}
        </button>
      </div>
    </div>
  );
}

// --- model --------------------------------------------------------------------

function ModelCard({ agent, onChanged }: { agent: Agent; onChanged: () => void }) {
  const [options, setOptions] = useState<ModelOption[]>([]);
  const current =
    agent.model_provider && agent.model_name
      ? `${agent.model_provider}|${agent.model_name}`
      : "";
  const [value, setValue] = useState(current);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [adding, setAdding] = useState(false);

  const load = useCallback(async () => {
    try {
      setOptions((await api.models()).options);
    } catch (e) {
      setError(errText(e));
    }
  }, []);
  useEffect(() => {
    void load();
  }, [load]);

  const tierDefault = options.find(
    (o) => o.source === "tier" && o.tier === agent.tier,
  );
  const known = options.some((o) => `${o.provider}|${o.model}` === current);

  async function save() {
    setBusy(true);
    setError(null);
    const [provider, model] = value ? value.split("|") : ["", ""];
    try {
      await api.updateAgent(agent.id, { model_provider: provider, model_name: model });
      onChanged();
    } catch (e) {
      setError(errText(e));
    }
    setBusy(false);
  }

  return (
    <div className="rounded-card bg-surface-2 p-3">
      <h2 className="mb-2 text-sm font-semibold text-text-primary">Model</h2>
      {agent.imported ? (
        <p className="text-[13px] text-text-secondary">
          This agent was adopted from an existing Hermes install and runs on its
          own config. Promote it to manage its model here.
        </p>
      ) : (
        <>
          <div className="flex items-center gap-2">
            <select
              value={value}
              onChange={(e) => setValue(e.target.value)}
              aria-label="Model"
              className="w-full rounded-xl bg-bg px-3 py-2 text-sm text-text-primary outline-none"
            >
              <option value="">
                {tierDefault
                  ? `Default — ${tierDefault.label} (${tierDefault.model})`
                  : "Default for this agent's tier"}
              </option>
              {options.map((o) => (
                <option
                  key={`${o.provider}|${o.model}`}
                  value={`${o.provider}|${o.model}`}
                  disabled={!o.available && `${o.provider}|${o.model}` !== current}
                >
                  {o.label} — {o.model}
                  {o.available ? "" : " (no key)"}
                </option>
              ))}
              {current && !known && (
                <option value={current}>
                  {agent.model_provider} — {agent.model_name} (current)
                </option>
              )}
            </select>
            <button
              type="button"
              aria-label="Save model"
              onClick={save}
              disabled={busy || value === current}
              className="shrink-0 rounded-full bg-ink px-4 py-1.5 text-sm font-medium text-ink-contrast disabled:opacity-50"
            >
              {busy ? "Saving…" : "Save"}
            </button>
          </div>
          <p className="mt-2 text-[12px] text-text-secondary">
            Changing the model restarts this agent so it takes effect now.
          </p>

          {error && <p className="mt-2 text-sm text-recording">{error}</p>}

          <button
            type="button"
            onClick={() => setAdding((v) => !v)}
            className="mt-3 text-[13px] font-medium text-text-primary underline-offset-2 hover:underline"
          >
            {adding ? "Hide add a model" : "Add a model…"}
          </button>
          {adding && (
            <AddModelForm
              options={options}
              onSaved={() => {
                setAdding(false);
                void load();
              }}
              onRemoved={() => void load()}
            />
          )}
        </>
      )}
    </div>
  );
}

function AddModelForm({
  options,
  onSaved,
  onRemoved,
}: {
  options: ModelOption[];
  onSaved: () => void;
  onRemoved: () => void;
}) {
  const [label, setLabel] = useState("");
  const [baseUrl, setBaseUrl] = useState("https://");
  const [model, setModel] = useState("");
  const [apiKey, setApiKey] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const custom = options.filter((o) => o.source === "custom");

  async function add() {
    setBusy(true);
    setError(null);
    try {
      await api.addCustomModel({
        label: label.trim(),
        base_url: baseUrl.trim(),
        model: model.trim(),
        api_key: apiKey,
      });
      setLabel("");
      setBaseUrl("https://");
      setModel("");
      setApiKey("");
      onSaved();
    } catch (e) {
      setError(errText(e));
    }
    setBusy(false);
  }

  async function remove(id: string) {
    setError(null);
    try {
      await api.deleteCustomModel(id);
      onRemoved();
    } catch (e) {
      setError(errText(e));
    }
  }

  return (
    <div className="mt-2 rounded-xl bg-bg p-3">
      <p className="mb-2 text-[13px] text-text-secondary">
        Any OpenAI-compatible provider works — e.g. xAI (`https://api.x.ai/v1`)
        or Groq. The key is stored server-side and never shown again.
      </p>
      <div className="grid gap-2 sm:grid-cols-2">
        <input
          value={label}
          onChange={(e) => setLabel(e.target.value)}
          placeholder="Label — e.g. Grok"
          aria-label="Model label"
          className="rounded-xl bg-surface-2 px-3 py-2 text-sm text-text-primary outline-none"
        />
        <input
          value={model}
          onChange={(e) => setModel(e.target.value)}
          placeholder="Model id — e.g. grok-4"
          aria-label="Model id"
          className="rounded-xl bg-surface-2 px-3 py-2 text-sm text-text-primary outline-none"
        />
        <input
          value={baseUrl}
          onChange={(e) => setBaseUrl(e.target.value)}
          placeholder="Base URL"
          aria-label="Base URL"
          className="rounded-xl bg-surface-2 px-3 py-2 text-sm text-text-primary outline-none"
        />
        <input
          type="password"
          value={apiKey}
          onChange={(e) => setApiKey(e.target.value)}
          placeholder="API key"
          aria-label="API key"
          className="rounded-xl bg-surface-2 px-3 py-2 text-sm text-text-primary outline-none"
        />
      </div>
      {error && <p className="mt-2 text-sm text-recording">{error}</p>}
      <div className="mt-2 flex justify-end">
        <button
          type="button"
          onClick={add}
          disabled={busy || !label.trim() || !model.trim() || !apiKey}
          className="rounded-full bg-ink px-4 py-1.5 text-sm font-medium text-ink-contrast disabled:opacity-50"
        >
          {busy ? "Adding…" : "Add model"}
        </button>
      </div>

      {custom.length > 0 && (
        <ul className="mt-3 space-y-1">
          {custom.map((o) => (
            <li
              key={o.provider}
              className="flex items-center justify-between gap-2 text-[13px] text-text-secondary"
            >
              <span className="min-w-0 truncate">
                {o.label} — {o.model}
              </span>
              <button
                type="button"
                aria-label={`Remove model ${o.label}`}
                onClick={() => remove(o.provider)}
                className="shrink-0 rounded-full bg-surface-2 px-2.5 py-0.5 text-xs font-medium text-text-secondary"
              >
                Remove
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

// --- soul ---------------------------------------------------------------------

function SoulCard({ agent }: { agent: Agent }) {
  const [content, setContent] = useState("");
  const [loadedContent, setLoadedContent] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);

  useEffect(() => {
    let alive = true;
    api
      .getSoul(agent.id)
      .then((s) => {
        if (!alive) return;
        setContent(s.content);
        setLoadedContent(s.content);
      })
      .catch((e) => alive && setError(errText(e)));
    return () => {
      alive = false;
    };
  }, [agent.id]);

  async function save() {
    setBusy(true);
    setError(null);
    setSaved(false);
    try {
      const r = await api.putSoul(agent.id, content);
      // Show the file as actually written (the managed team block is repaired
      // server-side if an edit lost it).
      setContent(r.content);
      setLoadedContent(r.content);
      setSaved(true);
    } catch (e) {
      setError(errText(e));
    }
    setBusy(false);
  }

  return (
    <div className="rounded-card bg-surface-2 p-3">
      <div className="mb-2 flex items-center justify-between gap-2">
        <h2 className="text-sm font-semibold text-text-primary">Soul</h2>
        <ImproveButton
          field="soul"
          text={content}
          agent={agent}
          onResult={setContent}
          onError={setError}
        />
      </div>
      <p className="mb-2 text-[13px] text-text-secondary">
        Advanced: the agent's full persona file (SOUL.md). The "Your team" block
        is managed automatically and comes back if an edit removes it.
      </p>
      <textarea
        value={content}
        onChange={(e) => {
          setContent(e.target.value);
          setSaved(false);
        }}
        rows={14}
        aria-label="Soul"
        className="w-full resize-y rounded-xl bg-bg px-3 py-2 font-mono text-[12px] leading-relaxed text-text-primary outline-none"
      />
      {error && <p className="mt-2 text-sm text-recording">{error}</p>}
      <div className="mt-2 flex items-center justify-end gap-3">
        {saved && <span className="text-[12px] text-text-secondary">Saved.</span>}
        <button
          type="button"
          onClick={save}
          disabled={busy || content === loadedContent}
          className="rounded-full bg-ink px-4 py-1.5 text-sm font-medium text-ink-contrast disabled:opacity-50"
        >
          {busy ? "Saving…" : "Save soul"}
        </button>
      </div>
    </div>
  );
}
