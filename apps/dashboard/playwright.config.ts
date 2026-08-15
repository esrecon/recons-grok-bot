import { defineConfig, devices } from "@playwright/test";

// E2E runs against the built SPA served by the mock backend on one origin,
// mirroring production (the orchestrator serves the same contract + the SPA).
// Uses the environment's preinstalled Chromium — never downloads a browser.
const PORT = 8340;
const SETUP_PORT = 8341;

export default defineConfig({
  testDir: "./e2e",
  timeout: 30_000,
  fullyParallel: true,
  reporter: process.env.CI ? "line" : "list",
  use: {
    baseURL: `http://127.0.0.1:${PORT}`,
    launchOptions: {
      executablePath: process.env.PW_CHROMIUM || "/opt/pw-browsers/chromium",
    },
  },
  projects: [
    {
      name: "desktop",
      testMatch: /desktop\.spec\.ts/,
      use: { ...devices["Desktop Chrome"] },
    },
    {
      // A brand-new install: its own server on its own port, since the fixture
      // state (no providers, no agents) is the opposite of the other projects'.
      name: "setup",
      testMatch: /setup\.spec\.ts/,
      use: {
        ...devices["Desktop Chrome"],
        baseURL: `http://127.0.0.1:${SETUP_PORT}`,
      },
    },
    {
      name: "phone",
      testMatch: /phone\.spec\.ts/,
      use: {
        ...devices["Pixel 7"],
        // Pixel 7 defaults to the bundled Chrome channel; force our Chromium.
        launchOptions: {
          executablePath: process.env.PW_CHROMIUM || "/opt/pw-browsers/chromium",
        },
      },
    },
  ],
  webServer: [
    {
      command: `node mock-server/server.mjs --serve dist`,
      env: { PORT: String(PORT) },
      url: `http://127.0.0.1:${PORT}/api/health`,
      reuseExistingServer: !process.env.CI,
      timeout: 30_000,
    },
    {
      // Fresh-install fixture for the setup project.
      command: `node mock-server/server.mjs --serve dist`,
      env: { PORT: String(SETUP_PORT), RECONS_MOCK_FRESH: "1" },
      url: `http://127.0.0.1:${SETUP_PORT}/api/health`,
      reuseExistingServer: !process.env.CI,
      timeout: 30_000,
    },
  ],
});
