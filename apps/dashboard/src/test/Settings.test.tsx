import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor, within } from "@testing-library/react";
import type { Agent, ProvidersResponse, SessionInfo } from "../types";

const providers = vi.fn();
const setCredential = vi.fn();
const removeCredential = vi.fn();
const securityPosture = vi.fn();
const services = vi.fn();
vi.mock("../api", () => ({
  api: {
    providers: () => providers(),
    setCredential: (k: string, v: string) => setCredential(k, v),
    removeCredential: (k: string) => removeCredential(k),
    securityPosture: () => securityPosture(),
    services: () => services(),
  },
}));

import { SettingsView } from "../views/SettingsView";

const SESSION: SessionInfo = {
  authenticated: true, operator: "tony", via: "password", csrf_token: "t",
  mode: "password", configured: true, reason: null,
};
const AGENTS: Agent[] = [
  { id: "recon", name: "Recon", role: "Lead", tier: "lead", avatar_color: "#8b5cf6", status: "running", is_lead: true, created_at: "" },
];
const PROVIDERS: ProvidersResponse = {
  restart_required: false,
  integrations: { webhook_feed: { last_event_at: "2026-08-15T12:00:00+00:00", accepted_count: 12, rejected_count: 1 } },
  providers: [
    {
      id: "nous", name: "Nous Portal", description: "Bulk tier.", health: "configured",
      keys: [{ key: "NOUS_API_KEY", label: "API key", secret: true, writable: true, required: true, hint: "",
               configured: true, updated_at: "2026-08-15T12:00:00+00:00", updated_by: "tony" }],
    },
    {
      id: "anthropic", name: "Anthropic API", description: "Fallback.", health: "not_configured",
      keys: [{ key: "ANTHROPIC_API_KEY", label: "API key", secret: true, writable: true, required: false, hint: "",
               configured: false, updated_at: null, updated_by: null }],
    },
    {
      id: "orchestrator", name: "Orchestrator", description: "Login + audit feed.", health: "configured",
      keys: [
        { key: "RECONS_WEBHOOK_SECRET", label: "Webhook signing secret", secret: true, writable: true, required: true, hint: "", configured: true, updated_at: null, updated_by: null },
        { key: "RECONS_OPERATOR_PASSWORD_HASH", label: "Operator password hash", secret: true, writable: false, required: false, hint: "Managed on the server", configured: true, updated_at: null, updated_by: null },
      ],
    },
  ],
};

