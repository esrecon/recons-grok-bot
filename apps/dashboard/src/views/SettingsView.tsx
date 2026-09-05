import { useCallback, useEffect, useState } from "react";
import type {
  Agent,
  KeyStatus,
  ProviderHealth,
  ProvidersResponse,
  SecurityPosture,
  ServiceStatus,
  SessionInfo,
} from "../types";
import { api } from "../api";
import { StatusPill, type Tone } from "../components/StatusPill";
import { formatDuration, formatTs } from "../lib/format";

// Settings: who is signed in, which providers/integrations are configured and
// healthy, the credentials behind them (write-only — set, replace, remove;
// never read back), per-agent service health, and the security posture the
// orchestrator is running with.
export function SettingsView({
  session,
  onSignOut,
}: {
  agents: Agent[];
  session: SessionInfo;
  onSignOut: () => void;
}) {
  const [providers, setProviders] = useState<ProvidersResponse | null>(null);
  const [posture, setPosture] = useState<SecurityPosture | null>(null);
  const [services, setServices] = useState<ServiceStatus[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  const load = useCallback(async () => {
    const results = await Promise.allSettled([
      api.providers(),
      api.securityPosture(),
      api.services(),
    ]);
    const [p, s, v] = results;
    if (p.status === "fulfilled") setProviders(p.value);
    if (s.status === "fulfilled") setPosture(s.value);
    if (v.status === "fulfilled") setServices(v.value.services);
    const failed = results.find((r) => r.status === "rejected") as PromiseRejectedResult | undefined;
    setError(failed ? (failed.reason instanceof Error ? failed.reason.message : "Failed to load") : null);
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  async function afterChange(message: string) {
    setNotice(message);
    await load();
  }

  const feed = providers?.integrations?.webhook_feed;

  return (
    <section className="flex h-full flex-1 flex-col bg-bg">
      <header className="border-b border-hairline px-4 py-3">
        <h1 className="text-[17px] font-semibold text-text-primary">Settings</h1>
        <p className="mt-0.5 text-[13px] text-text-secondary">
          Providers, credentials, service health and the security posture. Values are
          write-only: nothing here ever shows a saved secret.
        </p>
      </header>

      <div className="flex-1 space-y-6 overflow-y-auto px-4 py-4">
        {error && <p className="text-sm text-recording">{error}</p>}
        {notice && (
          <p className="rounded-xl bg-surface-2 px-3 py-2 text-sm text-text-primary" role="status">
            {notice}
          </p>
        )}

        {/* Operator */}
        <div className="rounded-card bg-surface-2 p-3">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <p className="text-sm text-text-primary">
              Signed in as <span className="font-medium">{session.operator}</span>
              <span className="text-text-secondary"> · via {session.via ?? session.mode}</span>
            </p>
            <button
              type="button"
              onClick={onSignOut}
              className="rounded-full bg-bg px-4 py-1.5 text-sm font-medium text-text-primary"
            >
              Sign out
            </button>
          </div>
        </div>

        {/* Providers & integrations */}
        <div>
          <h2 className="mb-2 text-sm font-semibold text-text-primary">Providers &amp; integrations</h2>
          {providers?.restart_required && (
            <p className="mb-2 rounded-xl bg-amber-bg px-3 py-2 text-[13px] text-amber">
              Credential changes apply when agents restart — pause and resume an agent, or
              restart its service.
            </p>
          )}
          {!providers && !error && <p className="text-sm text-text-secondary">Loading…</p>}
          <ul className="grid gap-2 lg:grid-cols-2">
            {providers?.providers.map((p) => (
              <li key={p.id} className="rounded-card bg-surface-2 p-3" data-testid={`provider-${p.id}`}>
                <div className="flex items-start justify-between gap-2">
                  <div className="min-w-0">
                    <p className="text-[15px] font-medium text-text-primary">{p.name}</p>
                    <p className="text-[13px] text-text-secondary">{p.description}</p>
                  </div>
                  <HealthPill health={p.health} />
                </div>
                <ul className="mt-2 divide-y divide-hairline">
                  {p.keys.map((k) => (
                    <KeyRow key={k.key} k={k} onChanged={afterChange} />
                  ))}
                </ul>
              </li>
            ))}
          </ul>
          {feed && (
            <p className="mt-2 text-[13px] text-text-secondary">
              Audit feed: {feed.accepted_count} accepted, {feed.rejected_count} rejected
              {feed.last_event_at ? ` · last event ${formatTs(feed.last_event_at)}` : " · no events yet"}
            </p>
          )}
        </div>

        {/* Agent services */}
        <div data-testid="services">
          <h2 className="mb-2 text-sm font-semibold text-text-primary">Agent services</h2>
          {services && services.length === 0 && (
            <p className="text-sm text-text-secondary">No agents yet.</p>
          )}
          <ul className="space-y-1.5">
            {services?.map((s) => (
              <li
                key={s.agent}
                className="flex flex-wrap items-center justify-between gap-2 rounded-xl bg-surface-2 px-3 py-2"
              >
                <div className="min-w-0">
                  <p className="text-[14px] font-medium text-text-primary">{s.name}</p>
                  <p className="truncate font-mono text-[12px] text-text-secondary">{s.unit}</p>
                </div>
                <div className="flex items-center gap-2 text-[12px] text-text-secondary">
                  <span>expected {s.expected}</span>
                  <StatusPill tone={s.healthy ? "ok" : s.status === "unknown" ? "muted" : "bad"}>
                    {s.status}
                  </StatusPill>
                </div>
              </li>
            ))}
          </ul>
        </div>

        {/* Security posture */}
        <div data-testid="security-posture">
          <h2 className="mb-2 text-sm font-semibold text-text-primary">Security posture</h2>
          {posture && (
            <ul className="grid gap-1.5 rounded-card bg-surface-2 p-3 text-[13px] text-text-primary sm:grid-cols-2">
              <li>Sign-in mode: <span className="font-medium">{posture.mode}</span></li>
              <li>Secure cookies: <OnOff on={posture.cookie_secure} /></li>
              <li>CSRF protection: <OnOff on={posture.csrf_protection} /></li>
              <li>HSTS: <OnOff on={posture.hsts} /></li>
              <li>Session lifetime: {formatDuration(posture.session_ttl_seconds)}</li>
              <li>
                Rate limits: {posture.rate_limits.login_per_minute} logins/min,{" "}
                {posture.rate_limits.api_per_minute} API calls/min
              </li>
              <li className="sm:col-span-2">
                Allowed origins:{" "}
                {posture.allowed_origins.length ? posture.allowed_origins.join(", ") : "same host only"}
              </li>
              {posture.proxy_identity_header && (
                <li className="sm:col-span-2">Proxy identity header: {posture.proxy_identity_header}</li>
              )}
            </ul>
          )}
          <p className="mt-2 text-[12px] text-text-secondary">
            Loopback-bound and published only over your tailnet. The public-endpoint plan lives
            in docs/65 and is a manual step.
          </p>
        </div>
      </div>
    </section>
  );
}

function OnOff({ on }: { on: boolean }) {
  return <span className="font-medium">{on ? "on" : "off"}</span>;
}

function HealthPill({ health }: { health: ProviderHealth }) {
  const map: Record<ProviderHealth, [Tone, string]> = {
    ok: ["ok", "Reachable"],
    unreachable: ["bad", "Unreachable"],
    configured: ["ok", "Ready"],
    not_configured: ["muted", "Needs setup"],
  };
  const [tone, label] = map[health] ?? ["muted", health];
  return <StatusPill tone={tone}>{label}</StatusPill>;
}

// One credential: status, provenance, and the write-only editor. The input is
// a password field with autocomplete off, is never pre-filled, and is
// discarded the moment the save round-trip finishes.
function KeyRow({ k, onChanged }: { k: KeyStatus; onChanged: (msg: string) => Promise<void> }) {
  const [editing, setEditing] = useState(false);
  const [value, setValue] = useState("");
  const [confirming, setConfirming] = useState(false);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  async function save() {
    if (!value.trim() || busy) return;
    setBusy(true);
    setErr(null);
    try {
      const r = await api.setCredential(k.key, value);
      setValue("");
      setEditing(false);
      await onChanged(
        `${k.key} ${r.action}. Agents pick up the new value when they restart.`,
      );
    } catch (e) {
      setErr(e instanceof Error ? e.message : "Could not save.");
    } finally {
      setBusy(false);
    }
  }

  async function remove() {
    setBusy(true);
    setErr(null);
    try {
      await api.removeCredential(k.key);
      setConfirming(false);
      await onChanged(`${k.key} removed. Agents notice when they restart.`);
    } catch (e) {
      setErr(e instanceof Error ? e.message : "Could not remove.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <li className="py-2">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="min-w-0">
          <p className="text-[14px] text-text-primary">
            {k.label}
            {k.required && <span className="ml-1 text-[11px] text-text-secondary">required</span>}
          </p>
          <p className="truncate font-mono text-[11px] text-text-secondary">{k.key}</p>
          {k.updated_at && (
            <p className="text-[12px] text-text-secondary">
              Updated {formatTs(k.updated_at)}
              {k.updated_by ? ` by ${k.updated_by}` : ""}
            </p>
          )}
        </div>
        <div className="flex items-center gap-2">
          <StatusPill tone={k.configured ? "ok" : "muted"}>
            {k.configured ? "Configured" : "Not set"}
          </StatusPill>
          {k.writable ? (
            <>
              {!editing && !confirming && (
                <button
                  type="button"
                  onClick={() => {
                    setEditing(true);
                    setErr(null);
                  }}
                  className="rounded-full bg-bg px-3 py-1 text-[13px] font-medium text-text-primary"
                >
                  {k.configured ? "Replace" : "Set"}
                </button>
              )}
              {k.configured && !editing && !confirming && (
                <button
                  type="button"
                  onClick={() => setConfirming(true)}
                  className="rounded-full bg-bg px-3 py-1 text-[13px] font-medium text-text-secondary"
                >
                  Remove
                </button>
              )}
            </>
          ) : (
            <span className="text-[12px] text-text-secondary">
              {k.hint || "Managed on the server"}
            </span>
          )}
        </div>
      </div>

      {editing && (
        <form
          className="mt-2 flex flex-wrap items-center gap-2"
          onSubmit={(e) => {
            e.preventDefault();
            void save();
          }}
        >
          <input
            type="password"
            autoComplete="off"
            spellCheck={false}
            value={value}
            onChange={(e) => setValue(e.target.value)}
            aria-label={`New value for ${k.key}`}
            placeholder={k.hint || "Paste the new value"}
            className="min-w-0 flex-1 rounded-xl bg-bg px-3 py-1.5 text-[14px] text-text-primary outline-none"
          />
          <button
            type="submit"
            disabled={busy || !value.trim()}
            className="rounded-full bg-ink px-3 py-1.5 text-[13px] font-medium text-ink-contrast disabled:opacity-50"
          >
            {busy ? "Saving…" : "Save"}
          </button>
          <button
            type="button"
            onClick={() => {
              setEditing(false);
              setValue("");
              setErr(null);
            }}
            className="rounded-full bg-bg px-3 py-1.5 text-[13px] font-medium text-text-primary"
          >
            Cancel
          </button>
        </form>
      )}

      {confirming && (
        <div className="mt-2 flex flex-wrap items-center gap-2 text-[13px]">
          <span className="text-text-secondary">Remove this value from the secrets file?</span>
          <button
            type="button"
            onClick={() => void remove()}
            disabled={busy}
            className="rounded-full bg-ink px-3 py-1 font-medium text-ink-contrast disabled:opacity-50"
          >
            Confirm remove
          </button>
          <button
            type="button"
            onClick={() => setConfirming(false)}
            className="rounded-full bg-bg px-3 py-1 font-medium text-text-primary"
          >
            Cancel
          </button>
        </div>
      )}
      {err && <p className="mt-1 text-[13px] text-recording">{err}</p>}
    </li>
  );
}
