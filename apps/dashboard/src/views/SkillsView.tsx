import { useCallback, useEffect, useState } from "react";
import type { Agent, Skill, SkillDetail, SkillFileContent } from "../types";
import { api } from "../api";
import { StatusPill } from "../components/StatusPill";

// Shared skill library + the teach-mode approval queue. A skill approved here
// moves into the library every agent shares; agent-authored drafts always wait
// for a human here (nothing self-installs). Every card can be inspected
// read-only before deciding.
export function SkillsView({ agents }: { agents: Agent[] }) {
  const [shared, setShared] = useState<Skill[]>([]);
  const [pending, setPending] = useState<Skill[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [inspecting, setInspecting] = useState<Skill | null>(null);
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
    setInspecting(null);
    await load();
  }
  async function reject(s: Skill) {
    if (!s.agent) return;
    await api.rejectSkill(s.agent, s.slug);
    setInspecting(null);
    await load();
  }

  return (
    <section className="flex h-full flex-1 flex-col bg-bg">
      <header className="border-b border-hairline px-4 py-3">
        <h1 className="text-[17px] font-semibold text-text-primary">Skills</h1>
        <p className="mt-0.5 text-[13px] text-text-secondary">
          Taught once, shared by every agent. Invoke in chat with “/”. Inspect before you approve.
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
                    <div className="flex shrink-0 flex-wrap justify-end gap-2">
                      <button
                        type="button"
                        aria-label={`Inspect ${s.name}`}
                        onClick={() => setInspecting(s)}
                        className="rounded-full bg-bg px-3 py-1.5 text-sm font-medium text-text-primary"
                      >
                        Inspect
                      </button>
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
              <div className="flex items-start justify-between gap-2">
                <div className="min-w-0">
                  <p className="text-[15px] font-medium text-text-primary">
                    {s.name}
                    {s.version && (
                      <span className="ml-2 text-[11px] text-text-secondary">
                        v{s.version}
                      </span>
                    )}
                  </p>
                  <p className="text-[13px] text-text-secondary">{s.description}</p>
                </div>
                <button
                  type="button"
                  aria-label={`Inspect ${s.name}`}
                  onClick={() => setInspecting(s)}
                  className="shrink-0 rounded-full bg-bg px-3 py-1 text-[13px] font-medium text-text-primary"
                >
                  Inspect
                </button>
              </div>
            </li>
          ))}
        </ul>
      </div>

      {inspecting && (
        <SkillInspector
          skill={inspecting}
          taughtBy={inspecting.agent ? nameOf(inspecting.agent) : null}
          onClose={() => setInspecting(null)}
          onApprove={inspecting.source === "pending" ? () => approve(inspecting) : undefined}
          onReject={inspecting.source === "pending" ? () => reject(inspecting) : undefined}
        />
      )}
    </section>
  );
}

