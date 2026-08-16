import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import type { Agent } from "../types";

const modelsFn = vi.fn();
const imageSettings = vi.fn();
const getSoul = vi.fn();
const putSoul = vi.fn();
const updateAgent = vi.fn();
const improve = vi.fn();
const generateAvatar = vi.fn();
const addCustomModel = vi.fn();
const deleteCustomModel = vi.fn();

vi.mock("../api", () => ({
  api: {
    models: () => modelsFn(),
    imageSettings: () => imageSettings(),
    getSoul: (id: string) => getSoul(id),
    putSoul: (id: string, content: string) => putSoul(id, content),
    updateAgent: (id: string, patch: unknown) => updateAgent(id, patch),
    improve: (input: unknown) => improve(input),
    generateAvatar: (id: string) => generateAvatar(id),
    addCustomModel: (input: unknown) => addCustomModel(input),
    deleteCustomModel: (id: string) => deleteCustomModel(id),
    avatarUrl: (id: string, v: number) => `/api/agents/${id}/avatar?v=${v}`,
  },
}));

import { CustomizeView } from "../views/CustomizeView";
import { BotAvatar } from "../components/BotAvatar";

const AGENTS: Agent[] = [
  { id: "scout", name: "Scout", role: "Research", personality: "Be quick.", tier: "bulk", avatar_color: "#3b82f6", status: "running", is_lead: false, created_at: "" },
  { id: "clerk", name: "Clerk", role: "Admin", tier: "bulk", avatar_color: "#4a4a52", status: "running", is_lead: false, created_at: "" },
];

const OPTIONS = [
  { provider: "claude_wrapper", model: "claude-sonnet-4-6", label: "Claude", tier: "lead", source: "tier", available: false },
  { provider: "nous", model: "hermes-4-70b", label: "Hermes", tier: "bulk", source: "tier", available: true },
];

