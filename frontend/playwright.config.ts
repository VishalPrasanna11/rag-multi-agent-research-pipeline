import { defineConfig, devices } from "@playwright/test";

const PORT = 8031;
export default defineConfig({
  testDir: "e2e",
  timeout: 120_000,
  expect: { timeout: 15_000 },
  retries: process.env.CI ? 1 : 0,
  reporter: [["list"], ["html", { open: "never" }]],
  use: { baseURL: `http://localhost:${PORT}`, trace: "retain-on-failure", screenshot: "only-on-failure" },
  projects: [
    { name: "desktop-light", use: { ...devices["Desktop Chrome"], colorScheme: "light" } },
    { name: "phone-dark", use: { ...devices["Pixel 7"], colorScheme: "dark" } },
  ],
  webServer: {
    // The built SPA served by the real API in fixture mode (no keys).
    command: `cd .. && ENV=test FIXTURE_MODE=1 EVAL_HOOKS=1 RUNS_PER_HOUR=1000 ANON_RUNS_PER_HOUR=1000 DATABASE_URL=sqlite:////tmp/ei-e2e-${Date.now()}.db .venv/bin/uvicorn app.main:app --app-dir backend --port ${PORT}`,
    url: `http://localhost:${PORT}/api/v1/health`,
    reuseExistingServer: !process.env.CI,
    timeout: 180_000,
  },
});
