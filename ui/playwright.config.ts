import { defineConfig } from "@playwright/test";
export default defineConfig({
  testDir: "./tests",
  fullyParallel: false,
  timeout: 30000,
  use: {
    baseURL: "http://127.0.0.1:18875",
    viewport: { width: 1440, height: 1000 },
    trace: "retain-on-failure",
  },
  webServer: {
    command: "npm run dev -- --port 18875 --strictPort",
    url: "http://127.0.0.1:18875",
    reuseExistingServer: false,
  },
});
