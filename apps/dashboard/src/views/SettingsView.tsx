import type { Agent, SessionInfo } from "../types";

// Settings (fleshed out in the next step): operator + sign out.
export function SettingsView({
  session,
  onSignOut,
}: {
  agents: Agent[];
  session: SessionInfo;
  onSignOut: () => void;
}) {
  return (
    <section className="flex h-full flex-1 flex-col bg-bg">
      <header className="border-b border-hairline px-4 py-3">
        <h1 className="text-[17px] font-semibold text-text-primary">Settings</h1>
      </header>
      <div className="flex-1 overflow-y-auto px-4 py-4">
        <p className="text-sm text-text-primary">
          Signed in as <span className="font-medium">{session.operator}</span>
        </p>
        <button
          type="button"
          onClick={onSignOut}
          className="mt-3 rounded-full bg-surface-2 px-4 py-1.5 text-sm font-medium text-text-primary"
        >
          Sign out
        </button>
      </div>
    </section>
  );
}
