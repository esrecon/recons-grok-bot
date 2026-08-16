import { useCallback, useEffect, useState } from "react";
import type { DiscoveredAgent } from "../types";
import { api } from "../api";
import { BotAvatar } from "./BotAvatar";
import { colorForIndex } from "../theme";

// Hermes agents already on this machine. Adopting one is non-destructive: the
// agent keeps its own home, memory and history, and carries on working through
// the Hermes CLI exactly as before — the dashboard just gains sight of it.
export function ImportAgents({
  usedColors,
  onImported,
}: {
  usedColors: string[];
  onImported: () => void;
}) {
  const [candidates, setCandidates] = useState<DiscoveredAgent[] | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      setCandidates((await api.importCandidates()).candidates);
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not scan for agents");
      setCandidates([]);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  async function adopt(c: DiscoveredAgent) {
    setBusy(c.home);
    setError(null);
    try {
      await api.importAgent({ home: c.home, name: c.name, role: c.role });
      await load();
      onImported();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Import failed");
    } finally {
      setBusy(null);
    }
  }

  if (candidates === null) {
    return <p className="text-[13px] text-text-secondary">Scanning…</p>;
  }

  const importable = candidates.filter((c) => !c.already_imported);
  if (candidates.length === 0) {
    return (
      <p className="text-[13px] text-text-secondary">
        No existing Hermes agents found on this machine.
      </p>
    );
  }

  return (
    <div data-testid="import-agents">
      <ul className="space-y-2">
        {candidates.map((c, i) => (
          <li
            key={c.home}
            className="flex items-start justify-between gap-3 rounded-card bg-surface-2 p-3"
          >
            <div className="flex min-w-0 gap-3">
              <BotAvatar
                id={c.id}
                color={colorForIndex(usedColors.length + i)}
                size={34}
                title={c.name}
              />
              <div className="min-w-0">
                <p className="text-[15px] font-medium text-text-primary">{c.name}</p>
                <p className="truncate text-[13px] text-text-secondary">
                  {c.role || c.model || "Existing Hermes agent"}
                </p>
                <p className="truncate font-mono text-[11px] text-text-secondary">
                  {c.home}
                </p>
              </div>
            </div>
            {c.already_imported ? (
              <span className="shrink-0 rounded-full bg-bg px-3 py-1.5 text-sm text-text-secondary">
                Imported
              </span>
            ) : (
              <button
                type="button"
                onClick={() => adopt(c)}
                disabled={busy === c.home}
                className="shrink-0 rounded-full bg-ink px-4 py-1.5 text-sm font-medium text-ink-contrast disabled:opacity-50"
              >
                {busy === c.home ? "Importing…" : "Import"}
              </button>
            )}
          </li>
        ))}
      </ul>
      {importable.length > 0 && (
        <p className="mt-2 text-[12px] text-text-secondary">
          Importing leaves the agent’s files untouched — it keeps its memory and
          carries on working outside the dashboard too.
        </p>
      )}
      {error && <p className="mt-2 text-sm text-recording">{error}</p>}
    </div>
  );
}
