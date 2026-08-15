// Shared "coming in a later phase" surface so the shell navigation is complete
// and honest about what's wired vs. stubbed.
export function Placeholder({
  title,
  blurb,
}: {
  title: string;
  blurb: string;
}) {
  return (
    <section className="flex h-full flex-1 flex-col bg-bg">
      <header className="border-b border-hairline px-4 py-3">
        <h1 className="text-[17px] font-semibold text-text-primary">{title}</h1>
      </header>
      <div className="grid flex-1 place-items-center p-6 text-center">
        <p className="max-w-sm text-sm text-text-secondary">{blurb}</p>
      </div>
    </section>
  );
}
