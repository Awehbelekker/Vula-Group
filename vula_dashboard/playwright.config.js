// playwright.config.js — visual baseline + accessibility checks for the UX redesign (2026-10-17).
// Runs the Vite dev server against a fake API host; tests/visual/mockApi.js answers every call
// from seeded demo data, so no screenshot ever contains production data.
import { defineConfig } from "@playwright/test";

// The redesign is the dashboard (2026-11-05): every run is the redesign. Specs still read
// UI_V2 to skip the old look's tests until those are deleted with it.
process.env.UI_V2 = "1";

// A service worker's own requests go through context.route() (pwa.spec.js hands the worker a
// build's file list); Chromium-only, and off unless asked for. Specs that block service workers
// are unaffected.
process.env.PW_EXPERIMENTAL_SERVICE_WORKER_NETWORK_EVENTS ??= "1";
const PORT = 4180;
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
