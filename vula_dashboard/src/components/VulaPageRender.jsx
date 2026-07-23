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
          name: b.name || prev.name,
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

  // SEO: title/description, canonical URL, Open Graph + Twitter Card (drives the rich
  // preview WhatsApp/Facebook/Twitter show when a page link is shared — the single
  // highest-leverage piece of "SEO" for a WhatsApp-first platform), and JSON-LD
  // Organization data using the same brand fetch above. All client-side DOM mutation —
  // there's no server-side rendering here, so this only helps crawlers that execute JS
  // (Google/WhatsApp do; some don't), not a substitute for SSR.
  useEffect(() => {
    if (!page) return;
    const seo = page.seo || {};
    const title = seo.title || page.title || "Vula";
    const description = seo.description || "";
    const image = seo.image || brand.logoUrl || "";
    const url = window.location.href;
    document.title = title;

    const setMeta = (selector, attrs) => {
      let el = document.querySelector(selector);
      if (!el) {
        el = document.createElement(attrs.tag || "meta");
        delete attrs.tag;
        Object.entries(attrs).forEach(([k, v]) => el.setAttribute(k, v));
        document.head.appendChild(el);
      }
      return el;
    };

    if (description) setMeta('meta[name="description"]', { name: "description" }).setAttribute("content", description);
    setMeta('link[rel="canonical"]', { tag: "link", rel: "canonical" }).setAttribute("href", url);
    setMeta('meta[property="og:title"]', { property: "og:title" }).setAttribute("content", title);
    setMeta('meta[property="og:type"]', { property: "og:type" }).setAttribute("content", "website");
    setMeta('meta[property="og:url"]', { property: "og:url" }).setAttribute("content", url);
    if (description) setMeta('meta[property="og:description"]', { property: "og:description" }).setAttribute("content", description);
    if (image) {
      setMeta('meta[property="og:image"]', { property: "og:image" }).setAttribute("content", image);
      setMeta('meta[name="twitter:card"]', { name: "twitter:card" }).setAttribute("content", "summary_large_image");
      setMeta('meta[name="twitter:image"]', { name: "twitter:image" }).setAttribute("content", image);
    }

    // JSON-LD Organization block — id'd so it's replaced, not duplicated, on slug changes.
    let ld = document.getElementById("vula-jsonld-org");
    if (!ld) {
      ld = document.createElement("script");
      ld.id = "vula-jsonld-org";
      ld.type = "application/ld+json";
      document.head.appendChild(ld);
    }
    ld.textContent = JSON.stringify({
      "@context": "https://schema.org", "@type": "Organization",
      name: brand.name || tenant, ...(brand.logoUrl ? { logo: brand.logoUrl } : {}), url,
    });
  }, [page, brand, tenant]);

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
