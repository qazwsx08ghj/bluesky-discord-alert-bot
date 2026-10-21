import { resolve } from "node:path";
import { defineConfig, devices } from "@playwright/test";

// Browser tests for the built control panel, against the real Django app.
//
//   npm run e2e                 builds the panel, starts config/testing/demo_server.py (a temporary
//                               database, fake Bluesky and Discord, no network) and runs every test
//   E2E_BASE_URL=http://127.0.0.1:18123 npx playwright test --grep @docker
//                               runs the tests that need no fakes against a running bot, e.g. the
//                               Docker test stack (config/tests_docker.py does this)
//   npm run screenshots         saves the screenshots in docs/screenshots/
const external = process.env.E2E_BASE_URL;
const port = Number(process.env.E2E_PORT ?? 8765);
// Forward slashes work in every shell Playwright may use on Windows.
const slashes = (p: string) => p.split("\\").join("/");
const python = slashes(process.env.PYTHON ?? resolve("..", "venv", process.platform === "win32" ? "Scripts/python.exe" : "bin/python"));
const demoServer = slashes(resolve("..", "config", "testing", "demo_server.py"));

export default defineConfig({
  testDir: "./e2e",
  fullyParallel: false,
  workers: 1, // one shared bot; tests reset its data when they need to
  retries: 0,
  timeout: 45_000,
  expect: { timeout: 10_000 },
  reporter: [["list"]],
  use: {
    baseURL: external ?? `http://127.0.0.1:${port}`,
    trace: "retain-on-failure",
    ...devices["Desktop Chrome"],
    viewport: { width: 1440, height: 900 },
  },
  projects: [
    { name: "panel", testIgnore: /screenshots\.spec\.ts/ },
    { name: "screenshots", testMatch: /screenshots\.spec\.ts/ },
  ],
  webServer: external
    ? undefined
    : {
        command: `"${python}" "${demoServer}" --port ${port} --scenario empty`,
        url: `http://127.0.0.1:${port}/api/health/`,
        reuseExistingServer: false,
        timeout: 60_000,
        stdout: "ignore",
        stderr: "pipe",
      },
});