// Read-only inspector: frontmatter, review warnings, the SKILL.md body as
// plain text (never rendered), and every file in the folder on demand.
function SkillInspector({
  skill,
  taughtBy,
  onClose,
  onApprove,
  onReject,
}: {
  skill: Skill;
  taughtBy: string | null;
  onClose: () => void;
  onApprove?: () => Promise<void>;
  onReject?: () => Promise<void>;
}) {
  const [detail, setDetail] = useState<SkillDetail | null>(null);
  const [file, setFile] = useState<SkillFileContent | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    let live = true;
    setDetail(null);
    setFile(null);
    api
      .skillDetail(skill.source, skill.slug, skill.agent ?? undefined)
      .then((d) => live && setDetail(d))
      .catch((e) => live && setError(e instanceof Error ? e.message : "Could not load skill"));
    return () => {
      live = false;
    };
  }, [skill]);

  async function openFile(path: string) {
    if (path === "SKILL.md") {
      setFile(null);
      return;
    }
    try {
      setFile(await api.skillFile(skill.source, skill.slug, path, skill.agent ?? undefined));
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not load file");
    }
  }

  async function run(fn?: () => Promise<void>) {
    if (!fn) return;
    setBusy(true);
    try {
      await fn();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Action failed");
      setBusy(false);
    }
  }

  const fm = detail?.frontmatter ?? {};

  return (
    <div
      className="fixed inset-0 z-40 flex justify-end bg-black/40"
      onClick={onClose}
      role="presentation"
    >
      <div
        role="dialog"
        aria-modal="true"
        aria-label={`${skill.name} — skill inspector`}
        onClick={(e) => e.stopPropagation()}
        className="flex h-full w-full max-w-2xl flex-col bg-bg shadow-xl"
      >
        <header className="flex items-start justify-between gap-3 border-b border-hairline px-4 py-3">
          <div className="min-w-0">
            <h2 className="text-[17px] font-semibold text-text-primary">
              {skill.name}
              {skill.version && (
                <span className="ml-2 text-[12px] text-text-secondary">v{skill.version}</span>
              )}
            </h2>
            <p className="text-[13px] text-text-secondary">
              {skill.source === "pending" ? `Pending · taught by ${taughtBy ?? skill.agent}` : "Shared library"}
              {" · "}
              <span className="font-mono">{skill.slug}</span>
            </p>
          </div>
          <button
            type="button"
            aria-label="Close inspector"
            onClick={onClose}
            className="grid h-8 w-8 shrink-0 place-items-center rounded-full text-text-secondary hover:bg-surface-2"
          >
            ✕
          </button>
        </header>

        <div className="flex-1 space-y-4 overflow-y-auto px-4 py-4">
          {error && <p className="text-sm text-recording">{error}</p>}
          {!detail && !error && <p className="text-sm text-text-secondary">Loading…</p>}

          {detail && (
            <>
              <div>
                <h3 className="mb-1 text-sm font-semibold text-text-primary">Review</h3>
                {detail.warnings.length === 0 ? (
                  <p className="text-[13px] text-text-secondary">
                    No concerns found — still read it before approving.
                  </p>
                ) : (
                  <ul className="space-y-1">
                    {detail.warnings.map((w) => (
                      <li key={w} className="rounded-xl bg-amber-bg px-3 py-1.5 text-[13px] text-amber">
                        {w}
                      </li>
                    ))}
                  </ul>
                )}
              </div>

              {Object.keys(fm).length > 0 && (
                <div>
                  <h3 className="mb-1 text-sm font-semibold text-text-primary">Frontmatter</h3>
                  <table className="w-full text-[13px]">
                    <tbody>
                      {Object.entries(fm).map(([k, v]) => (
                        <tr key={k} className="border-t border-hairline">
                          <th className="w-32 py-1 pr-2 text-left font-medium text-text-secondary">{k}</th>
                          <td className="py-1 text-text-primary">
                            {typeof v === "string" ? v : JSON.stringify(v)}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}

              <div>
                <h3 className="mb-1 text-sm font-semibold text-text-primary">Files</h3>
                <ul className="flex flex-wrap gap-1.5">
                  {detail.files.map((f) => (
                    <li key={f.path}>
                      <button
                        type="button"
                        onClick={() => openFile(f.path)}
                        className={`rounded-full px-2.5 py-1 font-mono text-[12px] ${
                          (file?.path ?? "SKILL.md") === f.path
                            ? "bg-ink text-ink-contrast"
                            : "bg-surface-2 text-text-primary"
                        }`}
                      >
                        {f.path}
                      </button>
                      {f.kind === "script" && (
                        <span className="ml-1 align-middle">
                          <StatusPill tone="warn">script</StatusPill>
                        </span>
                      )}
                      {f.kind === "binary" && (
                        <span className="ml-1 align-middle">
                          <StatusPill tone="muted">binary</StatusPill>
                        </span>
                      )}
                    </li>
                  ))}
                </ul>
              </div>

              <div>
                <h3 className="mb-1 text-sm font-semibold text-text-primary">
                  {file ? file.path : "SKILL.md"}
                  {(file ? file.truncated : detail.truncated) && (
                    <span className="ml-2 text-[12px] font-normal text-text-secondary">(truncated)</span>
                  )}
                </h3>
                <pre className="overflow-x-auto whitespace-pre-wrap break-words rounded-xl bg-surface-2 p-3 text-[12.5px] leading-relaxed text-text-primary">
                  {file ? file.text : detail.body}
                </pre>
              </div>
            </>
          )}
        </div>

        {(onApprove || onReject) && (
          <footer className="flex justify-end gap-2 border-t border-hairline px-4 py-3">
            {onReject && (
              <button
                type="button"
                disabled={busy}
                onClick={() => run(onReject)}
                className="rounded-full bg-surface-2 px-4 py-1.5 text-sm font-medium text-text-primary disabled:opacity-50"
              >
                Reject
              </button>
            )}
            {onApprove && (
              <button
                type="button"
                disabled={busy}
                onClick={() => run(onApprove)}
                className="rounded-full bg-ink px-4 py-1.5 text-sm font-medium text-ink-contrast disabled:opacity-50"
              >
                Approve
              </button>
            )}
          </footer>
        )}
      </div>
    </div>
  );
}
