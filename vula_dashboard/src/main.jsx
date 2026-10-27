import React from "react";
import ReactDOM from "react-dom/client";
import "./index.css";
import App from "./App";
import ErrorBoundary from "./components/ErrorBoundary";
import { OfflineBanner, UiHost, toast } from "./components/ui/index.jsx";
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
    <ErrorBoundary>
      <App />
      <UiHost />
      <OfflineBanner />
    </ErrorBoundary>
  </React.StrictMode>
);

// Register the PWA service worker (installable home-screen app, offline shell). 2026-10-27: the
// build id in its address makes every deploy install a fresh worker, so phones don't keep running
// an old app; when one takes over an open app, say so and offer a reload.
if ("serviceWorker" in navigator) {
  const hadWorker = Boolean(navigator.serviceWorker.controller);
  let told = false;
  navigator.serviceWorker.addEventListener("controllerchange", () => {
    if (!hadWorker || told) return;          // first install: nothing was out of date
    told = true;
    toast("Vula has been updated.", "ok", { action: { label: "Reload", onClick: () => window.location.reload() } });
  });
  window.addEventListener("load", () => {
    navigator.serviceWorker.register(`/sw.js?v=${encodeURIComponent(__BUILD_ID__)}`).catch(() => {});
  });
}
