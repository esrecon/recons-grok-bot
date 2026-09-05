import { test, expect } from "@playwright/test";
import { login } from "./helpers";

// Phone-shaped journeys: sign in, the single-pane messenger navigation, the
// history and settings surfaces one-handed, and proof the app is installable
// as an Android home-screen app (PWA).

test.describe("phone", () => {
  test("single-pane navigation: sign in → roster → chat → back", async ({ page }) => {
    await login(page);
    // Roster is the home screen; the conversation pane is not shown yet.
    const roster = page.getByLabel("Agents");
    await expect(roster).toBeVisible();
    await expect(page.getByLabel(/Message /)).toBeHidden();

    await roster.getByText("Scout").click();
    await expect(page.getByLabel(/Message Scout/)).toBeVisible();

    // Back returns to the roster.
    await page.getByRole("button", { name: /Agents$/ }).click();
    await expect(page.getByLabel(/Message Scout/)).toBeHidden();
    await expect(roster.getByText("Recon")).toBeVisible();
  });

  test("sessions and settings are usable one-handed", async ({ page }) => {
    await login(page);
    await page.getByText("Sessions").click();
    await page.getByText("Price these three suppliers.").first().click();
    await expect(page.getByTestId("transcript").getByText("Priced. Cheapest is Acme.")).toBeVisible();
    // Back to the list, then over to Settings.
    await page.getByRole("button", { name: /Sessions$/ }).click();
    await page.getByRole("button", { name: /Agents$/ }).click();
    await page.getByText("Settings").click();
    await expect(page.getByTestId("provider-nous").getByText("Configured")).toBeVisible();
    await expect(page.getByRole("button", { name: "Sign out" })).toBeVisible();
  });

  test("is installable: manifest and service worker are served", async ({ page }) => {
    await page.goto("/");
    const href = await page.getAttribute('link[rel="manifest"]', "href");
    expect(href).toBeTruthy();

    const manifest = await page.request.get(href!);
    expect(manifest.ok()).toBeTruthy();
    const json = await manifest.json();
    expect(json.name).toBe("Recons Grok Bot");
    expect(json.display).toBe("standalone");
    // Icons required for an Android home-screen install.
    const sizes = json.icons.map((i: { sizes: string }) => i.sizes);
    expect(sizes).toContain("192x192");
    expect(sizes).toContain("512x512");

    const sw = await page.request.get("/sw.js");
    expect(sw.ok()).toBeTruthy();
  });
});
