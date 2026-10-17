// playwright.config.js — visual baseline + accessibility checks for the UX redesign (2026-10-17).
// Runs the Vite dev server against a fake API host; tests/visual/mockApi.js answers every call
// from seeded demo data, so no screenshot ever contains production data.
import { defineConfig } from "@playwright/test";

const PORT = 4179;
export default defineConfig({
  testDir: "tests/visual",
  outputDir: "tests/visual/.results",
  fullyParallel: true,
  workers: 4,
  reporter: [["list"]],
  use: {
    baseURL: `http://localhost:${PORT}`,
    launchOptions: process.env.PW_CHROMIUM_PATH ? { executablePath: process.env.PW_CHROMIUM_PATH } : {},
    timezoneId: "Africa/Johannesburg",
    locale: "en-ZA",
    // The app registers /sw.js; a service worker would answer fetches itself and bypass
    // page.route(), so the mock API (tests/visual/mockApi.js) could miss calls.
    serviceWorkers: "block",
  },
  webServer: {
    command: `npx vite --port ${PORT} --strictPort`,
    port: PORT,
    reuseExistingServer: true,
    env: {
      VITE_API_URL: "http://api.vula.test",
      VITE_SUPABASE_URL: "http://supabase.vula.test",
      VITE_SUPABASE_ANON_KEY: "test-anon-key",
    },
  },
});
