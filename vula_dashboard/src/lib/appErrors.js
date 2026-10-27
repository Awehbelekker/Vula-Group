/**
 * appErrors.js — the dashboard tells Vula when it breaks on someone's device (2026-10-27).
 *
 * A screen that crashed on a customer's phone used to be invisible unless they told Ian. Now a
 * render crash (ErrorBoundary), an uncaught error and an unhandled promise rejection from Vula's
 * own code are sent to POST /v1/app-errors and listed in Master › Health (vula_mind/vula/app_errors.py).
 *
 * POPIA: nothing a person typed. What's sent is the error's message — with emails and numbers taken
 * out here and again on the server — its stack (the server keeps only Vula's file:line), the build,
 * the screen's id and the business id (kept only if the session proves it). At most 10 a visit,
 * each distinct error once; nothing while offline; nothing from browser extensions or other sites.
 */
import { VULA_API } from "./authFetch";

const MAX_PER_VISIT = 10;
const sent = new Set();
let context = { tenantId: null, page: "" };

/** App.jsx keeps this current: the business being used and the screen that's open. */
export function setErrorContext(next) {
  context = { ...context, ...next };
}

const strip = (s) => String(s || "")
  .replace(/[\w.+-]+@[\w-]+(\.[\w-]+)+/g, "[email]")
  .replace(/\d[\d\s-]{3,}\d|\d{4,}/g, "[n]")
  .slice(0, 500);

// Noise that isn't Vula's to fix: the browser's own layout warning, a script from another site
// (no stack we can read), a browser extension, and a dropped connection.
const NOISE = /ResizeObserver loop|^Script error\.?$|Failed to fetch|NetworkError|Load failed|The operation was aborted/i;
const ours = (stack) => !stack || /\/(assets|src)\//.test(stack) && !/(chrome|moz|safari)-extension:\/\//.test(stack.split("\n")[1] || "");

/** Report one error. Never throws, never blocks the page. */
export function reportError(error, { source = "error" } = {}) {
  try {
    const message = strip(error?.message || error);
    const stack = String(error?.stack || "").slice(0, 6000);
    if (!message || NOISE.test(message) || !ours(stack)) return false;
    if (typeof navigator !== "undefined" && navigator.onLine === false) return false;
    const key = `${message}|${stack.split("\n")[1] || ""}`;
    if (sent.has(key) || sent.size >= MAX_PER_VISIT) return false;
    sent.add(key);
    const body = JSON.stringify({
      message: source === "render" ? message : `${source}: ${message}`, stack,
      build: typeof __BUILD_ID__ !== "undefined" ? __BUILD_ID__ : "",
      page: String(context.page || "").slice(0, 60), tenant_id: context.tenantId || null,
    });
    fetch(`${VULA_API}/v1/app-errors`, { method: "POST", headers: { "Content-Type": "application/json" }, body, keepalive: true })
      .catch(() => {});
    return true;
  } catch {
    return false;
  }
}

/** Once, from main.jsx: uncaught errors and unhandled rejections anywhere in the app. */
export function listenForErrors() {
  if (typeof window === "undefined" || window.__vulaErrorsListening) return;
  window.__vulaErrorsListening = true;
  window.addEventListener("error", (e) => reportError(e.error || e.message, { source: "uncaught" }));
  window.addEventListener("unhandledrejection", (e) => reportError(e.reason, { source: "unhandled" }));
}
