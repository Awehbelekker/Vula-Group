import React from "react";
import ReactDOM from "react-dom/client";
import "./index.css";
import App from "./App";
import { installAuthFetch } from "./lib/authFetch";

// Attach the signed-in user's JWT to every guarded Vula API call (tenant-scoped auth).
installAuthFetch();

// Self-heal stale deployments: after a new deploy, an already-open tab still references old
// lazy-chunk filenames that no longer exist (Vercel returns index.html → module MIME error).
// Vite fires this event on chunk-load failure — one reload picks up the fresh build.
window.addEventListener("vite:preloadError", (e) => {
  e.preventDefault();
  window.location.reload();
});

ReactDOM.createRoot(document.getElementById("root")).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>
);

// Register the PWA service worker (installable home-screen app, offline shell).
if ("serviceWorker" in navigator) {
  window.addEventListener("load", () => {
    navigator.serviceWorker.register("/sw.js").catch(() => {});
  });
}
