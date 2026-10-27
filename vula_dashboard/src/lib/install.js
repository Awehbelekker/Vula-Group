/**
 * install.js — putting Vula on the phone's home screen (2026-10-27, PWA pass 2).
 *
 * Chrome / Edge / Samsung Internet offer their own install pop-up through a
 * `beforeinstallprompt` event that fires once, soon after the page loads — so it is caught here at
 * import time (main.jsx imports this first) and kept until someone taps "Install Vula". iPhones
 * have no such event: there the app shows the Share → Add to Home Screen steps instead
 * (components/install/InstallVula.jsx). Nothing here is sent anywhere.
 */
import { useEffect, useReducer } from "react";

let offer = null;            // the browser's deferred install prompt, when it gave one
let justInstalled = false;
const listeners = new Set();
const changed = () => listeners.forEach((f) => f());

if (typeof window !== "undefined") {
  window.addEventListener("beforeinstallprompt", (e) => {
    e.preventDefault();      // our own button instead of the browser's mini bar
    offer = e;
    changed();
  });
  window.addEventListener("appinstalled", () => {
    offer = null;
    justInstalled = true;
    changed();
  });
}

/** Running from the home screen (Android/desktop "standalone", or iOS's navigator.standalone). */
export function isInstalled() {
  if (justInstalled) return true;
  try {
    return window.matchMedia("(display-mode: standalone)").matches || window.navigator.standalone === true;
  } catch {
    return false;
  }
}

/** "ios" (iPhone / iPad, incl. iPadOS's desktop user agent), "android", or "desktop". */
export function platform() {
  const ua = navigator.userAgent || "";
  if (/iphone|ipad|ipod/i.test(ua) || (/macintosh/i.test(ua) && navigator.maxTouchPoints > 1)) return "ios";
  if (/android/i.test(ua)) return "android";
  return "desktop";
}

const DISMISS_KEY = "vula.install.dismissed";
const DISMISS_DAYS = 30;

/** The Home card was waved away on this phone in the last 30 days. A device setting, kept on sign-out. */
export function dismissedRecently() {
  try {
    const at = Number(localStorage.getItem(DISMISS_KEY) || 0);
    return at > 0 && Date.now() - at < DISMISS_DAYS * 86400000;
  } catch {
    return false;
  }
}

export function dismiss() {
  try { localStorage.setItem(DISMISS_KEY, String(Date.now())); } catch { /* private mode */ }
  changed();
}

/** Re-renders when the browser offers or completes an install. */
export function useInstall() {
  const [, refresh] = useReducer((n) => n + 1, 0);
  useEffect(() => {
    listeners.add(refresh);
    return () => listeners.delete(refresh);
  }, []);
  return {
    installed: isInstalled(),
    canPrompt: Boolean(offer),
    platform: platform(),
    dismissed: dismissedRecently(),
    dismiss,
    /** The browser's own install pop-up: "accepted" | "dismissed" | "unavailable". */
    async prompt() {
      if (!offer) return "unavailable";
      const e = offer;
      offer = null;          // a prompt can only be shown once
      changed();
      try {
        await e.prompt();
        const choice = await e.userChoice;
        return choice?.outcome || "dismissed";
      } catch {
        return "unavailable";
      }
    },
  };
}
