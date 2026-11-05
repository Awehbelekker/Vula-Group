/**
 * flags.js — the redesign is the dashboard (2026-11-05: every business was on it, so the old
 * look is being retired).
 *
 * The per-business switch (vula_tenant_config.ui_v2) and the build switch (VITE_UI_V2) no longer
 * decide anything. `?ui=v1` in the address bar still pins the old look on this device, as an
 * escape hatch while the old screens are removed — `?ui=auto` (or `?ui=v2`) undoes it.
 * The look itself is index.css's [data-ui="v2"] tokens.
 */
const KEY = "vula.ui";

function readOverride() {
  try {
    const q = new URLSearchParams(window.location.search).get("ui");
    if (q === "v1") localStorage.setItem(KEY, q);
    if (q === "auto" || q === "v2") localStorage.removeItem(KEY);
    return localStorage.getItem(KEY);
  } catch { return null; }
}

/** True unless this device pinned the old look with ?ui=v1. */
export function uiV2() {
  if (typeof window === "undefined") return true;
  return readOverride() !== "v1";
}

/** Put the choice on <html> (index.css keys every redesign token off data-ui="v2"). */
export function applyUiMode(on) {
  if (typeof document === "undefined") return;
  if (on) document.documentElement.dataset.ui = "v2";
  else delete document.documentElement.dataset.ui;
}
