import { expect, type Page } from "@playwright/test";

// Mock-only operator credentials (see mock-server/server.mjs). The real
// orchestrator reads a password hash from its environment instead.
export const OPERATOR = { username: "tony", password: "recons-dev" };

// Sign in and wait for whichever surface follows: the roster on a configured
// install, or the first-run setup wizard on a fresh one.
export async function login(page: Page) {
  await page.goto("/");
  await page.getByLabel("Username").fill(OPERATOR.username);
  await page.getByLabel("Password").fill(OPERATOR.password);
  await page.getByRole("button", { name: "Sign in" }).click();
  await expect(
    page.getByLabel("Agents").or(page.getByText("Let’s get your team running")),
  ).toBeVisible();
}
