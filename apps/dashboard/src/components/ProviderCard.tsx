import { useEffect, useRef, useState } from "react";
import type { LoginSession, Provider } from "../types";
import { api } from "../api";

// One provider, connected from the app: paste a key, or sign in with a
// subscription (device-code flow shown inline — link, code, live status).
// Stored secrets are never displayed back; state is "Connected" or nothing.
export function ProviderCard({
  provider,
  onChanged,
  note,
}: {
  provider: Provider;
  onChanged: () => void;
  // Provenance from the credential catalogue ("Key updated … by …"), never a value.
  note?: string | null;
}) {
  const [key, setKey] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [login, setLogin] = useState<LoginSession | null>(null);
  const poll = useRef<number | null>(null);

  const connected = provider.state === "configured";

  useEffect(() => {
    return () => {
      if (poll.current) window.clearInterval(poll.current);
    };
  }, []);

  async function saveKey() {
    if (!key.trim()) return;
    setBusy(true);
    setError(null);
    try {
      await api.saveProviderKey(provider.id, key.trim());
      setKey("");
      onChanged();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not save that key.");
    } finally {
      setBusy(false);
    }
  }

  async function disconnect() {
    setBusy(true);
    try {
      await api.clearProviderKey(provider.id);
      onChanged();
    } finally {
      setBusy(false);
    }
  }

  async function signIn() {
    setBusy(true);
    setError(null);
    try {
      const session = await api.startProviderLogin(provider.id);
      setLogin(session);
      poll.current = window.setInterval(async () => {
        try {
          const next = await api.pollProviderLogin(session.id);
          setLogin(next);
          if (next.status === "success" || next.status === "failed") {
            if (poll.current) window.clearInterval(poll.current);
            poll.current = null;
            setBusy(false);
            if (next.status === "success") onChanged();
          }
        } catch {
          /* keep polling; a transient error shouldn't kill the flow */
        }
      }, 1500);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not start sign-in.");
      setBusy(false);
    }
  }

  return (
    <div className="rounded-card bg-surface-2 p-4" data-testid={`provider-${provider.id}`}>
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="flex items-center gap-2">
            <h3 className="text-[15px] font-semibold text-text-primary">
              {provider.label}
            </h3>
            <span className="rounded-full bg-bg px-2 py-0.5 text-[11px] uppercase tracking-wide text-text-secondary">
              {provider.tier}
            </span>
            {connected && (
              <span className="rounded-full bg-verified-bg px-2 py-0.5 text-[11px] font-medium text-verified">
                Connected
              </span>
            )}
          </div>
          <p className="mt-1 text-[13px] text-text-secondary">{provider.detail}</p>
          {note && <p className="mt-0.5 text-[12px] text-text-secondary">{note}</p>}
        </div>
        {connected && provider.method !== "service" && (
          <button
            type="button"
            onClick={disconnect}
            disabled={busy}
            className="shrink-0 rounded-full bg-bg px-3 py-1.5 text-sm text-text-secondary"
          >
            Disconnect
          </button>
        )}
      </div>

      {/* --- paste-a-key providers --- */}
      {!connected && provider.method === "api_key" && (
        <div className="mt-3 flex flex-wrap gap-2">
          <input
            type="password"
            value={key}
            onChange={(e) => setKey(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && saveKey()}
            placeholder="Paste API key"
            aria-label={`${provider.label} API key`}
            className="min-w-0 flex-1 rounded-xl bg-bg px-3 py-2 text-sm text-text-primary outline-none"
          />
          <button
            type="button"
            onClick={saveKey}
            disabled={busy || !key.trim()}
            className="rounded-full bg-ink px-4 py-2 text-sm font-medium text-ink-contrast disabled:opacity-50"
          >
            {busy ? "Saving…" : "Save"}
          </button>
        </div>
      )}

      {/* --- subscription sign-in --- */}
      {!connected && provider.method === "oauth" && (
        <div className="mt-3">
          {!login && (
            <div className="flex flex-wrap items-center gap-2">
              <button
                type="button"
                onClick={signIn}
                disabled={busy}
                className="rounded-full bg-ink px-4 py-2 text-sm font-medium text-ink-contrast disabled:opacity-50"
              >
                {busy ? "Starting…" : `Sign in with ${provider.label}`}
              </button>
              <details className="text-[13px] text-text-secondary">
                <summary className="cursor-pointer">Or use an API key</summary>
                <div className="mt-2 flex flex-wrap gap-2">
                  <input
                    type="password"
                    value={key}
                    onChange={(e) => setKey(e.target.value)}
                    placeholder="Paste API key"
                    aria-label={`${provider.label} API key`}
                    className="min-w-0 flex-1 rounded-xl bg-bg px-3 py-2 text-sm text-text-primary outline-none"
                  />
                  <button
                    type="button"
                    onClick={saveKey}
                    disabled={busy || !key.trim()}
                    className="rounded-full bg-bg px-4 py-2 text-sm font-medium text-text-primary disabled:opacity-50"
                  >
                    Save
                  </button>
                </div>
              </details>
            </div>
          )}

          {login && (
            <div className="rounded-xl bg-bg p-3" data-testid="login-flow">
              {login.status === "failed" ? (
                <>
                  <p className="text-sm text-recording">{login.message}</p>
                  {login.command && (
                    <p className="mt-2 break-all font-mono text-[12px] text-text-secondary">
                      {login.command}
                    </p>
                  )}
                  <button
                    type="button"
                    onClick={() => setLogin(null)}
                    className="mt-3 rounded-full bg-surface-2 px-3 py-1.5 text-sm text-text-primary"
                  >
                    Try again
                  </button>
                </>
              ) : login.status === "success" ? (
                <p className="text-sm text-verified">{login.message}</p>
              ) : login.url ? (
                <>
                  <p className="text-sm text-text-primary">
                    Open this link and sign in:
                  </p>
                  <a
                    href={login.url}
                    target="_blank"
                    rel="noreferrer"
                    className="mt-1 block break-all text-sm font-medium text-text-primary underline"
                  >
                    {login.url}
                  </a>
                  {login.code && (
                    <p className="mt-3 text-sm text-text-secondary">
                      Enter this code:{" "}
                      <span className="rounded bg-surface-2 px-2 py-1 font-mono text-[15px] tracking-widest text-text-primary">
                        {login.code}
                      </span>
                    </p>
                  )}
                  <p className="mt-3 text-[13px] text-text-secondary">
                    Waiting for you to finish in the browser…
                  </p>
                </>
              ) : (
                <p className="text-sm text-text-secondary">Starting sign-in…</p>
              )}
            </div>
          )}
        </div>
      )}

      {/* --- local service (Claude wrapper) --- */}
      {!connected && provider.method === "service" && (
        <div className="mt-3">
          <div className="flex flex-wrap gap-2">
            <input
              type="password"
              value={key}
              onChange={(e) => setKey(e.target.value)}
              placeholder="Paste API key (optional)"
              aria-label={`${provider.label} API key`}
              className="min-w-0 flex-1 rounded-xl bg-bg px-3 py-2 text-sm text-text-primary outline-none"
            />
            <button
              type="button"
              onClick={saveKey}
              disabled={busy || !key.trim()}
              className="rounded-full bg-bg px-4 py-2 text-sm font-medium text-text-primary disabled:opacity-50"
            >
              Save
            </button>
          </div>
        </div>
      )}

      {error && <p className="mt-2 text-sm text-recording">{error}</p>}
    </div>
  );
}
