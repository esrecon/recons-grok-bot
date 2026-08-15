import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import type { Agent } from "../types";

const skills = vi.fn();
const approveSkill = vi.fn();
const routinesFn = vi.fn();
const createRoutine = vi.fn();
const toggleRoutine = vi.fn();
const deleteRoutine = vi.fn();

vi.mock("../api", () => ({
  api: {
    skills: () => skills(),
    approveSkill: (a: string, s: string) => approveSkill(a, s),
    rejectSkill: vi.fn().mockResolvedValue(undefined),
    routines: () => routinesFn(),
    createRoutine: (i: unknown) => createRoutine(i),
    toggleRoutine: (a: string, id: string, act: string) => toggleRoutine(a, id, act),
    deleteRoutine: (a: string, id: string) => deleteRoutine(a, id),
  },
}));

import { SkillsView } from "../views/SkillsView";
import { RoutinesView } from "../views/RoutinesView";

const AGENTS: Agent[] = [
  { id: "scout", name: "Scout", role: "Research", tier: "workhorse", avatar_color: "#3b82f6", status: "running", is_lead: false, created_at: "" },
  { id: "clerk", name: "Clerk", role: "Admin", tier: "bulk", avatar_color: "#4a4a52", status: "running", is_lead: false, created_at: "" },
];

describe("SkillsView", () => {
  beforeEach(() => {
    skills.mockReset();
    approveSkill.mockReset().mockResolvedValue({});
  });

  it("shows the pending approval queue and approves a draft", async () => {
    skills
      .mockResolvedValueOnce({
        shared: [{ slug: "invoice-chase", name: "Invoice Chase", description: "chase", source: "shared" }],
        pending: [{ slug: "sup", name: "Supplier Onboard", description: "onboard", source: "pending", agent: "scout" }],
      })
      .mockResolvedValueOnce({ shared: [], pending: [] });

    render(<SkillsView agents={AGENTS} />);
    expect(await screen.findByTestId("pending-queue")).toBeInTheDocument();
    expect(screen.getByText("Taught by Scout")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Approve" }));
    await waitFor(() => expect(approveSkill).toHaveBeenCalledWith("scout", "sup"));
  });
});

describe("RoutinesView", () => {
  beforeEach(() => {
    routinesFn.mockReset().mockResolvedValue({ routines: [] });
    createRoutine.mockReset().mockResolvedValue({});
    toggleRoutine.mockReset().mockResolvedValue({});
  });

  it("creates a routine from plain-language inputs", async () => {
    render(<RoutinesView agents={AGENTS} />);
    fireEvent.change(await screen.findByLabelText("Schedule"), {
      target: { value: "every weekday at 8am" },
    });
    fireEvent.change(screen.getByLabelText("Instruction"), {
      target: { value: "Post the brief" },
    });
    fireEvent.click(screen.getByRole("button", { name: /create routine/i }));

    await waitFor(() =>
      expect(createRoutine).toHaveBeenCalledWith(
        expect.objectContaining({
          agent: "scout",
          schedule: "every weekday at 8am",
          instruction: "Post the brief",
        }),
      ),
    );
  });

  it("pauses an enabled routine", async () => {
    routinesFn.mockResolvedValue({
      routines: [{ id: "routine-1", agent: "clerk", schedule: "daily", instruction: "brief", enabled: true }],
    });
    render(<RoutinesView agents={AGENTS} />);
    fireEvent.click(await screen.findByRole("button", { name: "Pause" }));
    await waitFor(() =>
      expect(toggleRoutine).toHaveBeenCalledWith("clerk", "routine-1", "pause"),
    );
  });
});
