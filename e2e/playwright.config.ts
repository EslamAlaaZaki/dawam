import { defineConfig } from "@playwright/test";

// The suite drives a running Compose stack (compose.e2e.yaml, e2e/README.md); it does not
// start one. DAWAM listens on the `edge` port, 8000 by default.
export default defineConfig({
  testDir: "./tests",
  // One journey that builds on its own earlier steps, so nothing runs in parallel.
  workers: 1,
  fullyParallel: false,
  timeout: 5 * 60_000,
  expect: { timeout: 15_000 },
  retries: 0,
  reporter: process.env.CI ? [["github"], ["html", { open: "never" }]] : "list",
  use: {
    baseURL: process.env.E2E_BASE_URL ?? "http://localhost:8000",
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
  },
});
