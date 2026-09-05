// Text-forward status chips. Colour is used sparingly (green ok, red bad,
// amber attention, grey neutral) in keeping with the monochrome chrome.
export type Tone = "ok" | "bad" | "warn" | "muted";

export function StatusPill({ tone, children }: { tone: Tone; children: React.ReactNode }) {
  const cls =
    tone === "ok"
      ? "bg-[#e6f6ec] text-[#1e7a41] dark:bg-[#173d27] dark:text-[#7fd6a0]"
      : tone === "bad"
        ? "bg-[#fdeaea] text-[#b3261e] dark:bg-[#4a1f1c] dark:text-[#f28b82]"
        : tone === "warn"
          ? "bg-amber-bg text-amber"
          : "bg-surface-2 text-text-secondary";
  return (
    <span className={`inline-flex items-center rounded-full px-2 py-0.5 text-[11px] font-medium ${cls}`}>
      {children}
    </span>
  );
}
