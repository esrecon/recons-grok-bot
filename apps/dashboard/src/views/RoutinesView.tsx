import { useCallback, useEffect, useState } from "react";
import type { Agent, Routine } from "../types";
import { api } from "../api";

// Per-agent scheduled/triggered automations (Hermes cron). Create in plain
// language ("every weekday at 8am, send the brief"), enable/pause, delete.
export function RoutinesView({ agents }: { agents: Agent[] }) {
  const [routines, setRoutines] = useState<Routine[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [agent, setAgent] = useState<string>(agents[0]?.id ?? "");
  const [schedule, setSchedule] = useState("");
  const [instruction, setInstruction] = useState("");
  const nameOf = (id: string) => agents.find((a) => a.id === id)?.name ?? id;

  const load = useCallback(async () => {
    try {
      setRoutines((await api.routines()).routines);
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to load routines");
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  async function create() {
    if (!agent || !schedule.trim() || !instruction.trim()) return;
    await api.createRoutine({ agent, schedule: schedule.trim(), instruction: instruction.trim() });
    setSchedule("");
    setInstruction("");
    await load();
  }
  async function toggle(r: Routine) {
    await api.toggleRoutine(r.agent, r.id, r.enabled ? "pause" : "enable");
    await load();
  }
  async function remove(r: Routine) {
    await api.deleteRoutine(r.agent, r.id);
    await load();
  }

  return (
    <section className="flex h-full flex-1 flex-col bg-bg">
      <header className="border-b border-hairline px-4 py-3">
        <h1 className="text-[17px] font-semibold text-text-primary">Routines</h1>
        <p className="mt-0.5 text-[13px] text-text-secondary">
          Scheduled and triggered work your agents run on their own.
        </p>
      </header>

      <div className="flex-1 overflow-y-auto px-4 py-4">
        <div className="mb-5 rounded-card bg-surface-2 p-3">
          <h2 className="mb-2 text-sm font-semibold text-text-primary">New routine</h2>
          <div className="grid gap-2 sm:grid-cols-[auto_1fr] sm:items-center">
            <select
              value={agent}
              onChange={(e) => setAgent(e.target.value)}
              aria-label="Agent"
              className="rounded-xl bg-bg px-3 py-2 text-sm text-text-primary outline-none"
            >
              {agents.map((a) => (
                <option key={a.id} value={a.id}>
                  {a.name}
                </option>
              ))}
            </select>
            <input
              value={schedule}
              onChange={(e) => setSchedule(e.target.value)}
              placeholder="when — e.g. every weekday at 8:00am"
              aria-label="Schedule"
              className="rounded-xl bg-bg px-3 py-2 text-sm text-text-primary outline-none"
            />
          </div>
          <textarea
            value={instruction}
            onChange={(e) => setInstruction(e.target.value)}
            rows={2}
            placeholder="what to do — e.g. Summarise overnight emails and post the brief"
            aria-label="Instruction"
            className="mt-2 w-full resize-none rounded-xl bg-bg px-3 py-2 text-sm text-text-primary outline-none"
          />
          <div className="mt-2 flex justify-end">
            <button
              type="button"
              onClick={create}
              className="rounded-full bg-ink px-4 py-1.5 text-sm font-medium text-ink-contrast"
            >
              Create routine
            </button>
          </div>
        </div>

        {error && <p className="text-sm text-recording">{error}</p>}
        {routines.length === 0 && !error && (
          <p className="text-sm text-text-secondary">No routines yet.</p>
        )}

        <ul className="space-y-2" data-testid="routine-list">
          {routines.map((r) => (
            <li
              key={`${r.agent}-${r.id}`}
              className="flex items-start justify-between gap-3 rounded-card bg-surface-2 p-3"
            >
              <div className="min-w-0">
                <p className="text-[15px] font-medium text-text-primary">
                  {r.instruction}
                </p>
                <p className="text-[13px] text-text-secondary">
                  {nameOf(r.agent)} · {r.schedule}
                  {!r.enabled && " · paused"}
                </p>
              </div>
              <div className="flex shrink-0 gap-2">
                <button
                  type="button"
                  onClick={() => toggle(r)}
                  className="rounded-full bg-bg px-3 py-1.5 text-sm font-medium text-text-primary"
                >
                  {r.enabled ? "Pause" : "Enable"}
                </button>
                <button
                  type="button"
                  onClick={() => remove(r)}
                  aria-label={`Delete routine ${r.id}`}
                  className="rounded-full bg-bg px-3 py-1.5 text-sm font-medium text-text-secondary"
                >
                  Delete
                </button>
              </div>
            </li>
          ))}
        </ul>
      </div>
    </section>
  );
}
