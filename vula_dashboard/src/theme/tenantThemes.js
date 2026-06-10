/**
 * tenantThemes.js — per-tenant white-label theming.
 *
 * The portal keeps Vula's design system (Cormorant serif, cream surfaces) but
 * each tenant carries their own logo + accent colour so the admin feels like
 * *their* system — "Powered by Vula". Resolved at login from the tenantId.
 *
 * Applied as CSS custom properties on the portal root (see App.jsx), so
 * components reference var(--accent) instead of a hardcoded colour. Add a
 * tenant here (or, later, load from a DB) to brand their portal.
 */

export const VULA_DEFAULT = {
  name: "Vula Commerce",
  accent: "#2C5545",      // Vula green
  accentDark: "#234436",
  accentSoft: "rgba(44,85,69,0.10)",
  ink: "#1E1E1E",
  logoUrl: null,          // null → render the name as wordmark
  tagline: "",
};

export const TENANT_THEMES = {
  "off-the-hook": {
    name: "Off the Hook",
    accent: "#2DAAB5",                      // brand teal
    accentDark: "#1F8B95",
    accentSoft: "rgba(45,170,181,0.12)",
    ink: "#0E2D4D",                         // deep navy
    logoUrl: "https://offthehook.co.za/images/logo.svg",
    tagline: "Quality food delivered to your door",
  },
  "awake-sa": {
    name: "Awake South Africa",
    accent: "#0EA5E9",
    accentDark: "#0284C7",
    accentSoft: "rgba(14,165,233,0.12)",
    ink: "#0B2545",
    logoUrl: null,
    tagline: "Premium eFoil distribution",
  },
  "digg-demo": {
    name: "DIGG Architecture",
    accent: "#1E1E1E",                       // architectural charcoal (placeholder)
    accentDark: "#000000",
    accentSoft: "rgba(30,30,30,0.08)",
    ink: "#1E1E1E",
    logoUrl: null,                           // TODO: DIGG logo URL
    tagline: "Architecture & spatial design",
  },
};

/**
 * Resolve a tenant id from the login hostname. Matches a known brand token
 * anywhere in the host, so it works for BOTH the client's own domain and the
 * vula-ai.com fallback:
 *   admin.offthehook.co.za / offthehook.vula-ai.com → "off-the-hook"
 *   admin.digg-ct.co.za     / digg.vula-ai.com       → "digg-demo"
 * Returns null (generic Vula login) for the apex / master / vercel host.
 */
const HOST_TENANT_MATCHERS = [
  { tokens: ["offthehook", "off-the-hook"], tenant: "off-the-hook" },
  { tokens: ["digg"], tenant: "digg-demo" },
  { tokens: ["awake"], tenant: "awake-sa" },
];

export function resolveTenantFromHost(hostname = window.location.hostname) {
  const h = hostname.toLowerCase();
  for (const m of HOST_TENANT_MATCHERS) {
    if (m.tokens.some((tok) => h.includes(tok))) return m.tenant;
  }
  return null; // vula-ai.com apex, vuladashboard.vercel.app, localhost → generic
}

/** Resolve a tenant's theme, falling back to the Vula default. */
export function getTenantTheme(tenantId) {
  return TENANT_THEMES[tenantId] || VULA_DEFAULT;
}

/** CSS custom properties to spread onto the portal root element. */
export function themeVars(theme) {
  return {
    "--accent": theme.accent,
    "--accent-dark": theme.accentDark,
    "--accent-soft": theme.accentSoft,
    "--ink": theme.ink,
  };
}
