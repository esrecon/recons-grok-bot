import { expect, type Page } from "@playwright/test";

// Mock-only operator credentials (see mock-server/server.mjs). The real
// orchestrator reads a password hash from its environment instead.
export const OPERATOR = { username: "tony", password: "recons-dev" };

export async function login(page: Page) {
  await page.goto("/");
  await page.getByLabel("Username").fill(OPERATOR.username);
  await page.getByLabel("Password").fill(OPERATOR.password);
  await page.getByRole("button", { name: "Sign in" }).click();
  await expect(page.getByLabel("Agents")).toBeVisible();
}
