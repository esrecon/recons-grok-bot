import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor, fireEvent } from "@testing-library/react";
import type { Agent, AuditEvent } from "../types";

const auditFn = vi.fn();
vi.mock("../api", () => ({
  api: {
    audit: (f: unknown) => auditFn(f),
    auditExportUrl: () => "/api/audit/export.jsonl",
  },
}));

import { AuditView } from "../views/AuditView";

const AGENTS: Agent[] = [
  { id: "recon", name: "Recon", role: "Lead", tier: "lead", avatar_color: "#8b5cf6", status: "running", is_lead: true, created_at: "" },
  { id: "scout", name: "Scout", role: "Research", tier: "workhorse", avatar_color: "#3b82f6", status: "running", is_lead: false, created_at: "" },
];

const EVENTS: AuditEvent[] = [
  { ts: 1, ts_iso: "2026-08-15T12:00:01Z", seq: 0, source: "session", agent_id: "recon", kind: "message", role: "user", text: "Find suppliers" },
  { ts: 3, ts_iso: "2026-08-15T12:00:03Z", seq: 1, source: "a2a", agent_id: "recon", kind: "a2a", peer_from: "recon", peer_to: "scout", text: "Price these", extra: { direction: "outbound" } },
];

describe("AuditView", () => {
  beforeEach(() => {
    auditFn.mockReset();
    auditFn.mockResolvedValue({ events: EVENTS, count: EVENTS.length });
  });

  it("renders merged events with agent-to-agent hand-offs labelled", async () => {
    render(<AuditView agents={AGENTS} />);
    expect(await screen.findByText("Find suppliers")).toBeInTheDocument();
    // A2A row shows "Recon → Scout".
    expect(screen.getByText("Recon → Scout")).toBeInTheDocument();
  });

  it("passes the agent-to-agent-only filter to the API", async () => {
    render(<AuditView agents={AGENTS} />);
    await screen.findByText("Find suppliers");
    fireEvent.click(screen.getByText("Agent-to-agent only"));
    await waitFor(() =>
      expect(auditFn).toHaveBeenLastCalledWith(
        expect.objectContaining({ a2a_only: true }),
      ),
    );
  });

  it("expands a row to show raw JSON", async () => {
    render(<AuditView agents={AGENTS} />);
    fireEvent.click(await screen.findByText("Find suppliers"));
    expect(await screen.findByText(/"kind": "message"/)).toBeInTheDocument();
  });
});
