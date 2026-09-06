import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor, within } from "@testing-library/react";
import type { Agent } from "../types";

const skills = vi.fn();
const skillDetail = vi.fn();
const skillFile = vi.fn();
const approveSkill = vi.fn();
vi.mock("../api", () => ({
  api: {
    skills: () => skills(),
    skillDetail: (s: string, slug: string, agent?: string) => skillDetail(s, slug, agent),
    skillFile: (s: string, slug: string, path: string, agent?: string) => skillFile(s, slug, path, agent),
    approveSkill: (a: string, s: string) => approveSkill(a, s),
    rejectSkill: vi.fn().mockResolvedValue(undefined),
  },
}));

import { SkillsView } from "../views/SkillsView";

const AGENTS: Agent[] = [
  { id: "scout", name: "Scout", role: "Research", tier: "workhorse", avatar_color: "#3b82f6", status: "running", is_lead: false, created_at: "" },
];

describe("SkillsView detail inspection", () => {
  beforeEach(() => {
    skills.mockReset().mockResolvedValue({
      shared: [{ slug: "invoice-chase", name: "Invoice Chase", description: "chase", source: "shared", version: "1.0.0" }],
      pending: [{ slug: "sup", name: "Supplier Onboard", description: "onboard", source: "pending", agent: "scout" }],
    });
    skillDetail.mockReset().mockResolvedValue({
      skill: { slug: "sup", name: "Supplier Onboard", description: "onboard", source: "pending", agent: "scout" },
      frontmatter: { name: "Supplier Onboard", description: "onboard", version: "0.1.0" },
      body: "# Supplier Onboard\n\n1. Open portal <img src=x onerror=alert(1)>\n",
      truncated: false,
      files: [
        { path: "SKILL.md", size: 120, kind: "text" },
        { path: "run.sh", size: 20, kind: "script" },
      ],
      warnings: ["no Guardrails section — every taught skill must say where the agent stops and asks", "contains scripts: run.sh — read them before approving"],
    });
    skillFile.mockReset().mockResolvedValue({ path: "run.sh", size: 20, text: "curl https://example", truncated: false });
    approveSkill.mockReset().mockResolvedValue({});
  });

  it("opens a read-only inspector with warnings, plain-text body and files", async () => {
    render(<SkillsView agents={AGENTS} />);
    fireEvent.click(await screen.findByRole("button", { name: /inspect supplier onboard/i }));
    await waitFor(() => expect(skillDetail).toHaveBeenCalledWith("pending", "sup", "scout"));
    const panel = await screen.findByRole("dialog", { name: /supplier onboard/i });
    expect(within(panel).getByText(/no guardrails section/i)).toBeInTheDocument();
    expect(within(panel).getByText(/contains scripts/i)).toBeInTheDocument();
    // The body is shown as text, never rendered as HTML.
    expect(within(panel).getByText(/<img src=x onerror=alert\(1\)>/)).toBeInTheDocument();
    expect(panel.querySelector("img")).toBeNull();
    // Frontmatter and files are listed.
    expect(within(panel).getByText("0.1.0")).toBeInTheDocument();
    fireEvent.click(within(panel).getByRole("button", { name: "run.sh" }));
    await waitFor(() => expect(skillFile).toHaveBeenCalledWith("pending", "sup", "run.sh", "scout"));
    expect(await within(panel).findByText(/curl https:\/\/example/)).toBeInTheDocument();
  });

  it("can approve from inside the inspector, and close it", async () => {
    render(<SkillsView agents={AGENTS} />);
    fireEvent.click(await screen.findByRole("button", { name: /inspect supplier onboard/i }));
    const panel = await screen.findByRole("dialog", { name: /supplier onboard/i });
    fireEvent.click(within(panel).getByRole("button", { name: "Approve" }));
    await waitFor(() => expect(approveSkill).toHaveBeenCalledWith("scout", "sup"));
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
  });

  it("inspects shared skills too", async () => {
    skillDetail.mockResolvedValue({
      skill: { slug: "invoice-chase", name: "Invoice Chase", description: "chase", source: "shared", version: "1.0.0" },
      frontmatter: { name: "Invoice Chase" }, body: "# Invoice Chase", truncated: false,
      files: [{ path: "SKILL.md", size: 10, kind: "text" }], warnings: [],
    });
    render(<SkillsView agents={AGENTS} />);
    fireEvent.click(await screen.findByRole("button", { name: /inspect invoice chase/i }));
    await waitFor(() => expect(skillDetail).toHaveBeenCalledWith("shared", "invoice-chase", undefined));
    const panel = await screen.findByRole("dialog", { name: /invoice chase/i });
    expect(within(panel).getByText(/no concerns/i)).toBeInTheDocument();
    fireEvent.click(within(panel).getByRole("button", { name: /close/i }));
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
  });
});
