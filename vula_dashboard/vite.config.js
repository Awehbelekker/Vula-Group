import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Names this build for the service worker (src/main.jsx registers /sw.js?v=<id>): a new id on
// every deploy is what makes phones install the new worker instead of running a stale app.
const BUILD_ID = (process.env.VERCEL_GIT_COMMIT_SHA || "").slice(0, 12) || Date.now().toString(36);

export default defineConfig({
  plugins: [react()],
  define: { __BUILD_ID__: JSON.stringify(BUILD_ID) },
  build: {
    // three pages: the dashboard (/), the coach app (/pay/, own manifest + service worker) and the customer receipt (/r/<token>)
    rollupOptions: { input: { main: "index.html", pay: "pay/index.html", receipt: "r/index.html" } },
  },
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
