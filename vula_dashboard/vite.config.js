import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import { precacheList } from "./precache.js";

// Names this build for the service worker (src/main.jsx registers /sw.js?v=<id>): a new id on
// every deploy is what makes phones install the new worker instead of running a stale app.
const BUILD_ID = (process.env.VERCEL_GIT_COMMIT_SHA || "").slice(0, 12) || Date.now().toString(36);

export default defineConfig({
  plugins: [react(), precacheList(BUILD_ID)],
  define: { __BUILD_ID__: JSON.stringify(BUILD_ID) },
  build: {
    // three pages: the dashboard (/), the coach app (/pay/, own manifest + service worker) and the customer receipt (/r/<token>)
    rollupOptions: { input: { main: "index.html", pay: "pay/index.html", receipt: "r/index.html" } },
  },
  // Screens load when opened (src/lib/lazyScreen.jsx), so the dev server would only find the
  // packages they use the first time one opens — then re-bundle and reload the page under you
  // (and under the browser tests). Scan every source file up front instead.
  optimizeDeps: { entries: ["index.html", "src/**/*.{js,jsx}"] },
  server: {
    port: 3000,
    proxy: {
      "/api": {
        target: "http://localhost:7438",
        rewrite: (path) => path.replace(/^\/api/, ""),
      },
    },
  },
});
