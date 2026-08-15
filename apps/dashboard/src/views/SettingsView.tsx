import type { Provider } from "../types";
import { ProviderCard } from "../components/ProviderCard";

// Providers and their connection state, manageable at any time. Keys are
// write-only: you can replace or disconnect, never read one back.
export function SettingsView({
  providers,
  onChanged,
}: {
  providers: Provider[];
  onChanged: () => void;
}) {
  return (
    <section className="flex h-full flex-1 flex-col bg-bg">
      <header className="border-b border-hairline px-4 py-3">
        <h1 className="text-[17px] font-semibold text-text-primary">Settings</h1>
        <p className="mt-0.5 text-[13px] text-text-secondary">
          Model providers for your team. Keys are stored on the server and never
          shown again.
        </p>
      </header>

      <div className="flex-1 overflow-y-auto px-4 py-4">
        <h2 className="mb-2 text-sm font-semibold text-text-primary">Providers</h2>
        <div className="space-y-3">
          {providers.map((p) => (
            <ProviderCard key={p.id} provider={p} onChanged={onChanged} />
          ))}
        </div>

        <h2 className="mb-2 mt-7 text-sm font-semibold text-text-primary">
          Where things live
        </h2>
        <dl className="space-y-2 text-[13px] text-text-secondary">
          <Row term="Agents" desc="One folder each, with its own SOUL.md, memory and history." />
          <Row term="Skills" desc="One shared library — teach any agent, every agent gains it." />
          <Row term="Keys" desc="One shared file on the server, readable only by your agents." />
          <Row term="Audit log" desc="Merged from every agent's transcript, plus a signed live feed." />
        </dl>
      </div>
    </section>
  );
}

function Row({ term, desc }: { term: string; desc: string }) {
  return (
    <div className="flex gap-3">
      <dt className="w-24 shrink-0 font-medium text-text-primary">{term}</dt>
      <dd className="min-w-0">{desc}</dd>
    </div>
  );
}
