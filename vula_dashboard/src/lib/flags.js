/**
 * flags.js — the dashboard's look. The redesign became the only design on 5 Nov 2026 (the old
 * look and its per-business switch, vula_tenant_config.ui_v2, are gone); index.css keys its
 * tokens off <html data-ui="v2">, which App.jsx sets on every page except the public
 * customer-facing ones.
 */
export function applyUiMode(on) {
  if (typeof document === "undefined") return;
  if (on) document.documentElement.dataset.ui = "v2";
  else delete document.documentElement.dataset.ui;
}
