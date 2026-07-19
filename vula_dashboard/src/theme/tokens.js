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
  ok: "var(--ok)", warn: "var(--warn)", danger: "var(--danger)", info: "var(--info)",
  display: "var(--font-display)",
  body: "var(--font-body)",
  mono: "var(--font-mono)",
  rCard: "var(--r-card)", rInput: "var(--r-input)", rPill: "var(--r-pill)",
  shadowSm: "var(--shadow-sm)", shadowMd: "var(--shadow-md)", shadowLg: "var(--shadow-lg)",
};

/** Apply a tenant accent to the whole portal by setting the CSS variables on :root. */
export function applyAccent(accent) {
  if (!accent || typeof document === "undefined") return;
  const root = document.documentElement;
  root.style.setProperty("--accent", accent);
  root.style.setProperty("--accent-dark", shade(accent, -0.18));
  root.style.setProperty("--accent-soft", hexToRgba(accent, 0.1));
  root.style.setProperty("--on-accent", contrastOn(accent));
}

/** Apply a tenant's ink (heading/body text) colour — part of the native brand kit (P3). */
export function applyInk(ink) {
  if (!ink || typeof document === "undefined") return;
  document.documentElement.style.setProperty("--ink", ink);
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
