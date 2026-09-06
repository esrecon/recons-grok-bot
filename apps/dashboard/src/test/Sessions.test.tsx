import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import type { Agent } from "../types";

const sessions = vi.fn();
const sessionDetail = vi.fn();
vi.mock("../api", () => ({
  api: {
    sessions: (agent?: string) => sessions(agent),
    sessionDetail: (a: string, id: string) => sessionDetail(a, id),
  },
}));

import { SessionsView } from "../views/SessionsView";

const AGENTS: Agent[] = [
  { id: "recon", name: "Recon", role: "Lead", tier: "lead", avatar_color: "#8b5cf6", status: "running", is_lead: true, created_at: "" },
  { id: "scout", name: "Scout", role: "Research", tier: "workhorse", avatar_color: "#3b82f6", status: "running", is_lead: false, created_at: "" },
];

const LIST = {
  sessions: [
    { agent_id: "recon", session_id: "recon-sess-1", session_key: "agent:recon:main", started_ts: 1, last_ts: 5,
      started_at: "2026-08-15T12:00:01+00:00", last_at: "2026-08-15T12:00:05+00:00", message_count: 3, tool_calls: 1,
      preview: "Find three suppliers" },
    { agent_id: "scout", session_id: "scout-sess-1", session_key: "agent:scout:main", started_ts: 1, last_ts: 4,
      started_at: "2026-08-15T12:00:01+00:00", last_at: "2026-08-15T12:00:04+00:00", message_count: 2, tool_calls: 0,
      preview: "Price these" },
  ],
};

describe("SessionsView", () => {
  beforeEach(() => {
    sessions.mockReset().mockResolvedValue(LIST);
    sessionDetail.mockReset().mockResolvedValue({
      session: LIST.sessions[0],
      events: [
        { ts: 1, ts_iso: "2026-08-15T12:00:01Z", seq: 0, source: "session", agent_id: "recon", kind: "message", role: "user", text: "Find three suppliers", session_id: "recon-sess-1" },
        { ts: 2, ts_iso: "2026-08-15T12:00:02Z", seq: 1, source: "session", agent_id: "recon", kind: "message", role: "assistant", text: "Searching now.", session_id: "recon-sess-1" },
        { ts: 2, ts_iso: "2026-08-15T12:00:02Z", seq: 1, source: "session", agent_id: "recon", kind: "tool_call", role: "assistant", text: "browser_navigate", session_id: "recon-sess-1", extra: { tool: "browser_navigate" } },
        { ts: 3, ts_iso: "2026-08-15T12:00:03Z", seq: 2, source: "session", agent_id: "recon", kind: "tool_result", role: "tool", text: "OK 200", session_id: "recon-sess-1", extra: { tool: "browser_navigate" } },
      ],
    });
  });

  it("lists sessions across agents with previews and counts", async () => {
    render(<SessionsView agents={AGENTS} />);
    expect(await screen.findByText("Find three suppliers")).toBeInTheDocument();
    expect(screen.getByText("Price these")).toBeInTheDocument();
    expect(screen.getByText(/3 messages/)).toBeInTheDocument();
  });

  it("filters by agent", async () => {
    render(<SessionsView agents={AGENTS} />);
    await screen.findByText("Find three suppliers");
    fireEvent.change(screen.getByLabelText(/filter by agent/i), { target: { value: "scout" } });
    await waitFor(() => expect(sessions).toHaveBeenLastCalledWith("scout"));
  });

  it("opens a transcript with bubbles and tool activity", async () => {
    render(<SessionsView agents={AGENTS} />);
    fireEvent.click(await screen.findByText("Find three suppliers"));
    await waitFor(() => expect(sessionDetail).toHaveBeenCalledWith("recon", "recon-sess-1"));
    const transcript = await screen.findByTestId("transcript");
    expect(transcript.querySelector('[data-role="user"]')).not.toBeNull();
    expect(transcript.querySelector('[data-role="assistant"]')).not.toBeNull();
    expect(screen.getByText("Searching now.")).toBeInTheDocument();
    expect(screen.getByText("browser_navigate")).toBeInTheDocument();
    expect(screen.getByText("agent:recon:main")).toBeInTheDocument();
  });
});
