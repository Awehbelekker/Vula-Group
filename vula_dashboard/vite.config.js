import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
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