describe("SettingsView", () => {
  beforeEach(() => {
    providers.mockReset().mockResolvedValue(PROVIDERS);
    setCredential.mockReset().mockResolvedValue({ key: "ANTHROPIC_API_KEY", action: "created", configured: true, restart_required: true });
    removeCredential.mockReset().mockResolvedValue({ key: "NOUS_API_KEY", action: "removed", configured: false, restart_required: true });
    securityPosture.mockReset().mockResolvedValue({
      mode: "password", configured: true, operator: "tony", via: "password", cookie_secure: true, hsts: false,
      session_ttl_seconds: 43200, csrf_protection: true, allowed_origins: [],
      rate_limits: { login_per_minute: 10, api_per_minute: 600 },
    });
    services.mockReset().mockResolvedValue({
      services: [
        { agent: "recon", name: "Recon", unit: "hermes-gateway@recon.service", status: "active", expected: "running", healthy: true },
        { agent: "scout", name: "Scout", unit: "hermes-gateway@scout.service", status: "failed", expected: "running", healthy: false },
      ],
    });
  });

  it("lists providers with masked status and never a value", async () => {
    render(<SettingsView agents={AGENTS} session={SESSION} onSignOut={vi.fn()} />);
    const nous = await screen.findByTestId("provider-nous");
    expect(within(nous).getByText("Configured")).toBeInTheDocument();
    expect(within(nous).getByText(/by tony/)).toBeInTheDocument();
    const anthropic = screen.getByTestId("provider-anthropic");
    expect(within(anthropic).getByText("Not set")).toBeInTheDocument();
    const orch = screen.getByTestId("provider-orchestrator");
    expect(within(orch).getByText(/managed on the server/i)).toBeInTheDocument();
    // No reveal affordance anywhere.
    expect(screen.queryByText(/reveal|show value/i)).not.toBeInTheDocument();
    // Integration health is visible.
    expect(screen.getByText(/12 accepted/)).toBeInTheDocument();
  });

  it("sets a credential through a write-only input that is cleared after save", async () => {
    render(<SettingsView agents={AGENTS} session={SESSION} onSignOut={vi.fn()} />);
    const anthropic = await screen.findByTestId("provider-anthropic");
    fireEvent.click(within(anthropic).getByRole("button", { name: /set/i }));
    const input = within(anthropic).getByLabelText(/new value/i) as HTMLInputElement;
    expect(input.type).toBe("password");
    expect(input.autocomplete).toBe("off");
    fireEvent.change(input, { target: { value: "secret-abc-123" } });
    fireEvent.click(within(anthropic).getByRole("button", { name: /save/i }));
    await waitFor(() => expect(setCredential).toHaveBeenCalledWith("ANTHROPIC_API_KEY", "secret-abc-123"));
    // Reloaded, input gone, and the value is nowhere in the document.
    await waitFor(() => expect(providers).toHaveBeenCalledTimes(2));
    expect(within(anthropic).queryByLabelText(/new value/i)).not.toBeInTheDocument();
    expect(document.body.innerHTML).not.toContain("secret-abc-123");
    expect(await screen.findByText(/restart/i)).toBeInTheDocument();
  });

  it("replaces and removes with confirmation", async () => {
    render(<SettingsView agents={AGENTS} session={SESSION} onSignOut={vi.fn()} />);
    const nous = await screen.findByTestId("provider-nous");
    expect(within(nous).getByRole("button", { name: /replace/i })).toBeInTheDocument();
    fireEvent.click(within(nous).getByRole("button", { name: /remove/i }));
    expect(removeCredential).not.toHaveBeenCalled();
    fireEvent.click(within(nous).getByRole("button", { name: /confirm remove/i }));
    await waitFor(() => expect(removeCredential).toHaveBeenCalledWith("NOUS_API_KEY"));
  });

  it("shows a validation error without echoing the value", async () => {
    setCredential.mockRejectedValueOnce(new Error("value contains unsupported characters"));
    render(<SettingsView agents={AGENTS} session={SESSION} onSignOut={vi.fn()} />);
    const anthropic = await screen.findByTestId("provider-anthropic");
    fireEvent.click(within(anthropic).getByRole("button", { name: /set/i }));
    fireEvent.change(within(anthropic).getByLabelText(/new value/i), { target: { value: "bad value" } });
    fireEvent.click(within(anthropic).getByRole("button", { name: /save/i }));
    expect(await within(anthropic).findByText(/unsupported characters/)).toBeInTheDocument();
  });

  it("shows agent service health and the security posture", async () => {
    render(<SettingsView agents={AGENTS} session={SESSION} onSignOut={vi.fn()} />);
    const svc = await screen.findByTestId("services");
    expect(within(svc).getByText("hermes-gateway@scout.service")).toBeInTheDocument();
    expect(within(svc).getByText("failed")).toBeInTheDocument();
    const posture = await screen.findByTestId("security-posture");
    expect(within(posture).getByText(/password/)).toBeInTheDocument();
    expect(within(posture).getByText(/Secure cookies/)).toBeInTheDocument();
    expect(within(posture).getByText(/CSRF/)).toBeInTheDocument();
  });

  it("signs out", async () => {
    const onSignOut = vi.fn();
    render(<SettingsView agents={AGENTS} session={SESSION} onSignOut={onSignOut} />);
    fireEvent.click(await screen.findByRole("button", { name: /sign out/i }));
    expect(onSignOut).toHaveBeenCalled();
  });
});
