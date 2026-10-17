/**
 * flags.js — whether this person sees the redesigned dashboard (2026-10-17, UX redesign).
 *
 * Two switches, both needed:
 *   VITE_UI_V2=true            the build carries the new look at all (Vercel env var)
 *   vula_tenant_config.ui_v2   this business has been switched onto it (migration 215,
 *                              returned by GET /v1/tenants/{t}); opt-in per tenant
 * When the build has it, `?ui=v2` / `?ui=v1` in the address bar pins a preview for this device
 * (Ian checking a tenant before switching it on) — remembered until `?ui=auto`.
 * The new look itself is index.css's [data-ui="v2"] tokens, so turning this off is instant.
 */
export const UI_V2_BUILD = import.meta.env.VITE_UI_V2 === "true";
const KEY = "vula.ui";

function readOverride() {
  try {
    const q = new URLSearchParams(window.location.search).get("ui");
    if (q === "v2" || q === "v1") localStorage.setItem(KEY, q);
    if (q === "auto") localStorage.removeItem(KEY);
    return localStorage.getItem(KEY);
  } catch { return null; }
}

/** True when this person should get the redesign; `tenantOn` is the tenant's ui_v2. */
export function uiV2For(tenantOn) {
  if (!UI_V2_BUILD || typeof window === "undefined") return false;
  const pinned = readOverride();
  if (pinned) return pinned === "v2";
  return !!tenantOn;
}

/** Put the choice on <html> (index.css keys every redesign token off data-ui="v2"). */
export function applyUiMode(on) {
  if (typeof document === "undefined") return;
  if (on) document.documentElement.dataset.ui = "v2";
  else delete document.documentElement.dataset.ui;
}
