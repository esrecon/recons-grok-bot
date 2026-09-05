import type { Agent } from "../types";
import { BotAvatarWithStatus } from "./BotAvatar";
import type { View } from "../types-view";

// iMessage-style roster sidebar. Bottom holds the secondary surfaces (Skills,
// Routines, Audit, Settings) the way Grok Bot parks "Plugins" + the user there.
export function Sidebar({
  agents,
  selectedId,
  view,
  onSelectAgent,
  onNewAgent,
  onNavigate,
  theme,
  onToggleTheme,
  className = "",
}: {
  agents: Agent[];
  selectedId: string | null;
  view: View;
  onSelectAgent: (id: string) => void;
  onNewAgent: () => void;
  onNavigate: (v: View) => void;
  theme: "light" | "dark";
  onToggleTheme: () => void;
  className?: string;
}) {
  return (
    <aside
      className={`flex h-full flex-col border-r border-hairline bg-surface md:w-[280px] md:shrink-0 ${className}`}
    >
      <header className="flex items-center justify-between px-4 py-3">
        <span className="text-[17px] font-semibold text-text-primary">Recons</span>
        <div className="flex items-center gap-1">
          <button
            type="button"
            aria-label="Toggle theme"
            onClick={onToggleTheme}
            className="grid h-8 w-8 place-items-center rounded-full text-text-secondary hover:bg-surface-2"
          >
            {theme === "dark" ? "☀" : "☾"}
          </button>
          <button
            type="button"
            aria-label="New agent"
            onClick={onNewAgent}
            className="grid h-8 w-8 place-items-center rounded-full bg-ink text-lg text-ink-contrast"
          >
            +
          </button>
        </div>
      </header>

      <nav className="flex-1 overflow-y-auto px-2" aria-label="Agents">
        {agents.length === 0 && (
          <p className="px-3 py-6 text-sm text-text-secondary">
            No agents yet. Tap <span className="font-semibold">+</span> to hire your
            first teammate.
          </p>
        )}
        {agents.map((a) => {
          const active = view === "chats" && a.id === selectedId;
          return (
            <button
              key={a.id}
              type="button"
              onClick={() => onSelectAgent(a.id)}
              className={`flex w-full items-center gap-3 rounded-xl px-2.5 py-2 text-left ${
                active ? "bg-surface-2" : "hover:bg-surface-2/60"
              }`}
            >
              <BotAvatarWithStatus
                id={a.id}
                color={a.avatar_color}
                status={a.status}
                title={a.name}
                size={38}
              />
              <span className="min-w-0 flex-1">
                <span className="flex items-center gap-1.5">
                  <span className="truncate text-[15px] font-semibold text-text-primary">
                    {a.name}
                  </span>
                  {a.is_lead && (
                    <span className="rounded-full bg-surface-2 px-1.5 text-[10px] font-medium uppercase text-text-secondary">
                      lead
                    </span>
                  )}
                </span>
                <span className="block truncate text-[13px] text-text-secondary">
                  {a.role}
                </span>
              </span>
            </button>
          );
        })}
      </nav>

      <footer className="border-t border-hairline p-2">
        <SideNavItem label="Sessions" icon="≡" active={view === "sessions"} onClick={() => onNavigate("sessions")} />
        <SideNavItem label="Skills" icon="◇" active={view === "skills"} onClick={() => onNavigate("skills")} />
        <SideNavItem label="Routines" icon="◷" active={view === "routines"} onClick={() => onNavigate("routines")} />
        <SideNavItem label="Audit log" icon="▦" active={view === "audit"} onClick={() => onNavigate("audit")} />
        <SideNavItem label="Settings" icon="⚙" active={view === "settings"} onClick={() => onNavigate("settings")} />
      </footer>
    </aside>
  );
}

function SideNavItem({
  label,
  icon,
  active,
  onClick,
}: {
  label: string;
  icon: string;
  active: boolean;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={`flex w-full items-center gap-3 rounded-xl px-3 py-2 text-left text-[14px] ${
        active
          ? "bg-surface-2 font-medium text-text-primary"
          : "text-text-secondary hover:bg-surface-2/60"
      }`}
    >
      <span aria-hidden className="w-4 text-center">
        {icon}
      </span>
      {label}
    </button>
  );
}
