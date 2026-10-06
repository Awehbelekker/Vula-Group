import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  build: {
    // two pages: the dashboard (/) and the coach app (/pay/, its own manifest + service worker)
    rollupOptions: { input: { main: "index.html", pay: "pay/index.html" } },
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
