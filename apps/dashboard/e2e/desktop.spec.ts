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
      // Model providers keep their sign-in cards; connected ones show provenance.
      await expect(page.getByTestId("provider-nous").getByText("Connected")).toBeVisible();

      const wrapper = page.getByTestId("provider-claude_wrapper");
      await expect(wrapper.getByText("Not set")).toBeVisible();
      await wrapper.getByRole("button", { name: "Set" }).click();
      const input = wrapper.getByLabel(/New value/);
      await expect(input).toHaveAttribute("type", "password");
      await input.fill(SECRET);
      await wrapper.getByRole("button", { name: "Save" }).click();

      await expect(wrapper.getByText("Not set")).toBeHidden();
      await expect(page.getByRole("status")).toContainText(/restart/i);
      // Never echoed: not in the page, not in the API.
      expect(await page.content()).not.toContain(SECRET);
      const creds = await page.request.get("/api/settings/credentials");
      expect(await creds.text()).not.toContain(SECRET);
      const audit = await page.request.get("/api/audit?source=operator");
      const auditText = await audit.text();
      expect(auditText).toContain("created CLAUDE_WRAPPER_API_KEY");
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

    test("customize edits an agent, improves text and paints a headshot", async ({ page }) => {
      // Only the nav item exists before the view mounts, so the bare text is
      // unambiguous here (the view's own header appears after the click).
      await page.getByText("Customize").click();

      // Pick Scout in the agent chips.
      await page.getByLabel("Customize Scout").click();
      const jobRole = page.getByLabel("Job role", { exact: true });
      await expect(jobRole).toHaveValue("Researches suppliers and drafts outreach");

      // Save an edited job role; the roster shows it after the refresh.
      await jobRole.fill("Sources rare console parts");
      await page.getByLabel("Save identity").click();
      await expect(
        page.getByLabel("Agents").getByText("Sources rare console parts"),
      ).toBeVisible();

      // ✨ Improve rewrites a field through the assist endpoint.
      const personality = page.getByLabel("Personality", { exact: true });
      await personality.fill("be nice");
      await page.getByLabel("Improve personality").click();
      await expect(personality).toHaveValue("be nice (polished)");

      // Generate the headshot: the button flips to Regenerate and the picker
      // chip swaps the blob for the generated image.
      await page.getByRole("button", { name: "Generate headshot" }).click();
      await expect(
        page.getByRole("button", { name: "Regenerate headshot" }),
      ).toBeVisible();
      await expect(page.getByAltText("Scout avatar").first()).toBeVisible();

      // Choose a different model; after the save the roster round-trips it and
      // the Save button disables again (selection == saved value).
      await page.getByLabel("Model", { exact: true }).selectOption("nous|hermes-4-70b");
      await page.getByLabel("Save model").click();
      await expect(page.getByLabel("Save model")).toBeDisabled();
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
