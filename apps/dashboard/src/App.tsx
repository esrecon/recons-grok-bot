import { useCallback, useEffect, useState } from "react";
import type { Agent, NewAgentInput } from "./types";
import type { View } from "./types-view";
import { api } from "./api";
import { applyTheme, loadTheme, type ThemeMode } from "./theme";
import { Sidebar } from "./components/Sidebar";
import { ChatView } from "./components/ChatView";
import { NewAgentModal } from "./components/NewAgentModal";
import { Placeholder } from "./views/Placeholder";

export function App() {
  const [agents, setAgents] = useState<Agent[]>([]);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [view, setView] = useState<View>("chats");
  const [modalOpen, setModalOpen] = useState(false);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [theme, setTheme] = useState<ThemeMode>("light");

  useEffect(() => {
    const t = loadTheme();
    setTheme(t);
    applyTheme(t);
  }, []);

  const refresh = useCallback(async () => {
    try {
      const list = await api.listAgents();
      setAgents(list);
      setLoadError(null);
      setSelectedId((cur) => cur ?? list[0]?.id ?? null);
    } catch (e) {
      setLoadError(e instanceof Error ? e.message : "Could not load agents.");
    }
  }, []);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  async function createAgent(input: NewAgentInput) {
    const created = await api.createAgent(input);
    await refresh();
    setSelectedId(created.id);
    setView("chats");
  }

  function toggleTheme() {
    const next: ThemeMode = theme === "dark" ? "light" : "dark";
    setTheme(next);
    applyTheme(next);
  }

  const selected = agents.find((a) => a.id === selectedId) ?? null;

  return (
    <div className="flex h-full w-full overflow-hidden">
      <Sidebar
        agents={agents}
        selectedId={selectedId}
        view={view}
        onSelectAgent={(id) => {
          setSelectedId(id);
          setView("chats");
        }}
        onNewAgent={() => setModalOpen(true)}
        onNavigate={setView}
        theme={theme}
        onToggleTheme={toggleTheme}
      />

      <main className="flex h-full flex-1 flex-col">
        {view === "chats" &&
          (selected ? (
            <ChatView agent={selected} />
          ) : (
            <div className="grid h-full place-items-center bg-bg p-6 text-center">
              <div>
                <p className="text-[15px] font-semibold text-text-primary">
                  Your team of always-on agents
                </p>
                <p className="mt-1 text-sm text-text-secondary">
                  {loadError
                    ? `Couldn't reach the orchestrator: ${loadError}`
                    : "Create your first agent to get started."}
                </p>
                <button
                  type="button"
                  onClick={() => setModalOpen(true)}
                  className="mt-4 rounded-full bg-ink px-5 py-2 text-sm font-medium text-ink-contrast"
                >
                  New agent
                </button>
              </div>
            </div>
          ))}

        {view === "skills" && (
          <Placeholder
            title="Skills"
            blurb="The shared skill library and the teach-mode approval queue land in Phase 5. Skills taught to one agent are usable by all."
          />
        )}
        {view === "routines" && (
          <Placeholder
            title="Routines"
            blurb="Scheduled and event-triggered automations per agent land in Phase 5, backed by Hermes cron."
          />
        )}
        {view === "audit" && (
          <Placeholder
            title="Audit log"
            blurb="The merged, filterable transcript of every agent conversation and agent-to-agent message lands in Phase 4 — the feature Grok Bot itself doesn't ship."
          />
        )}
        {view === "settings" && (
          <Placeholder
            title="Settings"
            blurb="Provider/key status and node health (your PC, phone, and peers) land alongside the deployment kit."
          />
        )}
      </main>

      {modalOpen && (
        <NewAgentModal
          existing={agents}
          onClose={() => setModalOpen(false)}
          onCreate={createAgent}
        />
      )}
    </div>
  );
}
