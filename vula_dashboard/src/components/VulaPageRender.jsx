/**
 * VulaPageRender.jsx — public renderer for a tenant's Puck page. Mounted (pre-auth) on the hash
 * route #/page/:tenant/:slug, so EVERY tenant's published pages are viewable at a Vula URL even if
 * their storefront doesn't ship a renderer. Uses the same shared config as the editor → no drift.
 *
 * Brand + SEO (2026-07-17): sets the --brand-* CSS vars the Puck blocks read (they previously
 * NEVER received tenant brand here and always fell back to hardcoded teal), and applies the
 * page's stored `seo` jsonb to document.title/meta description.
 */
import { useState, useEffect } from "react";
import { Render } from "@measured/puck";
import "@measured/puck/puck.css";
import { config, VULA_PUCK_STYLES } from "../puck/config";
import { getTenantTheme } from "../theme/tenantThemes";
import { FONT_PAIRINGS } from "../theme/tokens";

const VULA_API = import.meta.env.VITE_API_URL || "https://vula-group-production.up.railway.app";
const centre = { padding: 48, fontFamily: "system-ui", textAlign: "center", color: "#666" };

export default function VulaPageRender({ tenant, slug }) {
  const [page, setPage] = useState(undefined);   // undefined = loading, null = not found
  const [brand, setBrand] = useState(() => getTenantTheme(tenant));

  useEffect(() => {
    // Live product blocks read these globals to fetch the right tenant's catalog.
    window.__VULA_PAGE_TENANT = tenant;
    window.__VULA_API = VULA_API;
    fetch(`${VULA_API}/v1/commerce/${tenant}/pages/${slug}`)
      .then((r) => (r.ok ? r.json() : Promise.reject(r.status)))
      .then(setPage).catch(() => setPage(null));
    // Live brand kit (P3 deepening) — the SAME commerce_invoice_settings row the dashboard's
    // Brand Kit card writes. Public + non-secret, so this unauthenticated page can read it
    // directly (previously read a separate, never-updated tenant_config.theme field).
    fetch(`${VULA_API}/v1/commerce/${tenant}/brand`)
      .then((r) => r.json())
      .then((b) => {
        setBrand((prev) => ({
          ...prev,
          accent: b.accent_color || prev.accent,
          ink: b.ink_color || prev.ink,
          logoUrl: b.logo_url || prev.logoUrl,
          fontPairing: b.font_pairing || null,
        }));
        if (b.font_pairing && FONT_PAIRINGS[b.font_pairing] && b.font_pairing !== "vula") {
          const p = FONT_PAIRINGS[b.font_pairing];
          const id = `font-pairing-${b.font_pairing}`;
          if (!document.getElementById(id)) {
            const link = document.createElement("link");
            link.id = id; link.rel = "stylesheet";
            link.href = `https://fonts.googleapis.com/css2?family=${p.family.replace(/ /g, "+")}:wght@${p.weights}&display=swap`;
            document.head.appendChild(link);
          }
        }
      })
      .catch(() => {});
  }, [tenant, slug]);

  // SEO: apply the stored seo jsonb (title/description) once the page loads.
  useEffect(() => {
    if (!page) return;
    const seo = page.seo || {};
    document.title = seo.title || page.title || "Vula";
    if (seo.description) {
      let meta = document.querySelector('meta[name="description"]');
      if (!meta) { meta = document.createElement("meta"); meta.name = "description"; document.head.appendChild(meta); }
      meta.content = seo.description;
    }
  }, [page]);

  if (page === undefined) return <div style={centre}>Loading…</div>;
  if (page === null) return <div style={centre}>Page not found.</div>;

  const d = page.puck_data || {};
  const data = { content: Array.isArray(d.content) ? d.content : [], root: { props: (d.root && (d.root.props || d.root)) || {} } };
  const fontDef = (brand.fontPairing && FONT_PAIRINGS[brand.fontPairing]) || null;
  const brandVars = {
    "--brand-accent": brand.accent || "#2C5545",
    "--brand-accent-fg": "#FFFFFF",
    "--brand-ink": brand.ink || "#1E1E1E",
    ...(fontDef ? { "--brand-font-display": `'${fontDef.family}', ${fontDef.fallback}` } : {}),
  };
  return (
    <div style={{ minHeight: "100vh", background: "#fff", ...brandVars }} data-vula-tenant={tenant}>
      <style>{VULA_PUCK_STYLES}</style>
      <Render config={config} data={data} />
    </div>
  );
}
