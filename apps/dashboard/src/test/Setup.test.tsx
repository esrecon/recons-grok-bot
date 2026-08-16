import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import type { Provider, SetupStatus } from "../types";

const listAgents = vi.fn();
const setupFn = vi.fn();
const saveProviderKey = vi.fn();
const startProviderLogin = vi.fn();
const pollProviderLogin = vi.fn();

vi.mock("../api", () => ({
  api: {
    listAgents: () => listAgents(),
    setup: () => setupFn(),
    createAgent: vi.fn(),
    saveProviderKey: (id: string, key: string) => saveProviderKey(id, key),
    clearProviderKey: vi.fn(),
    startProviderLogin: (id: string) => startProviderLogin(id),
    pollProviderLogin: (id: string) => pollProviderLogin(id),
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
    imageSettings: vi
      .fn()
      .mockResolvedValue({ key_set: false, base_url: "", model: "" }),
    getTelegram: vi.fn().mockResolvedValue({
      enabled: false,
      allowed_users: "",
      token_set: false,
      imported: false,
    }),
  },
}));

import { App } from "../App";

const PROVIDERS: Provider[] = [
  {
    id: "nous", label: "Nous Portal", tier: "bulk", method: "api_key",
    state: "not_configured", detail: "Paste an API key.",
  },
  {
    id: "openai", label: "ChatGPT", tier: "workhorse", method: "oauth",
    state: "not_configured", detail: "Sign in with your subscription.",
  },
];

const FRESH: SetupStatus = {
  providers: PROVIDERS,
  has_provider: false,
  has_agents: false,
  complete: false,
};

describe("first-run setup", () => {
  beforeEach(() => {
    listAgents.mockReset().mockResolvedValue([]);
    setupFn.mockReset().mockResolvedValue(FRESH);
    saveProviderKey.mockReset().mockResolvedValue({ ...PROVIDERS[0], state: "configured" });
    startProviderLogin.mockReset();
    pollProviderLogin.mockReset();
  });

  it("shows the wizard instead of the empty roster when nothing is set up", async () => {
    render(<App />);
    expect(await screen.findByText(/let’s get your team running/i)).toBeInTheDocument();
    expect(screen.getByText(/connect a brain/i)).toBeInTheDocument();
  });

  it("saves an API key typed into the app — no terminal involved", async () => {
    render(<App />);
    const input = await screen.findByLabelText("Nous Portal API key");
    fireEvent.change(input, { target: { value: "nous-key-123" } });
    fireEvent.click(screen.getAllByRole("button", { name: "Save" })[0]);

    await waitFor(() =>
      expect(saveProviderKey).toHaveBeenCalledWith("nous", "nous-key-123"),
    );
  });

  it("runs subscription sign-in in the app, showing the link and code", async () => {
    startProviderLogin.mockResolvedValue({
      id: "l1", provider: "openai", status: "awaiting_user",
      url: "https://auth.example.com/device", code: "WXYZ-1234",
      message: "", command: "hermes auth add openai", output: [],
    });
    render(<App />);
    fireEvent.click(await screen.findByRole("button", { name: /sign in with chatgpt/i }));

    expect(await screen.findByTestId("login-flow")).toBeInTheDocument();
    expect(screen.getByText("https://auth.example.com/device")).toBeInTheDocument();
    expect(screen.getByText("WXYZ-1234")).toBeInTheDocument();
  });

  it("tells you the manual command if the sign-in helper fails", async () => {
    startProviderLogin.mockResolvedValue({
      id: "l2", provider: "openai", status: "failed",
      url: null, code: null,
      message: "The sign-in helper exited without giving a link. Run this in a terminal on the VPS to finish: hermes auth add openai",
      command: "hermes auth add openai", output: [],
    });
    render(<App />);
    fireEvent.click(await screen.findByRole("button", { name: /sign in with chatgpt/i }));

    expect(await screen.findByText(/exited without giving a link/i)).toBeInTheDocument();
    expect(screen.getByText("hermes auth add openai")).toBeInTheDocument();
  });

  it("blocks agent creation until a provider is connected", async () => {
    render(<App />);
    const newAgent = await screen.findByRole("button", { name: "New agent" });
    expect(newAgent).toBeDisabled();
    expect(screen.getByText(/connect a provider first/i)).toBeInTheDocument();
  });

  it("goes straight to the app once setup is complete", async () => {
    setupFn.mockResolvedValue({ ...FRESH, has_provider: true, has_agents: true, complete: true });
    render(<App />);
    await waitFor(() =>
      expect(screen.queryByText(/let’s get your team running/i)).not.toBeInTheDocument(),
    );
  });
});

describe("Settings", () => {
  beforeEach(() => {
    listAgents.mockReset().mockResolvedValue([]);
    setupFn.mockReset().mockResolvedValue({
      providers: [{ ...PROVIDERS[0], state: "configured", detail: "Cheap bulk work." }],
      has_provider: true,
      has_agents: true,
      complete: true,
    });
  });

  it("shows connected providers without ever revealing the key", async () => {
    render(<App />);
    fireEvent.click(await screen.findByText("Settings"));
    expect(await screen.findByTestId("provider-nous")).toBeInTheDocument();
    expect(screen.getByText("Connected")).toBeInTheDocument();
    // No key input offered for an already-connected provider.
    expect(screen.queryByLabelText("Nous Portal API key")).not.toBeInTheDocument();
  });
});
