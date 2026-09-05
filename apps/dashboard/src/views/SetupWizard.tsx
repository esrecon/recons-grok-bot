import type { SetupStatus } from "../types";
import { ProviderCard } from "../components/ProviderCard";
import { BotAvatar } from "../components/BotAvatar";
import { ImportAgents } from "../components/ImportAgents";

// Shown until there's at least one provider and one agent. This is the whole
// setup story: connect a brain, hire a teammate. No terminal, no files.
export function SetupWizard({
  status,
  onChanged,
  onNewAgent,
}: {
  status: SetupStatus;
  onChanged: () => void;
  onNewAgent: () => void;
}) {
  return (
    <section className="flex h-full flex-1 flex-col overflow-y-auto bg-bg">
      <div className="mx-auto w-full max-w-2xl px-5 py-10">
        <div className="mb-8 flex items-center gap-4">
          <BotAvatar id="welcome" color="#8b5cf6" size={52} title="Recons" />
          <div>
            <h1 className="text-2xl font-semibold tracking-tight text-text-primary">
              Let’s get your team running
            </h1>
            <p className="mt-1 text-[15px] text-text-secondary">
              Two steps. Everything happens here — nothing to edit on the server.
            </p>
          </div>
        </div>

        <Step
          n={1}
          title="Connect a brain"
          done={status.has_provider}
          blurb="Your agents need at least one model provider. Nous is the cheapest way to start; your ChatGPT subscription is the everyday workhorse."
        >
          <div className="mt-3 space-y-3">
            {status.providers.map((p) => (
              <ProviderCard key={p.id} provider={p} onChanged={onChanged} />
            ))}
          </div>
        </Step>

        <Step
          n={2}
          title="Hire your first teammate"
          done={status.has_agents}
          blurb="Give it a name and a one-line job. It gets its own identity and memory, and shares skills and keys with everyone you add later."
          disabled={!status.has_provider}
        >
          <button
            type="button"
            onClick={onNewAgent}
            disabled={!status.has_provider}
            className="mt-3 rounded-full bg-ink px-5 py-2 text-sm font-medium text-ink-contrast disabled:opacity-40"
          >
            New agent
          </button>
          {!status.has_provider && (
            <p className="mt-2 text-[13px] text-text-secondary">
              Connect a provider first.
            </p>
          )}

          <div className="mt-5">
            <h3 className="mb-2 text-sm font-semibold text-text-primary">
              …or bring in agents you already have
            </h3>
            <ImportAgents usedColors={[]} onImported={onChanged} />
          </div>
        </Step>
      </div>
    </section>
  );
}

function Step({
  n,
  title,
  blurb,
  done,
  disabled,
  children,
}: {
  n: number;
  title: string;
  blurb: string;
  done: boolean;
  disabled?: boolean;
  children: React.ReactNode;
}) {
  return (
    <div className={`border-t border-hairline py-6 ${disabled ? "opacity-60" : ""}`}>
      <div className="flex items-center gap-3">
        <span
          className={`grid h-7 w-7 shrink-0 place-items-center rounded-full text-[13px] font-semibold ${
            done
              ? "bg-verified-bg text-verified"
              : "bg-surface-2 text-text-secondary"
          }`}
          aria-hidden
        >
          {done ? "✓" : n}
        </span>
        <h2 className="text-[17px] font-semibold text-text-primary">{title}</h2>
      </div>
      <p className="ml-10 mt-1 max-w-prose text-[14px] text-text-secondary">{blurb}</p>
      <div className="ml-10">{children}</div>
    </div>
  );
}
