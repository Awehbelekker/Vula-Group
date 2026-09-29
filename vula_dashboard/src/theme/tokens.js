/**
 * tokens.js — JS handles for the design tokens defined in index.css.
 * Use these in inline styles so every component shares one language and the
 * per-tenant --accent flows through automatically.
 */
export const T = {
  accent: "var(--accent)",
  accentDark: "var(--accent-dark)",
  accentSoft: "var(--accent-soft)",
  onAccent: "var(--on-accent)",
  bg: "var(--bg)",
  surface: "var(--surface)",
  surfaceAlt: "var(--surface-alt)",
  border: "var(--border)",
  borderSoft: "var(--border-soft)",
  ink: "var(--ink)",
  text: "var(--text)",
  muted: "var(--muted)",
  accent2: "var(--accent-2)",
  ok: "var(--ok)", warn: "var(--warn)", danger: "var(--danger)", info: "var(--info)",
  display: "var(--font-display)",
  body: "var(--font-body)",
  mono: "var(--font-mono)",
  rCard: "var(--r-card)", rInput: "var(--r-input)", rPill: "var(--r-pill)",
  shadowSm: "var(--shadow-sm)", shadowMd: "var(--shadow-md)", shadowLg: "var(--shadow-lg)",
};

/* ── The tenant's brand → the whole dashboard (2026-09-29) ─────────────────────────────────
 * A brand is set as --brand-* variables on :root; index.css maps them to the design tokens
 * (--accent, --accent-2, --ink…) separately for light and dark mode. Setting --accent directly
 * (the old way) pinned one colour for both modes and could be overridden per element — which is
 * exactly how a hardcoded theme wrapper hid every tenant's saved brand kit. */

/** Apply a tenant accent: light-mode values, plus a dark-mode variant lifted until it reads on
 * a dark surface (a charcoal or navy brand would otherwise vanish in dark mode). */
export function applyAccent(accent) {
  if (!accent || typeof document === "undefined") return;
  const root = document.documentElement;
  const onDark = liftForDark(accent);
  const set = (k, v) => root.style.setProperty(k, v);
  set("--brand-accent", accent);
  set("--brand-accent-dark", shade(accent, -0.18));
  set("--brand-accent-soft", hexToRgba(accent, 0.1));
  set("--brand-on-accent", contrastOn(accent));
  set("--brand-accent-dm", onDark);
  set("--brand-accent-dark-dm", shade(onDark, 0.12));
  set("--brand-accent-soft-dm", hexToRgba(onDark, 0.16));
  set("--brand-on-accent-dm", contrastOn(onDark));
}

/** A second brand colour (charts, highlights, the Home glow). Unset → derived from the accent. */
export function applySecondary(color) {
  if (typeof document === "undefined") return;
  const root = document.documentElement;
  if (!color) { root.style.removeProperty("--brand-accent-2"); root.style.removeProperty("--brand-accent-2-dm"); return; }
  root.style.setProperty("--brand-accent-2", color);
  root.style.setProperty("--brand-accent-2-dm", liftForDark(color));
}

/** A tenant's ink (headings) — light mode only; dark mode always uses light ink. */
export function applyInk(ink) {
  if (!ink || typeof document === "undefined") return;
  document.documentElement.style.setProperty("--brand-ink", ink);
}

export const CORNER_STYLES = { rounded: "Rounded", soft: "Soft", sharp: "Sharp" };
export const DENSITIES = { comfortable: "Comfortable", compact: "Compact" };
// One logo size for every surface: this dashboard, the login page, invoices, emails, the
// WhatsApp menu page and the phone app icon (backend: vula/commerce/brand_sizes.py).
export const LOGO_SIZES = { sm: "Small", md: "Medium", lg: "Large", xl: "Extra large" };

