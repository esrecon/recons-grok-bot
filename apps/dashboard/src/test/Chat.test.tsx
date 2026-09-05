import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import type { Agent, ChatEvent } from "../types";

const streamChat = vi.fn();
const decideApproval = vi.fn();
const setStatus = vi.fn();
const deleteAgent = vi.fn();
vi.mock("../api", () => ({
  api: {
    streamChat: (id: string, text: string) => streamChat(id, text),
    decideApproval: (a: string, id: string, d: string) => decideApproval(a, id, d),
    setStatus: (id: string, action: string) => setStatus(id, action),
    deleteAgent: (id: string) => deleteAgent(id),
  },
}));

import { ChatView } from "../components/ChatView";

const SCOUT: Agent = {
  id: "scout", name: "Scout", role: "Research", tier: "workhorse", avatar_color: "#3b82f6",
  status: "running", is_lead: false, created_at: "",
};

async function* events(list: ChatEvent[]) {
  for (const e of list) yield e;
}

describe("ChatView", () => {
  beforeEach(() => {
    streamChat.mockReset();
    decideApproval.mockReset().mockResolvedValue(undefined);
    setStatus.mockReset().mockResolvedValue({ ...SCOUT, status: "paused" });
    deleteAgent.mockReset().mockResolvedValue(undefined);
  });

  it("forwards approval decisions to the orchestrator", async () => {
    streamChat.mockReturnValue(events([
      { type: "token", text: "Ready to send." },
      { type: "approval", id: "appr-1", title: "New email", body: "Hi…", kind: "send" },
      { type: "done" },
    ]));
    render(<ChatView agent={SCOUT} />);
    fireEvent.change(screen.getByLabelText(/message scout/i), { target: { value: "send it" } });
    fireEvent.click(screen.getByLabelText("Send"));
    fireEvent.click(await screen.findByRole("button", { name: "Approve" }));
    await waitFor(() => expect(decideApproval).toHaveBeenCalledWith("scout", "appr-1", "approve"));
    expect(await screen.findByText(/you approved the action/i)).toBeInTheDocument();
  });

  it("shows streamed errors instead of hanging", async () => {
    streamChat.mockReturnValue(events([
      { type: "error", message: "agent unreachable (ConnectError)" },
      { type: "done" },
    ]));
    render(<ChatView agent={SCOUT} />);
    fireEvent.change(screen.getByLabelText(/message scout/i), { target: { value: "hi" } });
    fireEvent.click(screen.getByLabelText("Send"));
    expect(await screen.findByText(/agent unreachable/)).toBeInTheDocument();
  });

  it("offers pause/resume and remove from the header menu", async () => {
    const onChanged = vi.fn();
    render(<ChatView agent={SCOUT} onChanged={onChanged} />);
    fireEvent.click(screen.getByLabelText(/agent menu/i));
    fireEvent.click(screen.getByRole("menuitem", { name: /pause/i }));
    await waitFor(() => expect(setStatus).toHaveBeenCalledWith("scout", "pause"));
    expect(onChanged).toHaveBeenCalled();
  });

  it("disables the composer while an agent is paused", () => {
    render(<ChatView agent={{ ...SCOUT, status: "paused" }} />);
    expect(screen.getByLabelText(/message scout/i)).toBeDisabled();
    expect(screen.getByText(/paused/i)).toBeInTheDocument();
  });
});
