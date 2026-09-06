import { useState } from "react";
import type { SessionInfo } from "../types";
import { api } from "../api";

// The operator sign-in screen. Password mode posts to the orchestrator; proxy
// mode means an access proxy in front of it decides, so there is nothing to
// type here. A locked backend (no operator configured) says so instead of
// pretending a form could work.
export function LoginView({
  info,
  onSignedIn,
}: {
  info: SessionInfo;
  onSignedIn: (s: SessionInfo) => void;
}) {
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const locked = !info.configured;
  const proxy = info.mode === "proxy";

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    if (locked || busy) return;
    setBusy(true);
    setError(null);
    try {
      const s = await api.login(username.trim(), password);
      setPassword("");
      onSignedIn(s);
    } catch (err) {
      const msg = err instanceof Error ? err.message : "";
      if (/too many/i.test(msg)) setError("Too many attempts — wait a minute and try again.");
      else if (/invalid credentials|401/i.test(msg)) setError("Wrong username or password.");
      else if (/not configured/i.test(msg)) setError(msg);
      else setError(msg || "Could not sign in.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="grid h-full w-full place-items-center bg-bg p-4">
      <div className="w-full max-w-sm rounded-card bg-surface p-6 shadow-sm">
        <div className="mb-5">
          <p className="text-[17px] font-semibold text-text-primary">Recons</p>
          <p className="text-sm text-text-secondary">Operator sign-in</p>
        </div>

        {proxy ? (
          <div className="space-y-2 text-sm text-text-secondary">
            <p>
              Sign-in is handled by your access proxy. If you can see this, the
              proxy did not vouch for you: your identity is not on the operator
              allow-list, or the proxy secret is missing.
            </p>
            {locked && info.reason && <p className="text-recording">{info.reason}</p>}
          </div>
        ) : (
          <form onSubmit={submit} className="space-y-3">
            {locked && (
              <p className="rounded-xl bg-amber-bg px-3 py-2 text-sm text-amber">
                {info.reason ?? "Operator login is not configured on the server."}
              </p>
            )}
            <label className="block text-sm font-medium text-text-primary">
              Username
              <input
                value={username}
                onChange={(e) => setUsername(e.target.value)}
                autoComplete="username"
                autoCapitalize="none"
                disabled={locked}
                className="mt-1 w-full rounded-xl bg-surface-2 px-3 py-2 text-[15px] text-text-primary outline-none"
              />
            </label>
            <label className="block text-sm font-medium text-text-primary">
              Password
              <input
                type="password"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                autoComplete="current-password"
                disabled={locked}
                className="mt-1 w-full rounded-xl bg-surface-2 px-3 py-2 text-[15px] text-text-primary outline-none"
              />
            </label>
            {error && <p className="text-sm text-recording">{error}</p>}
            <button
              type="submit"
              disabled={locked || busy || !username.trim() || !password}
              className="w-full rounded-full bg-ink px-4 py-2 text-sm font-medium text-ink-contrast disabled:opacity-50"
            >
              {busy ? "Signing in…" : "Sign in"}
            </button>
          </form>
        )}
        <p className="mt-4 text-[12px] text-text-secondary">
          Private by design: reachable only over your tailnet or an authenticated
          proxy. Nothing here is public.
        </p>
      </div>
    </div>
  );
}