/** Everything in one call — the dashboard, login, Brand kit preview and hosted pages use this. */
export function applyBrand(brand = {}) {
  if (typeof document === "undefined") return;
  const hex = (c) => (c && /^#?[0-9a-fA-F]{3,8}$/.test(c) ? (c.startsWith("#") ? c : `#${c}`) : null);
  const root = document.documentElement;
  const accent = hex(brand.accent_color) || "#2C5545";
  applyAccent(accent);
  applySecondary(hex(brand.secondary_color));
  applyInk(hex(brand.ink_color) || "#1E1E1E");
  applyFontPairing(brand.font_pairing);
  root.dataset.corners = CORNER_STYLES[brand.corner_style] ? brand.corner_style : "rounded";
  root.dataset.density = DENSITIES[brand.density] ? brand.density : "comfortable";
  root.dataset.logoSize = LOGO_SIZES[brand.logo_size] ? brand.logo_size : "md";
  let meta = document.querySelector('meta[name="theme-color"]');
  if (!meta) { meta = document.createElement("meta"); meta.name = "theme-color"; document.head.appendChild(meta); }
  meta.content = accent;
  const icon = brand.icon_url || brand.logo_url;
  if (icon) {
    let link = document.querySelector('link[rel="icon"]');
    if (!link) { link = document.createElement("link"); link.rel = "icon"; document.head.appendChild(link); }
    link.href = icon;
  }
}

/* ── Light / dark mode: Auto follows the phone; a person can pin Light or Dark ──────────── */
// On since every dashboard page draws from the tokens (2026-09-29: 50 of 57 pages audited clean
// in dark mode in Chromium; the rest were fake-data renders). Flip to false to pin light.
export const DARK_MODE_READY = true;
const MODE_KEY = "vula-theme-mode";
export function getThemeMode() {
  try { return localStorage.getItem(MODE_KEY) || "auto"; } catch { return "auto"; }
}
export function setThemeMode(mode) {
  const m = ["auto", "light", "dark"].includes(mode) ? mode : "auto";
  try { localStorage.setItem(MODE_KEY, m); } catch { /* private mode: this visit only */ }
  applyThemeMode(m);
}
export function applyThemeMode(mode = getThemeMode()) {
  if (typeof document === "undefined") return;
  const root = document.documentElement;
  if (!DARK_MODE_READY) { root.dataset.theme = "light"; return; }
  if (mode === "light" || mode === "dark") root.dataset.theme = mode;
  else delete root.dataset.theme;
}

// Curated display-font pairings — body stays Inter (already loaded) so only ONE extra Google
// Fonts family needs fetching per pairing, never all of them for every tenant.
export const FONT_PAIRINGS = {
  vula:      { label: "Vula (Cormorant Garamond)", family: "Cormorant Garamond", weights: "500;600;700", fallback: "Georgia, serif" },
  modern:    { label: "Modern (Poppins)",           family: "Poppins",           weights: "500;600;700", fallback: "system-ui, sans-serif" },
  editorial: { label: "Editorial (Playfair Display)", family: "Playfair Display", weights: "500;600;700", fallback: "Georgia, serif" },
  classic:   { label: "Classic (Merriweather)",     family: "Merriweather",      weights: "500;600;700", fallback: "Georgia, serif" },
};

/** Load (once) and apply a tenant's chosen display-font pairing. Falls back silently to the
 * default Vula pairing (already loaded by index.css) for an unknown/blank key. */
export function applyFontPairing(key) {
  if (typeof document === "undefined") return;
  const p = FONT_PAIRINGS[key] || FONT_PAIRINGS.vula;
  if (p !== FONT_PAIRINGS.vula) {
    const id = `font-pairing-${key}`;
    if (!document.getElementById(id)) {
      const link = document.createElement("link");
      link.id = id; link.rel = "stylesheet";
      link.href = `https://fonts.googleapis.com/css2?family=${p.family.replace(/ /g, "+")}:wght@${p.weights}&display=swap`;
      document.head.appendChild(link);
    }
  }
  document.documentElement.style.setProperty("--font-display", `'${p.family}', ${p.fallback}`);
}

function clamp(n) { return Math.max(0, Math.min(255, Math.round(n))); }
function parseHex(hex) {
  const h = (hex || "").replace("#", "");
  if (h.length === 3) return [0, 1, 2].map((i) => parseInt(h[i] + h[i], 16));
  if (h.length >= 6) return [0, 2, 4].map((i) => parseInt(h.slice(i, i + 2), 16));
  return null;
}
export function shade(hex, amt) {
  const rgb = parseHex(hex); if (!rgb) return hex;
  const [r, g, b] = rgb.map((c) => clamp(c + c * amt));
  return "#" + [r, g, b].map((c) => clamp(c).toString(16).padStart(2, "0")).join("");
}
export function hexToRgba(hex, a) {
  const rgb = parseHex(hex); if (!rgb) return hex;
  return `rgba(${rgb[0]}, ${rgb[1]}, ${rgb[2]}, ${a})`;
}
function contrastOn(hex) {
  const rgb = parseHex(hex); if (!rgb) return "#FFFFFF";
  const lum = (0.299 * rgb[0] + 0.587 * rgb[1] + 0.114 * rgb[2]) / 255;
  return lum > 0.62 ? "#1E1E1E" : "#FFFFFF";
}
function relLum(rgb) {
  const [r, g, b] = rgb.map((c) => { const v = c / 255; return v <= 0.03928 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4; });
  return 0.2126 * r + 0.7152 * g + 0.0722 * b;
}
/** Contrast ratio of two hex colours (WCAG). */
export function contrastRatio(a, b) {
  const x = parseHex(a), y = parseHex(b); if (!x || !y) return 21;
  const [l1, l2] = [relLum(x), relLum(y)].sort((p, q) => q - p);
  return (l1 + 0.05) / (l2 + 0.05);
}
/** Lighten a colour until it reaches 4.5:1 on the dark surface (#1C1C1A), keeping its hue. */
export function liftForDark(hex, surface = "#1C1C1A") {
  let c = hex;
  for (let i = 0; i < 12 && contrastRatio(c, surface) < 4.5; i++) {
    const rgb = parseHex(c); if (!rgb) return hex;
    c = "#" + rgb.map((v) => clamp(v + (255 - v) * 0.18).toString(16).padStart(2, "0")).join("");
  }
  return c;
}
