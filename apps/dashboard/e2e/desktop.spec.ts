import { test, expect } from "@playwright/test";
import { login } from "./helpers";

// End-to-end smoke over the real built SPA + the mock backend (which enforces
// the same session, CSRF and security-header contract as the orchestrator).
// Covers the journeys Tony will actually perform: sign in, see the roster,
// chat with an agent, create a permanent agent in one click, read the audit
// trail and session history, inspect and approve a taught skill, schedule a
// routine, and manage credentials without ever seeing a value.

test.describe("desktop", () => {
  test("signed-out visitors only get the login screen", async ({ page }) => {
    await page.goto("/");
    await expect(page.getByRole("button", { name: "Sign in" })).toBeVisible();
    await expect(page.getByLabel("Agents")).toBeHidden();
    // The API refuses too.
    const r = await page.request.get("/api/agents");
    expect(r.status()).toBe(401);
    // A wrong password is explained, not swallowed.
    await page.getByLabel("Username").fill("tony");
    await page.getByLabel("Password").fill("wrong");
    await page.getByRole("button", { name: "Sign in" }).click();
    await expect(page.getByText(/wrong username or password/i)).toBeVisible();
  });

  test.describe("signed in", () => {
    test.beforeEach(async ({ page }) => {
      await login(page);
    });

    test("roster renders with job roles", async ({ page }) => {
      const roster = page.getByLabel("Agents");
      await expect(roster.getByText("Recon")).toBeVisible();
      await expect(roster.getByText("Lead assistant — coordinates the team")).toBeVisible();
      await expect(roster.getByText("Scout")).toBeVisible();
    });

    test("chat streams a reply and shows tool activity", async ({ page }) => {
      await page.getByLabel("Agents").getByText("Scout").click();
      await page.getByLabel(/Message Scout/).fill("find brake caliper suppliers");
      await page.getByLabel("Send").click();

      await expect(page.getByText(/Working on: find brake caliper suppliers/)).toBeVisible();
      await expect(page.getByText("browser_navigate")).toBeVisible();
    });

    test("risky request surfaces an inline approval card that reaches the backend", async ({ page }) => {
      await page.getByLabel("Agents").getByText("Recon").click();
      await page.getByLabel(/Message Recon/).fill("send the supplier email");
      await page.getByLabel("Send").click();

      const card = page.getByTestId("approval-card");
      await expect(card).toBeVisible();
      await expect(card.getByRole("button", { name: "Approve" })).toBeVisible();
      await card.getByRole("button", { name: "Deny" }).click();
      await expect(page.getByText("You denied the action.")).toBeVisible();

      // The decision is an operator action in the audit log.
      await page.getByText("Audit log").click();
      await page.getByLabel("Filter by source").selectOption("operator");
      await expect(page.getByText(/approval_deny recon\//)).toBeVisible();
    });

    test("one click creates a permanent agent", async ({ page }) => {
      await page.getByLabel("New agent").click();
      await page.getByPlaceholder("e.g. Scout").fill("Fixer");
      await page.getByPlaceholder(/Researches suppliers/).fill("Chases overdue invoices");
      await page.getByRole("button", { name: "Create agent" }).click();

      await expect(page.getByLabel("Agents").getByText("Fixer")).toBeVisible();
      await expect(page.getByLabel("Agents").getByText("Chases overdue invoices")).toBeVisible();
    });

    test("audit log shows agent-to-agent hand-offs and filters", async ({ page }) => {
      await page.getByText("Audit log").click();

      await expect(page.getByText("Recon → Scout")).toBeVisible();
      await expect(page.getByText("Priced. Cheapest is Acme.").first()).toBeVisible();

      await page.getByRole("button", { name: "Agent-to-agent only" }).click();
      await expect(page.getByText("Find three suppliers for brake calipers.")).toBeHidden();
      await expect(page.getByText("Recon → Scout")).toBeVisible();
    });

    test("sessions list opens a transcript", async ({ page }) => {
      await page.getByText("Sessions").click();
      await expect(page.getByRole("heading", { name: "Sessions" })).toBeVisible();
      await page.getByText("Find three suppliers for brake calipers.").click();
      const transcript = page.getByTestId("transcript");
      await expect(transcript.getByText("Here are three suppliers.")).toBeVisible();
      await expect(transcript.getByText("browser_navigate")).toBeVisible();
      await expect(page.getByText("agent:recon:main")).toBeVisible();
    });

    test("a taught skill can be inspected, then approved into the shared library", async ({ page }) => {
      await page.getByText("Skills").click();

      await expect(page.getByTestId("pending-queue")).toBeVisible();
      await page.getByRole("button", { name: "Inspect Supplier Onboard" }).click();
      const panel = page.getByRole("dialog", { name: /Supplier Onboard/ });
      await expect(panel.getByText(/no Guardrails section/)).toBeVisible();
      await expect(panel.getByText(/contains scripts/)).toBeVisible();
      await panel.getByRole("button", { name: "send.sh" }).click();
      await expect(panel.getByText(/curl -X POST/)).toBeVisible();
      await panel.getByRole("button", { name: "Approve" }).click();

      await expect(page.getByTestId("pending-queue")).toBeHidden();
      await expect(page.getByText("Supplier Onboard")).toBeVisible();
    });

    test("a routine can be scheduled", async ({ page }) => {
      await page.getByText("Routines").click();

      await page.getByLabel("Schedule").fill("every Monday at 9am");
      await page.getByLabel("Instruction").fill("Check stock levels");
      await page.getByRole("button", { name: "Create routine" }).click();

      await expect(page.getByTestId("routine-list").getByText("Check stock levels")).toBeVisible();
    });

    test("settings manages credentials without ever showing a value", async ({ page }) => {
      const SECRET = "e2e-secret-value-XYZ-9f8e7d";
      await page.getByText("Settings").click();
      await expect(page.getByRole("heading", { name: "Settings" })).toBeVisible();
      await expect(page.getByText(/Signed in as/)).toBeVisible();

      const anthropic = page.getByTestId("provider-anthropic");
      await expect(anthropic.getByText("Not set")).toBeVisible();
      await anthropic.getByRole("button", { name: "Set" }).click();
      const input = anthropic.getByLabel(/New value/);
      await expect(input).toHaveAttribute("type", "password");
      await input.fill(SECRET);
      await anthropic.getByRole("button", { name: "Save" }).click();

      await expect(anthropic.getByText("Configured")).toBeVisible();
      await expect(page.getByText(/restart/i).first()).toBeVisible();
      // Never echoed: not in the page, not in the API.
      expect(await page.content()).not.toContain(SECRET);
      const providers = await page.request.get("/api/settings/providers");
      expect(await providers.text()).not.toContain(SECRET);
      const audit = await page.request.get("/api/audit?source=operator");
      const auditText = await audit.text();
      expect(auditText).toContain("created ANTHROPIC_API_KEY");
      expect(auditText).not.toContain(SECRET);
      // Reveal is not a thing.
      await expect(page.getByText(/reveal/i)).toHaveCount(0);

      // Service health and posture are visible.
      await expect(page.getByTestId("services").getByText("hermes-gateway@recon.service")).toBeVisible();
      await expect(page.getByTestId("security-posture").getByText(/CSRF protection/)).toBeVisible();
    });

    test("mutations without the CSRF token are refused by the backend", async ({ page }) => {
      // The browser session cookie is present, but no token: refused.
      const r = await page.request.post("/api/agents/scout/pause", {
        headers: { "sec-fetch-site": "same-origin" },
      });
      expect(r.status()).toBe(403);
      // And the roster is unaffected.
      await expect(page.getByLabel("Agents").getByText("Scout")).toBeVisible();
    });

    test("sign out returns to the login screen", async ({ page }) => {
      await page.getByText("Settings").click();
      await page.getByRole("button", { name: "Sign out" }).click();
      await expect(page.getByRole("button", { name: "Sign in" })).toBeVisible();
      const r = await page.request.get("/api/agents");
      expect(r.status()).toBe(401);
    });
  });
});