describe("CustomizeView", () => {
  beforeEach(() => {
    modelsFn.mockReset().mockResolvedValue({ options: OPTIONS });
    imageSettings.mockReset().mockResolvedValue({
      key_set: true,
      base_url: "https://api.x.ai/v1",
      model: "grok-2-image",
    });
    getSoul.mockReset().mockResolvedValue({ content: "# Scout\n\nSoul text.\n", exists: true });
    putSoul.mockReset().mockResolvedValue({ content: "# Scout\n\nSaved soul.\n" });
    updateAgent.mockReset().mockResolvedValue(AGENTS[0]);
    improve.mockReset();
    generateAvatar.mockReset().mockResolvedValue({ ...AGENTS[0], avatar_version: 1 });
  });

  it("prefills the selected agent and saves only the dirty fields", async () => {
    const onChanged = vi.fn();
    render(<CustomizeView agents={AGENTS} onChanged={onChanged} />);

    const role = await screen.findByLabelText("Job role");
    expect(role).toHaveValue("Research");
    fireEvent.change(role, { target: { value: "Deep research" } });
    fireEvent.click(screen.getByLabelText("Save identity"));

    await waitFor(() =>
      expect(updateAgent).toHaveBeenCalledWith("scout", { role: "Deep research" }),
    );
    expect(onChanged).toHaveBeenCalled();
  });

  it("switching the picker shows the other agent's fields", async () => {
    render(<CustomizeView agents={AGENTS} onChanged={() => {}} />);
    fireEvent.click(await screen.findByLabelText("Customize Clerk"));
    expect(await screen.findByLabelText("Job role")).toHaveValue("Admin");
    expect(getSoul).toHaveBeenCalledWith("clerk");
  });

  it("✨ Improve replaces the field content with the model's rewrite", async () => {
    improve.mockResolvedValue({ text: "Be fast, thorough and honest." });
    render(<CustomizeView agents={AGENTS} onChanged={() => {}} />);

    fireEvent.click(await screen.findByLabelText("Improve personality"));
    await waitFor(() =>
      expect(screen.getByLabelText("Personality")).toHaveValue(
        "Be fast, thorough and honest.",
      ),
    );
    expect(improve).toHaveBeenCalledWith({
      field: "personality",
      text: "Be quick.",
      agent_context: { name: "Scout", role: "Research" },
    });
  });

  it("generates a headshot and refreshes the roster", async () => {
    const onChanged = vi.fn();
    render(<CustomizeView agents={AGENTS} onChanged={onChanged} />);

    fireEvent.click(
      await screen.findByRole("button", { name: "Generate headshot" }),
    );
    await waitFor(() => expect(generateAvatar).toHaveBeenCalledWith("scout"));
    await waitFor(() => expect(onChanged).toHaveBeenCalled());
  });

  it("hints when no image key is configured", async () => {
    imageSettings.mockResolvedValue({ key_set: false, base_url: "", model: "" });
    render(<CustomizeView agents={AGENTS} onChanged={() => {}} />);
    expect(await screen.findByText(/Needs an image key/)).toBeInTheDocument();
  });

  it("saves a model choice as the provider/model pair", async () => {
    render(<CustomizeView agents={AGENTS} onChanged={() => {}} />);

    const select = await screen.findByLabelText("Model");
    // Options load async; wait until the Hermes option is present.
    await screen.findByRole("option", { name: /Hermes — hermes-4-70b/ });
    fireEvent.change(select, { target: { value: "nous|hermes-4-70b" } });
    fireEvent.click(screen.getByLabelText("Save model"));

    await waitFor(() =>
      expect(updateAgent).toHaveBeenCalledWith("scout", {
        model_provider: "nous",
        model_name: "hermes-4-70b",
      }),
    );
  });

  it("saves the soul and shows the repaired content", async () => {
    render(<CustomizeView agents={AGENTS} onChanged={() => {}} />);
    const soul = await screen.findByLabelText("Soul");
    expect(soul).toHaveValue("# Scout\n\nSoul text.\n");

    fireEvent.change(soul, { target: { value: "# Scout\n\nEdited.\n" } });
    fireEvent.click(screen.getByRole("button", { name: "Save soul" }));

    await waitFor(() =>
      expect(putSoul).toHaveBeenCalledWith("scout", "# Scout\n\nEdited.\n"),
    );
    // The server may repair the managed block; the textarea shows the result.
    await waitFor(() => expect(soul).toHaveValue("# Scout\n\nSaved soul.\n"));
  });

  it("adds a custom model provider", async () => {
    addCustomModel.mockResolvedValue({ model: { id: "grok" } });
    render(<CustomizeView agents={AGENTS} onChanged={() => {}} />);

    fireEvent.click(await screen.findByRole("button", { name: "Add a model…" }));
    fireEvent.change(screen.getByLabelText("Model label"), { target: { value: "Grok" } });
    fireEvent.change(screen.getByLabelText("Model id"), { target: { value: "grok-4" } });
    fireEvent.change(screen.getByLabelText("Base URL"), { target: { value: "https://api.x.ai/v1" } });
    fireEvent.change(screen.getByLabelText("API key"), { target: { value: "xai-1" } });
    fireEvent.click(screen.getByRole("button", { name: "Add model" }));

    await waitFor(() =>
      expect(addCustomModel).toHaveBeenCalledWith({
        label: "Grok",
        base_url: "https://api.x.ai/v1",
        model: "grok-4",
        api_key: "xai-1",
      }),
    );
    // The catalogue reloads after a successful add.
    expect(modelsFn.mock.calls.length).toBeGreaterThan(1);
  });
});

describe("BotAvatar", () => {
  it("renders the generated headshot when a version is set, the blob otherwise", () => {
    const { rerender } = render(
      <BotAvatar id="scout" color="#3b82f6" title="Scout" imageVersion={7} />,
    );
    const img = screen.getByAltText("Scout avatar");
    expect(img.tagName).toBe("IMG");
    expect(img).toHaveAttribute("src", "/api/agents/scout/avatar?v=7");

    rerender(<BotAvatar id="scout" color="#3b82f6" title="Scout" />);
    expect(screen.getByLabelText("Scout avatar").tagName.toLowerCase()).toBe("svg");
  });
});
