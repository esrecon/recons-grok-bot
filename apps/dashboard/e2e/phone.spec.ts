import { test, expect } from "@playwright/test";

// Phone-shaped journeys: the single-pane messenger navigation and proof the
// app is installable as an Android home-screen app (PWA).

test.describe("phone", () => {

  test("single-pane navigation: roster → chat → back", async ({ page }) => {
    await page.goto("/");
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
