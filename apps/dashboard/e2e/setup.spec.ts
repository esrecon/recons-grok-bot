import { test, expect } from "@playwright/test";

// The first-run experience on a brand-new install: everything a user needs to
// do happens in the app. Runs against the mock backend in fresh mode.
// These tests share one stateful mock backend and reset it between runs, so
// they must not run concurrently with each other — a parallel reset would wipe
// another test's in-flight sign-in.
test.describe.configure({ mode: "serial" });

test.describe("first-run setup", () => {
  // Each test starts from a clean brand-new install.
  test.beforeEach(async ({ request }) => {
    await request.post("/api/__reset");
  });

  test("connects a provider by pasting a key, then unlocks agent creation", async ({
    page,
  }) => {
    await page.goto("/");
    await expect(page.getByText("Let’s get your team running")).toBeVisible();

    // Agent creation is gated until a provider exists.
    await expect(page.getByRole("button", { name: "New agent" })).toBeDisabled();

    await page.getByLabel("Nous Portal API key").fill("nous-test-key");
    await page.getByTestId("provider-nous").getByRole("button", { name: "Save" }).click();

    await expect(page.getByTestId("provider-nous").getByText("Connected")).toBeVisible();
    await expect(page.getByRole("button", { name: "New agent" })).toBeEnabled();
  });

  test("subscription sign-in shows a link and code in the app, then completes", async ({
    page,
  }) => {
    await page.goto("/");
    await page.getByRole("button", { name: /sign in with chatgpt/i }).click();

    const flow = page.getByTestId("login-flow");
    await expect(flow).toBeVisible();
    await expect(flow.getByText("https://auth.example.com/device")).toBeVisible();
    await expect(flow.getByText("WXYZ-1234")).toBeVisible();

    // The mock completes the device flow shortly after; the card flips to Connected.
    await expect(page.getByTestId("provider-openai").getByText("Connected")).toBeVisible({
      timeout: 10_000,
    });
  });

  test("the whole setup can be finished without leaving the app", async ({ page }) => {
    await page.goto("/");
    await page.getByLabel("Nous Portal API key").fill("nous-test-key");
    await page.getByTestId("provider-nous").getByRole("button", { name: "Save" }).click();
    await expect(page.getByRole("button", { name: "New agent" })).toBeEnabled();

    await page.getByRole("button", { name: "New agent" }).click();
    await page.getByPlaceholder("e.g. Scout").fill("Recon");
    await page.getByPlaceholder(/Researches suppliers/).fill("Lead assistant");
    await page.getByRole("button", { name: "Create agent" }).click();

    // Wizard gives way to the real app, with the new agent in the roster.
    await expect(page.getByLabel("Agents").getByText("Recon")).toBeVisible();
  });
});
