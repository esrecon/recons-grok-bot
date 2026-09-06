import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor, within } from "@testing-library/react";
import type { Agent, CredentialsResponse, Provider, SessionInfo } from "../types";

const credentialStatus = vi.fn();
const setCredential = vi.fn();
const removeCredential = vi.fn();
const securityPosture = vi.fn();
const services = vi.fn();
vi.mock("../api", () => ({
  api: {
    credentialStatus: () => credentialStatus(),
    setCredential: (k: string, v: string) => setCredential(k, v),
    removeCredential: (k: string) => removeCredential(k),
    securityPosture: () => securityPosture(),
    services: () => services(),
    imageSettings: vi.fn().mockResolvedValue({ key_set: false, base_url: "", model: "" }),
    importCandidates: vi.fn().mockResolvedValue({ candidates: [] }),
    getTelegram: vi.fn().mockResolvedValue({ enabled: false, allowed_users: "", token_set: false, imported: false }),
    saveProviderKey: vi.fn(),
    clearProviderKey: vi.fn(),
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
// Model providers come from the setup status (ProviderCard handles keys and sign-in).
const PROVIDERS: Provider[] = [
  { id: "nous", label: "Nous Portal", tier: "bulk", method: "api_key", state: "configured", detail: "Cheap bulk work." },
];
// The credential catalogue adds provenance for those and manages the rest.
const CREDS: CredentialsResponse = {
  restart_required: false,
  integrations: { webhook_feed: { last_event_at: "2026-08-15T12:00:00+00:00", accepted_count: 12, rejected_count: 1 } },
  providers: [
    {
      id: "nous", name: "Nous Portal", description: "Bulk tier.", health: "configured",
      keys: [{ key: "NOUS_API_KEY", label: "API key", secret: true, writable: true, required: true, hint: "",
               configured: true, updated_at: "2026-08-15T12:00:00+00:00", updated_by: "tony" }],
    },
    {
      id: "claude_wrapper", name: "Claude (wrapper)", description: "Lead tier wrapper.", health: "unreachable",
      keys: [
        { key: "CLAUDE_WRAPPER_BASE_URL", label: "Wrapper base URL", secret: false, writable: true, required: false, hint: "",
          configured: true, updated_at: "2026-08-15T12:00:00+00:00", updated_by: "tony" },
        { key: "CLAUDE_WRAPPER_API_KEY", label: "Wrapper API key", secret: true, writable: true, required: false, hint: "",
          configured: false, updated_at: null, updated_by: null },
      ],
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

function renderView(onSignOut = vi.fn()) {
  return render(
    <SettingsView providers={PROVIDERS} agents={AGENTS} session={SESSION} onChanged={vi.fn()} onSignOut={onSignOut} />,
  );
}

describe("SettingsView", () => {
  beforeEach(() => {
    credentialStatus.mockReset().mockResolvedValue(CREDS);
    setCredential.mockReset().mockResolvedValue({ key: "CLAUDE_WRAPPER_API_KEY", action: "created", configured: true, restart_required: true });
    removeCredential.mockReset().mockResolvedValue({ key: "RECONS_WEBHOOK_SECRET", action: "removed", configured: false, restart_required: true });
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

  it("shows model providers with provenance and the catalogue masked, never a value", async () => {
    renderView();
    const nous = await screen.findByTestId("provider-nous");
    expect(within(nous).getByText("Connected")).toBeInTheDocument();
    expect(await within(nous).findByText(/Key updated .* by tony/)).toBeInTheDocument();
    const wrapper = await screen.findByTestId("provider-claude_wrapper");
    expect(within(wrapper).getByText("Configured")).toBeInTheDocument();
    expect(within(wrapper).getByText("Not set")).toBeInTheDocument();
    expect(within(wrapper).getByText(/by tony/)).toBeInTheDocument();
    const orch = screen.getByTestId("provider-orchestrator");
    expect(within(orch).getByText(/managed on the server/i)).toBeInTheDocument();
    // No reveal affordance anywhere, and integration health is visible.
    expect(screen.queryByText(/reveal|show value/i)).not.toBeInTheDocument();
    expect(screen.getByText(/12 accepted/)).toBeInTheDocument();
  });

  it("sets a credential through a write-only input that is cleared after save", async () => {
    renderView();
    const wrapper = await screen.findByTestId("provider-claude_wrapper");
    fireEvent.click(within(wrapper).getByRole("button", { name: "Set" }));
    const input = within(wrapper).getByLabelText(/new value for CLAUDE_WRAPPER_API_KEY/i) as HTMLInputElement;
    expect(input.type).toBe("password");
    expect(input.autocomplete).toBe("off");
    fireEvent.change(input, { target: { value: "secret-abc-123" } });
    fireEvent.click(within(wrapper).getByRole("button", { name: /save/i }));
    await waitFor(() => expect(setCredential).toHaveBeenCalledWith("CLAUDE_WRAPPER_API_KEY", "secret-abc-123"));
    await waitFor(() => expect(credentialStatus).toHaveBeenCalledTimes(2));
    expect(within(wrapper).queryByLabelText(/new value/i)).not.toBeInTheDocument();
    expect(document.body.innerHTML).not.toContain("secret-abc-123");
    expect(await screen.findByRole("status")).toHaveTextContent(/restart/i);
  });

  it("replaces and removes with confirmation", async () => {
    renderView();
    const orch = await screen.findByTestId("provider-orchestrator");
    expect(within(orch).getByRole("button", { name: "Replace" })).toBeInTheDocument();
    fireEvent.click(within(orch).getByRole("button", { name: "Remove" }));
    expect(removeCredential).not.toHaveBeenCalled();
    fireEvent.click(within(orch).getByRole("button", { name: /confirm remove/i }));
    await waitFor(() => expect(removeCredential).toHaveBeenCalledWith("RECONS_WEBHOOK_SECRET"));
  });

  it("shows a validation error without echoing the value", async () => {
    setCredential.mockRejectedValueOnce(new Error("value contains unsupported characters"));
    renderView();
    const wrapper = await screen.findByTestId("provider-claude_wrapper");
    fireEvent.click(within(wrapper).getByRole("button", { name: "Set" }));
    fireEvent.change(within(wrapper).getByLabelText(/new value/i), { target: { value: "bad value" } });
    fireEvent.click(within(wrapper).getByRole("button", { name: /save/i }));
    expect(await within(wrapper).findByText(/unsupported characters/)).toBeInTheDocument();
  });

  it("shows agent service health and the security posture", async () => {
    renderView();
    const svc = await screen.findByTestId("services");
    expect(await within(svc).findByText("hermes-gateway@scout.service")).toBeInTheDocument();
    expect(within(svc).getByText("failed")).toBeInTheDocument();
    const posture = await screen.findByTestId("security-posture");
    expect(await within(posture).findByText(/Sign-in mode/)).toBeInTheDocument();
    expect(within(posture).getByText(/Secure cookies/)).toBeInTheDocument();
    expect(within(posture).getByText(/CSRF/)).toBeInTheDocument();
  });

  it("signs out", async () => {
    const onSignOut = vi.fn();
    renderView(onSignOut);
    fireEvent.click(await screen.findByRole("button", { name: /sign out/i }));
    expect(onSignOut).toHaveBeenCalled();
  });
});
