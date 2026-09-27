import { defineConfig, devices } from "@playwright/test";

/**
 * Smoke test against a running stack (`make up`). It seeds its own run through the API,
 * using the `scripted` model, so no LLM key is needed.
 */
export default defineConfig({
  testDir: "./e2e",
  timeout: 300_000,
  expect: { timeout: 10_000 },
  retries: 0,
  reporter: [["list"]],
  use: {
    baseURL: process.env.DEVAGENT_WEB_URL ?? "http://localhost:3000",
    viewport: { width: 1440, height: 900 },
    colorScheme: "dark",
    trace: "retain-on-failure",
  },
  projects: [
    {
      name: "chromium",
      use: {
        ...devices["Desktop Chrome"],
        viewport: { width: 1440, height: 900 },
        launchOptions: process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE
          ? { executablePath: process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE }
          : {},
      },
    },
  ],
});
