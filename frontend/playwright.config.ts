import { defineConfig, devices } from "@playwright/test";

// End-to-end tests against a running stack (docker compose, fake LLM, seeded).
//   docker compose up -d --build --wait && docker compose run --rm api python -m claimpilot.seed
//   npm run e2e
export default defineConfig({
  testDir: "e2e",
  testMatch: "**/*.e2e.ts",
  timeout: 90_000,
  retries: process.env.CI ? 1 : 0,
  reporter: process.env.CI ? [["list"], ["html", { open: "never" }]] : "list",
  use: {
    baseURL: process.env.E2E_BASE_URL ?? "http://localhost:8080",
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
  },
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"] } }],
});
