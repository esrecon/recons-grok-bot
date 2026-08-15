import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor, fireEvent, within } from "@testing-library/react";
import type { Agent } from "../types";

// Mock the API module so the shell can be tested without a backend.
const listAgents = vi.fn();
const createAgent = vi.fn();
const setupFn = vi.fn();
vi.mock("../api", () => ({
  api: {
    listAgents: () => listAgents(),
    createAgent: (input: unknown) => createAgent(input),
    setup: () => setupFn(),
    setStatus: vi.fn(),
    deleteAgent: vi.fn(),
    streamChat: vi.fn(),
    audit: vi.fn().mockResolvedValue({ events: [], count: 0 }),
    auditExportUrl: () => "/api/audit/export.jsonl",
    skills: vi.fn().mockResolvedValue({ shared: [], pending: [] }),
    routines: vi.fn().mockResolvedValue({ routines: [] }),
  },
}));

import { App } from "../App";

const RECON: Agent = {
  id: "recon",
  name: "Recon",
  role: "Lead assistant",
  tier: "lead",
  avatar_color: "#8b5cf6",
  status: "running",
  is_lead: true,
  created_at: "2026-08-15T12:00:00+00:00",
};

describe("App shell", () => {
  beforeEach(() => {
    listAgents.mockReset();
    createAgent.mockReset();
    // Setup already complete, so these tests exercise the normal UI.
    setupFn.mockReset().mockResolvedValue({
      providers: [],
      has_provider: true,
      has_agents: true,
      complete: true,
    });
  });

  it("renders the roster from the API", async () => {
    listAgents.mockResolvedValue([RECON]);
    render(<App />);
    // Scope to the roster nav; the name also appears in the chat header.
    const roster = await screen.findByLabelText("Agents");
    expect(within(roster).getByText("Recon")).toBeInTheDocument();
    expect(within(roster).getByText("Lead assistant")).toBeInTheDocument();
    // Lead badge shows.
    expect(within(roster).getByText("lead")).toBeInTheDocument();
  });

  it("shows an empty state when there are no agents", async () => {
    listAgents.mockResolvedValue([]);
    render(<App />);
    expect(
      await screen.findByText(/hire your first teammate/i),
    ).toBeInTheDocument();
  });

  it("creates an agent through the one-click modal", async () => {
    listAgents.mockResolvedValueOnce([]); // initial load
    createAgent.mockResolvedValue({ ...RECON, id: "scout", name: "Scout" });
    listAgents.mockResolvedValueOnce([{ ...RECON, id: "scout", name: "Scout", role: "Research" }]);

    render(<App />);
    // Open modal via the sidebar + button (aria-label "New agent").
    fireEvent.click(await screen.findByLabelText("New agent"));

    fireEvent.change(screen.getByPlaceholderText("e.g. Scout"), {
      target: { value: "Scout" },
    });
    fireEvent.change(
      screen.getByPlaceholderText(/Researches suppliers/i),
      { target: { value: "Research" } },
    );
    fireEvent.click(screen.getByRole("button", { name: /create agent/i }));

    await waitFor(() =>
      expect(createAgent).toHaveBeenCalledWith(
        expect.objectContaining({ name: "Scout", role: "Research", tier: "workhorse" }),
      ),
    );
  });

  it("navigates to the audit surface", async () => {
    listAgents.mockResolvedValue([RECON]);
    render(<App />);
    fireEvent.click(await screen.findByText("Audit log"));
    expect(
      await screen.findByText(/every message, tool call and agent-to-agent/i),
    ).toBeInTheDocument();
  });
});
