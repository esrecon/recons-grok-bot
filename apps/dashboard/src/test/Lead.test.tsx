import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor, fireEvent, within } from "@testing-library/react";
import type { Agent } from "../types";

// Mock the API module so the head-of-staff flow runs without a backend.
const listAgents = vi.fn();
const setupFn = vi.fn();
const makeLead = vi.fn();
vi.mock("../api", () => ({
  api: {
    session: () => Promise.resolve({
      authenticated: true, operator: "tony", via: "password", csrf_token: "t",
      mode: "password", configured: true, reason: null,
    }),
    logout: vi.fn().mockResolvedValue(undefined),
    sessions: vi.fn().mockResolvedValue({ sessions: [] }),
    credentialStatus: vi.fn().mockResolvedValue({ providers: [], integrations: {}, restart_required: false }),
    securityPosture: vi.fn().mockResolvedValue({
      mode: "password", configured: true, operator: "tony", via: "password", cookie_secure: true,
      hsts: false, session_ttl_seconds: 43200, csrf_protection: true, allowed_origins: [],
      rate_limits: { login_per_minute: 10, api_per_minute: 600 },
    }),
    services: vi.fn().mockResolvedValue({ services: [] }),
    imageSettings: vi.fn().mockResolvedValue({ key_set: false, base_url: "", model: "" }),
    getTelegram: vi.fn().mockResolvedValue({ enabled: false, allowed_users: "", token_set: false, imported: false }),
    listAgents: () => listAgents(),
    setup: () => setupFn(),
    makeLead: (id: string) => makeLead(id),
    createAgent: vi.fn(),
    setStatus: vi.fn(),
    deleteAgent: vi.fn(),
    streamChat: vi.fn(),
    history: vi.fn().mockResolvedValue({ messages: [] }),
    audit: vi.fn().mockResolvedValue({ events: [], count: 0 }),
    auditExportUrl: () => "/api/audit/export.jsonl",
    skills: vi.fn().mockResolvedValue({ shared: [], pending: [] }),
    importCandidates: vi.fn().mockResolvedValue({ candidates: [] }),
    importAgent: vi.fn(),
    routines: vi.fn().mockResolvedValue({ routines: [] }),
  },
  onAuthChange: () => () => {},
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

const SCOUT: Agent = {
  id: "scout",
  name: "Scout",
  role: "Research",
  tier: "workhorse",
  avatar_color: "#3b82f6",
  status: "running",
  is_lead: false,
  created_at: "2026-08-15T12:00:00+00:00",
};

describe("head of staff", () => {
  beforeEach(() => {
    listAgents.mockReset();
    makeLead.mockReset();
    setupFn.mockReset().mockResolvedValue({
      providers: [],
      has_provider: true,
      has_agents: true,
      complete: true,
    });
  });

  it("shows the pill on the lead and promotes another agent after confirm", async () => {
    listAgents.mockResolvedValueOnce([RECON, SCOUT]);
    makeLead.mockResolvedValue({ ...SCOUT, is_lead: true });
    listAgents.mockResolvedValueOnce([
      { ...RECON, is_lead: false },
      { ...SCOUT, is_lead: true },
    ]);
    vi.spyOn(window, "confirm").mockReturnValue(true);

    render(<App />);
    // The initially selected agent (Recon) is the lead: pill, no button.
    expect(await screen.findByText("head of staff")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /make head of staff/i })).toBeNull();

    // Open Scout and promote it.
    const roster = screen.getByLabelText("Agents");
    fireEvent.click(within(roster).getByText("Scout"));
    fireEvent.click(await screen.findByRole("button", { name: /make head of staff/i }));

    await waitFor(() => expect(makeLead).toHaveBeenCalledWith("scout"));
    // The roster refreshes and the promoted agent now carries the pill.
    await waitFor(() => expect(listAgents).toHaveBeenCalledTimes(2));
    expect(await screen.findByText("head of staff")).toBeInTheDocument();
  });

  it("does nothing when the confirm is dismissed", async () => {
    listAgents.mockResolvedValue([RECON, SCOUT]);
    vi.spyOn(window, "confirm").mockReturnValue(false);

    render(<App />);
    const roster = await screen.findByLabelText("Agents");
    fireEvent.click(within(roster).getByText("Scout"));
    fireEvent.click(await screen.findByRole("button", { name: /make head of staff/i }));

    expect(makeLead).not.toHaveBeenCalled();
  });
});
