import { useCallback, useEffect, useState } from "react";
import type { Agent, TelegramStatus } from "../types";
import { api } from "../api";

// Per-agent Telegram gateway settings. The bot token is write-only — the
// server reports whether one is stored, never the value (same contract as
// provider keys). Imported agents are read-only here: their bot still lives
// on the original machine until the cutover script moves it.
export function TelegramCard({
  agent,
  onChanged,
}: {
  agent: Agent;
  onChanged: () => void;
}) {
  const [status, setStatus] = useState<TelegramStatus | null>(null);
  const [users, setUsers] = useState("");
  const [token, setToken] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const s = await api.getTelegram(agent.id);
      setStatus(s);
      setUsers((cur) => cur || s.allowed_users);
    } catch {
      setStatus(null);
    }
  }, [agent.id]);

  useEffect(() => {
    void load();
  }, [load]);

  if (!status) return null;

  if (status.imported) {
    return (
      <div className="rounded-card bg-surface-2 p-3">
        <p className="text-[15px] font-medium text-text-primary">{agent.name}</p>
        <p className="mt-1 text-[13px] text-text-secondary">
          Still served by its original machine. Move the bot here with{" "}
          <code className="font-mono text-[12px]">scripts/telegram-cutover.sh</code>{" "}
          — one bot token supports only one live connection.
        </p>
      </div>
    );
  }

  async function save(enabled: boolean) {
    setBusy(true);
    setError(null);
    try {
      await api.setTelegram(agent.id, {
        enabled,
        allowed_users: users,
        token: token.trim() || undefined,
      });
      setToken("");
      await load();
      onChanged();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not update Telegram");
    } finally {
      setBusy(false);
    }
  }

  const canEnable =
    users.trim().length > 0 && (status.token_set || token.trim().length > 0);

  return (
    <div className="rounded-card bg-surface-2 p-3">
      <div className="flex items-center justify-between gap-3">
        <div className="min-w-0">
          <p className="text-[15px] font-medium text-text-primary">{agent.name}</p>
          <p className="text-[13px] text-text-secondary">
            {status.enabled
              ? "Connected — this bot answers as the agent."
              : status.token_set
                ? "Not connected. Bot token stored."
                : "Not connected."}
          </p>
        </div>
        <button
          type="button"
          disabled={busy || (!status.enabled && !canEnable)}
          onClick={() => save(!status.enabled)}
          className="shrink-0 rounded-full bg-ink px-4 py-1.5 text-sm font-medium text-ink-contrast disabled:opacity-50"
        >
          {busy ? "Saving…" : status.enabled ? "Disable" : "Enable"}
        </button>
      </div>

      {!status.enabled && (
        <div className="mt-2 space-y-2">
          <input
            value={users}
            onChange={(e) => setUsers(e.target.value)}
            placeholder="Your numeric Telegram user id(s), comma-separated"
            className="w-full rounded-lg border border-hairline bg-bg px-3 py-1.5 text-[13px] text-text-primary placeholder:text-text-secondary"
          />
          <input
            type="password"
            value={token}
            onChange={(e) => setToken(e.target.value)}
            placeholder={
              status.token_set
                ? "Bot token (stored — paste only to replace)"
                : "Bot token from @BotFather"
            }
            className="w-full rounded-lg border border-hairline bg-bg px-3 py-1.5 text-[13px] text-text-primary placeholder:text-text-secondary"
          />
        </div>
      )}
      {error && <p className="mt-2 text-sm text-recording">{error}</p>}
    </div>
  );
}
