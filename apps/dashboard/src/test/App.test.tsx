import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor, fireEvent, within } from "@testing-library/react";
import type { Agent } from "../types";

// Mock the API module so the shell can be tested without a backend.
const listAgents = vi.fn();
const createAgent = vi.fn();
const session = vi.fn();
const authListeners: ((s: "signed-out") => void)[] = [];
vi.mock("../api", () => ({
  api: {
    session: () => session(),
    login: vi.fn(),
    logout: vi.fn().mockResolvedValue(undefined),
    listAgents: () => listAgents(),
    createAgent: (input: unknown) => createAgent(input),
    setStatus: vi.fn(),
    deleteAgent: vi.fn(),
    streamChat: vi.fn(),
    audit: vi.fn().mockResolvedValue({ events: [], count: 0 }),
    auditExportUrl: () => "/api/audit/export.jsonl",
    skills: vi.fn().mockResolvedValue({ shared: [], pending: [] }),
    routines: vi.fn().mockResolvedValue({ routines: [] }),
    sessions: vi.fn().mockResolvedValue({ sessions: [] }),
    providers: vi.fn().mockResolvedValue({ providers: [], integrations: {}, restart_required: false }),
    services: vi.fn().mockResolvedValue({ services: [] }),
    securityPosture: vi.fn().mockResolvedValue({
      mode: "password", configured: true, operator: "tony", via: "password", cookie_secure: true,
      hsts: false, session_ttl_seconds: 43200, csrf_protection: true, allowed_origins: [],
      rate_limits: { login_per_minute: 10, api_per_minute: 600 },
    }),
  },
  onAuthChange: (fn: (s: "signed-out") => void) => {
    authListeners.push(fn);
    return () => {};
  },
}));

import { App } from "../App";

const SIGNED_IN = {
  authenticated: true, operator: "tony", via: "password", csrf_token: "t",
  mode: "password", configured: true, reason: null,
};
const SIGNED_OUT = { ...SIGNED_IN, authenticated: false, operator: null, csrf_token: null };

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
    session.mockReset().mockResolvedValue(SIGNED_IN);
    authListeners.length = 0;
  });

  it("shows the login screen when there is no operator session", async () => {
    session.mockResolvedValue(SIGNED_OUT);
    listAgents.mockResolvedValue([RECON]);
    render(<App />);
    expect(await screen.findByRole("button", { name: /sign in/i })).toBeInTheDocument();
    expect(screen.queryByLabelText("Agents")).not.toBeInTheDocument();
    // Nothing else is fetched until we are signed in.
    expect(listAgents).not.toHaveBeenCalled();
  });

  it("drops back to the login screen when the session expires", async () => {
    listAgents.mockResolvedValue([RECON]);
    render(<App />);
    await screen.findByLabelText("Agents");
    authListeners.forEach((fn) => fn("signed-out"));
    expect(await screen.findByRole("button", { name: /sign in/i })).toBeInTheDocument();
  });

  it("navigates to sessions and settings", async () => {
    listAgents.mockResolvedValue([RECON]);
    render(<App />);
    fireEvent.click(await screen.findByText("Sessions"));
    expect(await screen.findByRole("heading", { name: "Sessions" })).toBeInTheDocument();
    fireEvent.click(screen.getByText("Settings"));
    expect(await screen.findByRole("heading", { name: "Settings" })).toBeInTheDocument();
    expect(await screen.findByText(/signed in as/i)).toBeInTheDocument();
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
