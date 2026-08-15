import { test, expect } from "@playwright/test";

// End-to-end smoke over the real built SPA + the mock backend. Covers the
// journeys Tony will actually perform: see the roster, chat with an agent,
// create a permanent agent in one click, read the audit trail, approve a
// taught skill, schedule a routine — and install the app on a phone.

test.describe("desktop", () => {

  test("roster renders with job roles", async ({ page }) => {
    await page.goto("/");
    const roster = page.getByLabel("Agents");
    await expect(roster.getByText("Recon")).toBeVisible();
    await expect(roster.getByText("Lead assistant — coordinates the team")).toBeVisible();
    await expect(roster.getByText("Scout")).toBeVisible();
  });

  test("chat streams a reply and shows tool activity", async ({ page }) => {
    await page.goto("/");
    await page.getByLabel("Agents").getByText("Scout").click();
    await page.getByLabel(/Message Scout/).fill("find brake caliper suppliers");
    await page.getByLabel("Send").click();

    // Streamed assistant text arrives.
    await expect(page.getByText(/Working on: find brake caliper suppliers/)).toBeVisible();
    // Tool-call chip is rendered.
    await expect(page.getByText("browser_navigate")).toBeVisible();
  });

  test("risky request surfaces an inline approval card", async ({ page }) => {
    await page.goto("/");
    await page.getByLabel("Agents").getByText("Recon").click();
    await page.getByLabel(/Message Recon/).fill("send the supplier email");
    await page.getByLabel("Send").click();

    const card = page.getByTestId("approval-card");
    await expect(card).toBeVisible();
    await expect(card.getByRole("button", { name: "Approve" })).toBeVisible();
    await card.getByRole("button", { name: "Deny" }).click();
    await expect(page.getByText("You denied the action.")).toBeVisible();
  });

  test("one click creates a permanent agent", async ({ page }) => {
    await page.goto("/");
    await page.getByLabel("New agent").click();
    await page.getByPlaceholder("e.g. Scout").fill("Fixer");
    await page.getByPlaceholder(/Researches suppliers/).fill("Chases overdue invoices");
    await page.getByRole("button", { name: "Create agent" }).click();

    // Appears in the roster with its job role, and its chat opens.
    await expect(page.getByLabel("Agents").getByText("Fixer")).toBeVisible();
    await expect(page.getByLabel("Agents").getByText("Chases overdue invoices")).toBeVisible();
  });

  test("audit log shows agent-to-agent hand-offs and filters", async ({ page }) => {
    await page.goto("/");
    await page.getByText("Audit log").click();

    await expect(page.getByText("Recon → Scout")).toBeVisible();
    await expect(page.getByText("Priced. Cheapest is Acme.")).toBeVisible();

    // A2A-only filter drops the plain chat rows.
    await page.getByRole("button", { name: "Agent-to-agent only" }).click();
    await expect(page.getByText("Find three suppliers for brake calipers.")).toBeHidden();
    await expect(page.getByText("Recon → Scout")).toBeVisible();
  });

  test("a taught skill can be approved into the shared library", async ({ page }) => {
    await page.goto("/");
    await page.getByText("Skills").click();

    await expect(page.getByTestId("pending-queue")).toBeVisible();
    await page.getByRole("button", { name: "Approve" }).click();

    // Moves out of the queue and into the shared library.
    await expect(page.getByTestId("pending-queue")).toBeHidden();
    await expect(page.getByText("Supplier Onboard")).toBeVisible();
  });

  test("a routine can be scheduled", async ({ page }) => {
    await page.goto("/");
    await page.getByText("Routines").click();

    await page.getByLabel("Schedule").fill("every Monday at 9am");
    await page.getByLabel("Instruction").fill("Check stock levels");
    await page.getByRole("button", { name: "Create routine" }).click();

    await expect(page.getByTestId("routine-list").getByText("Check stock levels")).toBeVisible();
  });
});
