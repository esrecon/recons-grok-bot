import { useCallback, useEffect, useState } from "react";
import type { Agent, NewAgentInput, SetupStatus } from "./types";
import type { View } from "./types-view";
import { api } from "./api";
import { applyTheme, loadTheme, type ThemeMode } from "./theme";
import { Sidebar } from "./components/Sidebar";
import { ChatView } from "./components/ChatView";
import { NewAgentModal } from "./components/NewAgentModal";
import { AuditView } from "./views/AuditView";
import { SkillsView } from "./views/SkillsView";
import { RoutinesView } from "./views/RoutinesView";
import { SettingsView } from "./views/SettingsView";
import { SetupWizard } from "./views/SetupWizard";

export function App() {
  const [agents, setAgents] = useState<Agent[]>([]);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [view, setView] = useState<View>("chats");
  const [modalOpen, setModalOpen] = useState(false);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [theme, setTheme] = useState<ThemeMode>("light");
  const [setup, setSetup] = useState<SetupStatus | null>(null);
  // Dismissing lets you reach the normal UI mid-setup; it returns next load.
  const [wizardDismissed, setWizardDismissed] = useState(false);
  // On phones the app is a single-pane messenger: the roster is home, and
  // opening an agent or a surface pushes it over the top with a back button.
  // On md+ both panes show side by side, as on desktop.
  const [mobileDetail, setMobileDetail] = useState(false);

  useEffect(() => {
    const t = loadTheme();
    setTheme(t);
    applyTheme(t);
  }, []);

  const refresh = useCallback(async () => {
    try {
      const [list, setupStatus] = await Promise.all([api.listAgents(), api.setup()]);
      setAgents(list);
      setSetup(setupStatus);
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
    setMobileDetail(true);
  }

  function toggleTheme() {
    const next: ThemeMode = theme === "dark" ? "light" : "dark";
    setTheme(next);
    applyTheme(next);
  }

  const selected = agents.find((a) => a.id === selectedId) ?? null;

  // Until there's a provider and an agent, setup is the whole app — there is
  // nothing useful to show behind it, and no reason to send anyone to a terminal.
  if (setup && !setup.complete && !wizardDismissed) {
    return (
      <div className="flex h-full w-full flex-col overflow-hidden">
        <SetupWizard
          status={setup}
          onChanged={refresh}
          onNewAgent={() => setModalOpen(true)}
        />
        {setup.has_provider && (
          <button
            type="button"
            onClick={() => setWizardDismissed(true)}
            className="border-t border-hairline py-2 text-sm text-text-secondary"
          >
            Skip for now
          </button>
        )}
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

  return (
    <div className="flex h-full w-full overflow-hidden">
      <Sidebar
        className={mobileDetail ? "hidden md:flex w-full" : "flex w-full"}
        agents={agents}
        selectedId={selectedId}
        view={view}
        onSelectAgent={(id) => {
          setSelectedId(id);
          setView("chats");
          setMobileDetail(true);
        }}
        onNewAgent={() => setModalOpen(true)}
        onNavigate={(v) => {
          setView(v);
          setMobileDetail(true);
        }}
        theme={theme}
        onToggleTheme={toggleTheme}
      />

      <main
        className={`${mobileDetail ? "flex" : "hidden md:flex"} h-full flex-1 flex-col`}
      >
        {/* Phone-only back bar returning to the roster. */}
        <button
          type="button"
          onClick={() => setMobileDetail(false)}
          className="flex items-center gap-1 border-b border-hairline px-3 py-2 text-left text-sm font-medium text-text-secondary md:hidden"
        >
          <span aria-hidden>‹</span> Agents
        </button>

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

        {view === "skills" && <SkillsView agents={agents} />}
        {view === "routines" && <RoutinesView agents={agents} />}
        {view === "audit" && <AuditView agents={agents} />}
        {view === "settings" && (
          <SettingsView providers={setup?.providers ?? []} agents={agents} onChanged={refresh} />
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
