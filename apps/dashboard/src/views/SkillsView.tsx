import { useCallback, useEffect, useState } from "react";
import type { Agent, Skill } from "../types";
import { api } from "../api";

// Shared skill library + the teach-mode approval queue. A skill approved here
// moves into the library every agent shares; agent-authored drafts always wait
// for a human here (nothing self-installs).
export function SkillsView({ agents }: { agents: Agent[] }) {
  const [shared, setShared] = useState<Skill[]>([]);
  const [pending, setPending] = useState<Skill[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const nameOf = (id?: string | null) =>
    agents.find((a) => a.id === id)?.name ?? id ?? "";

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const r = await api.skills();
      setShared(r.shared);
      setPending(r.pending);
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to load skills");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  async function approve(s: Skill) {
    if (!s.agent) return;
    await api.approveSkill(s.agent, s.slug);
    await load();
  }
  async function reject(s: Skill) {
    if (!s.agent) return;
    await api.rejectSkill(s.agent, s.slug);
    await load();
  }

  return (
    <section className="flex h-full flex-1 flex-col bg-bg">
      <header className="border-b border-hairline px-4 py-3">
        <h1 className="text-[17px] font-semibold text-text-primary">Skills</h1>
        <p className="mt-0.5 text-[13px] text-text-secondary">
          Taught once, shared by every agent. Invoke in chat with “/”.
        </p>
      </header>

      <div className="flex-1 overflow-y-auto px-4 py-4">
        {loading && <p className="text-sm text-text-secondary">Loading…</p>}
        {error && <p className="text-sm text-recording">{error}</p>}

        {pending.length > 0 && (
          <div className="mb-5" data-testid="pending-queue">
            <h2 className="mb-2 flex items-center gap-2 text-sm font-semibold text-text-primary">
              Needs your approval
              <span className="rounded-full bg-amber-bg px-2 py-0.5 text-[11px] font-medium text-amber">
                {pending.length}
              </span>
            </h2>
            <ul className="space-y-2">
              {pending.map((s) => (
                <li
                  key={`${s.agent}-${s.slug}`}
                  className="rounded-card bg-surface-2 p-3"
                >
                  <div className="flex items-start justify-between gap-3">
                    <div className="min-w-0">
                      <p className="text-[15px] font-medium text-text-primary">
                        {s.name}
                      </p>
                      <p className="text-[13px] text-text-secondary">
                        {s.description}
                      </p>
                      <p className="mt-1 text-[12px] text-text-secondary">
                        Taught by {nameOf(s.agent)}
                      </p>
                    </div>
                    <div className="flex shrink-0 gap-2">
                      <button
                        type="button"
                        onClick={() => approve(s)}
                        className="rounded-full bg-ink px-3 py-1.5 text-sm font-medium text-ink-contrast"
                      >
                        Approve
                      </button>
                      <button
                        type="button"
                        onClick={() => reject(s)}
                        className="rounded-full bg-bg px-3 py-1.5 text-sm font-medium text-text-primary"
                      >
                        Reject
                      </button>
                    </div>
                  </div>
                </li>
              ))}
            </ul>
          </div>
        )}

        <h2 className="mb-2 text-sm font-semibold text-text-primary">Library</h2>
        {!loading && shared.length === 0 && (
          <p className="text-sm text-text-secondary">
            No skills yet. Teach an agent a task, then approve it here to share it
            with the team.
          </p>
        )}
        <ul className="grid gap-2 sm:grid-cols-2">
          {shared.map((s) => (
            <li key={s.slug} className="rounded-card bg-surface-2 p-3">
              <p className="text-[15px] font-medium text-text-primary">
                {s.name}
                {s.version && (
                  <span className="ml-2 text-[11px] text-text-secondary">
                    v{s.version}
                  </span>
                )}
              </p>
              <p className="text-[13px] text-text-secondary">{s.description}</p>
            </li>
          ))}
        </ul>
      </div>
    </section>
  );
}
