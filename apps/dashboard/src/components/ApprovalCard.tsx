// In-chat approval card (Grok Bot renders these inline): outer gray card with a
// bold label, a nested white card with the draft, then black "Approve" + gray
// "Deny" pills. This is the human-in-the-loop gate for risky actions.
export function ApprovalCard({
  approval,
  onDecision,
}: {
  approval: { id: string; title: string; body: string; kind?: string };
  onDecision?: (id: string, decision: "approve" | "deny") => void;
}) {
  return (
    <div className="rounded-card bg-surface-2 p-3" data-testid="approval-card">
      <div className="mb-2 flex items-center gap-2">
        <span className="text-sm font-semibold text-text-primary">
          {approval.title}
        </span>
        {approval.kind && (
          <span className="rounded-full bg-amber-bg px-2 py-0.5 text-[11px] font-medium text-amber">
            needs approval
          </span>
        )}
      </div>
      <div className="rounded-xl bg-bg p-3 text-sm text-text-primary shadow-sm">
        <span className="whitespace-pre-wrap break-words">{approval.body}</span>
      </div>
      <div className="mt-3 flex gap-2">
        <button
          type="button"
          onClick={() => onDecision?.(approval.id, "approve")}
          className="rounded-full bg-ink px-4 py-1.5 text-sm font-medium text-ink-contrast active:opacity-80"
        >
          Approve
        </button>
        <button
          type="button"
          onClick={() => onDecision?.(approval.id, "deny")}
          className="rounded-full bg-surface px-4 py-1.5 text-sm font-medium text-text-primary active:opacity-80"
        >
          Deny
        </button>
      </div>
    </div>
  );
}
