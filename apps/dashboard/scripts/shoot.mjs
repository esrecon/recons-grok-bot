// Ad-hoc screenshot helper for visual QA. Assumes the mock server is serving
// the built dist on :8330. Uses the preinstalled Chromium.
import { chromium } from "@playwright/test";

const base = "http://127.0.0.1:8330";
// Use the environment's preinstalled Chromium rather than downloading one.
const browser = await chromium.launch({
  executablePath: process.env.PW_CHROMIUM || "/opt/pw-browsers/chromium",
});

async function shoot(name, { width, height }, prep) {
  const ctx = await browser.newContext({ viewport: { width, height } });
  const page = await ctx.newPage();
  await page.goto(base, { waitUntil: "networkidle" });
  await page.getByText("Recon").first().waitFor();
  if (prep) await prep(page);
  await page.screenshot({ path: `../../.preview/${name}.png` });
  await ctx.close();
  console.log("wrote", name);
}

await shoot("desktop-light", { width: 1200, height: 800 });
await shoot("desktop-dark", { width: 1200, height: 800 }, async (page) => {
  await page.getByLabel("Toggle theme").click();
  await page.waitForTimeout(150);
});
await shoot("new-agent", { width: 1200, height: 800 }, async (page) => {
  await page.getByLabel("New agent").click();
  await page.getByPlaceholder("e.g. Scout").fill("Fixer");
  await page.getByPlaceholder(/Researches suppliers/).fill("Chases overdue invoices");
  await page.waitForTimeout(150);
});
await shoot("phone", { width: 402, height: 850 });
await shoot("audit", { width: 1200, height: 800 }, async (page) => {
  await page.getByText("Audit log").click();
  await page.getByText("Priced. Cheapest is Acme.").waitFor();
});

await browser.close();
