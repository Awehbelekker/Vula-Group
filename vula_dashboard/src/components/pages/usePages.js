/**
 * usePages.js — the Puck page builder's logic (shared by VulaPages and PagesV2), moved verbatim:
 * the tenant's pages, open/create/save/publish, rename/duplicate/delete/reorder, "try a new
 * template" previews and making one live, version history, AI-draft (copy + a suggested theme,
 * applied only on an explicit click) and conversational refine. See VulaPages.jsx's comments.
 */
import { useState, useEffect, useRef } from "react";
import { VULA_API } from "../../lib/authFetch";
import { getTenantTheme } from "../../theme/tenantThemes";
import { FONT_PAIRINGS } from "../../theme/tokens";
import { confirmDialog, promptDialog } from "../ui/index.jsx";

// Every tenant gets a real, live, SSR'd storefront the instant they publish — no more "no
// custom domain configured, previewing a Vula-hosted page" — vula_storefront serves this
// subdomain for every tenant automatically. A tenant with a wired custom domain (storeUrl) still
// takes priority below; this is the always-real fallback, not just a preview.
export const STOREFRONT_ROOT_DOMAIN = import.meta.env.VITE_STOREFRONT_ROOT_DOMAIN || "vula.site";

export function norm(data) {
  const d = data && typeof data === "object" ? data : {};
  return {
    content: Array.isArray(d.content) ? d.content : [],
    root: { props: (d.root && (d.root.props || d.root)) || {} },
  };
}

// Preview viewport switcher (P4) — Puck renders its own Monitor/Tablet/Smartphone toolbar icons
// once given a list, no custom UI needed.
export const PREVIEW_VIEWPORTS = [
  { width: 1440, height: "auto", label: "Desktop", icon: "Monitor" },
  { width: 768, height: "auto", label: "Tablet", icon: "Tablet" },
  { width: 390, height: "auto", label: "Mobile", icon: "Smartphone" },
];

// Mirrors vula/commerce/page_copy.py's MOOD_PRESETS keys — a small, curated set the AI snaps
// to (never a freely-generated style), so a tenant's pick is always deterministic and reliable.
export const MOOD_PRESETS = [
  { key: "minimal", label: "Minimal" },
  { key: "warm_earthy", label: "Warm & earthy" },
  { key: "bold_energetic", label: "Bold & energetic" },
  { key: "classic_elegant", label: "Classic & elegant" },
  { key: "playful", label: "Playful & fun" },
];

// Mirrors page_copy.py's FEATURE_BLOCK_MAP — features a reference-URL analysis can offer to
// actually ADD as a new, real block (Booking is wired to the real bookings backend; FAQ/Pricing
// are content-only). Other recognized features (shop_grid/testimonials/gallery/contact_form)
// aren't offered here since Vula already has an equivalent block for those.
export const ADDABLE_FEATURES = { booking: "a booking calendar", faq: "an FAQ section", pricing: "a pricing table" };

export const UNSUPPORTED_FEATURE_LABELS = { blog: "a blog", login: "account/login", live_chat: "live chat", newsletter_signup: "newsletter signup" };

export const URL_RE = /https?:\/\/[^\s]+/gi;

// Starter templates — a page is never a blank scary canvas. Homepage templates are keyed by
// business_type (food|retail|services|trades|health|other, the same taxonomy _map_business_type
// already uses server-side — see tenants.py) so a tenant gets copy and blocks that actually fit
// their business, not fish-shop language regardless of what they sell (the bug this replaces:
// every tenant used to get "Fresh from the harbour" / FeaturedProducts / CategoryNav no matter
// what business_type they were).
export const TEMPLATES = {
  blank: { label: "Blank page", data: {} },
  home_food: {
    label: "Homepage — Food & drink",
    data: { content: [
      { type: "Hero", props: { id: "t-food-hero", title: "Fresh, local, delivered", subtitle: "Order before 10am for same-day delivery.", image: "", ctaText: "Shop now", ctaHref: "/shop" } },
      { type: "AnnouncementBar", props: { id: "t-food-ann", text: "Free delivery on orders over R500", linkText: "", href: "" } },
      { type: "FeaturedProducts", props: { id: "t-food-feat", title: "Today's picks", count: 4, linkBase: "/shop" } },
      { type: "CategoryNav", props: { id: "t-food-cats", title: "Browse by category", linkBase: "/shop" } },
      { type: "Features", props: { id: "t-food-why", title: "Why order with us", items: [
        { heading: "Fresh", body: "Sourced daily." }, { heading: "Local", body: "Proudly South African." }, { heading: "Delivered", body: "Straight to your door." }] } },
      { type: "Testimonials", props: { id: "t-food-test", title: "What customers say", items: [
        { quote: "Always fresh, always on time.", author: "A happy customer", role: "" }] } },
      { type: "WhatsAppCTA", props: { id: "t-food-wa", phone: "", message: "Hi! I'd like to order.", buttonText: "💬 Order on WhatsApp" } },
    ] },
  },
  home_retail: {
    label: "Homepage — Retail / shop",
    data: { content: [
      { type: "Hero", props: { id: "t-retail-hero", title: "Shop the range", subtitle: "Quality products, straight to your door.", image: "", ctaText: "Shop now", ctaHref: "/shop" } },
      { type: "ProductGrid", props: { id: "t-retail-grid", title: "Popular right now", category: "", count: 8, linkBase: "/shop" } },
      { type: "CategoryNav", props: { id: "t-retail-cats", title: "Browse by category", linkBase: "/shop" } },
      { type: "TwoColumns", props: { id: "t-retail-story", leftImage: "", leftHeading: "Our story", leftBody: "Tell customers who you are and why they should buy from you.", rightImage: "", rightHeading: "Quality you can trust", rightBody: "What makes your products different." } },
      { type: "Testimonials", props: { id: "t-retail-test", title: "What customers say", items: [
        { quote: "Great quality, great service.", author: "A happy customer", role: "" }] } },
      { type: "CTA", props: { id: "t-retail-cta", text: "Shop the full range", href: "/shop", variant: "solid" } },
    ] },
  },
  home_trades: {
    label: "Homepage — Trades & construction",
    data: { content: [
      { type: "Hero", props: { id: "t-trades-hero", title: "Built to last", subtitle: "Quality workmanship, on time, on budget.", image: "", ctaText: "Get a quote", ctaHref: "#contact" } },
      { type: "Features", props: { id: "t-trades-serv", title: "Our services", items: [
        { heading: "Service one", body: "Describe what you offer." }, { heading: "Service two", body: "Describe what you offer." }, { heading: "Service three", body: "Describe what you offer." }] } },
      { type: "TwoColumns", props: { id: "t-trades-work", leftImage: "", leftHeading: "Recent work", leftBody: "Show off a recent project — add a photo and describe the job.", rightImage: "", rightHeading: "Why choose us", rightBody: "Experience, reliability, and a job done properly." } },
      { type: "Testimonials", props: { id: "t-trades-test", title: "What clients say", items: [
        { quote: "Professional from start to finish.", author: "A happy client", role: "" }] } },
      { type: "ContactCard", props: { id: "t-trades-contact", title: "Get in touch", phone: "", email: "", address: "", hours: "" } },
      { type: "CTA", props: { id: "t-trades-cta", text: "Get a quote", href: "#contact", variant: "solid" } },
    ] },
  },
  home_services: {
    label: "Homepage — Professional services",
    data: { content: [
      { type: "Hero", props: { id: "t-serv-hero", title: "Expertise you can rely on", subtitle: "Tell visitors what you do and who you do it for.", image: "", ctaText: "Book a consultation", ctaHref: "#contact" } },
      { type: "Features", props: { id: "t-serv-what", title: "What we do", items: [
        { heading: "Service one", body: "Describe it." }, { heading: "Service two", body: "Describe it." }, { heading: "Service three", body: "Describe it." }] } },
      { type: "TwoColumns", props: { id: "t-serv-approach", leftImage: "", leftHeading: "Our approach", leftBody: "How you work with clients, what sets you apart.", rightImage: "", rightHeading: "Who we work with", rightBody: "The kind of clients/projects you take on." } },
      { type: "Testimonials", props: { id: "t-serv-test", title: "What clients say", items: [
        { quote: "They understood exactly what we needed.", author: "A happy client", role: "" }] } },
      { type: "CTA", props: { id: "t-serv-cta", text: "Book a consultation", href: "#contact", variant: "solid" } },
    ] },
  },
  home_health: {
    label: "Homepage — Health, salon & wellness",
    data: { content: [
      { type: "Hero", props: { id: "t-health-hero", title: "Feel your best", subtitle: "Book your appointment today.", image: "", ctaText: "Book now", ctaHref: "#contact" } },
      { type: "Features", props: { id: "t-health-serv", title: "Our services", items: [
        { heading: "Service one", body: "Describe it." }, { heading: "Service two", body: "Describe it." }, { heading: "Service three", body: "Describe it." }] } },
      { type: "Gallery", props: { id: "t-health-gallery", title: "Our space", images: [] } },
      { type: "Testimonials", props: { id: "t-health-test", title: "What clients say", items: [
        { quote: "Always leave feeling great.", author: "A happy client", role: "" }] } },
      { type: "ContactCard", props: { id: "t-health-contact", title: "Book your visit", phone: "", email: "", address: "", hours: "" } },
      { type: "WhatsAppCTA", props: { id: "t-health-wa", phone: "", message: "Hi! I'd like to book an appointment.", buttonText: "💬 Book on WhatsApp" } },
    ] },
  },
  home_other: {
    label: "Homepage — General",
    data: { content: [
      { type: "Hero", props: { id: "t-other-hero", title: "Your headline", subtitle: "A short supporting line about your business.", image: "", ctaText: "Get in touch", ctaHref: "#contact" } },
      { type: "Features", props: { id: "t-other-feat", title: "Why us", items: [
        { heading: "Feature one", body: "Describe it." }, { heading: "Feature two", body: "Describe it." }, { heading: "Feature three", body: "Describe it." }] } },
      { type: "Testimonials", props: { id: "t-other-test", title: "What customers say", items: [
        { quote: "They made it so easy.", author: "A happy customer", role: "" }] } },
      { type: "ContactCard", props: { id: "t-other-contact", title: "Get in touch", phone: "", email: "", address: "", hours: "" } },
    ] },
  },
  specials: {
    label: "Specials / promo page",
    data: { content: [
      { type: "Heading", props: { id: "t-h", text: "This week's specials", level: "h1", align: "center" } },
      { type: "Text", props: { id: "t-t", text: "Limited stock — order on WhatsApp or right here.", align: "center" } },
      { type: "ProductGrid", props: { id: "t-grid", title: "", category: "", count: 8, linkBase: "/shop" } },
      { type: "CTA", props: { id: "t-cta", text: "See the full range", href: "/shop", variant: "solid" } },
    ] },
  },
  about: {
    label: "About us",
    data: { content: [
      { type: "Hero", props: { id: "t-hero", title: "Our story", subtitle: "", image: "", ctaText: "", ctaHref: "" } },
      { type: "Text", props: { id: "t-t", text: "Tell your story here — who you are, where it started, why customers love you.", align: "left" } },
      { type: "Features", props: { id: "t-f", title: "What we stand for", items: [
        { heading: "Quality", body: "Only the best." }, { heading: "Community", body: "Proudly local." }, { heading: "Service", body: "We answer on WhatsApp." }] } },
    ] },
  },
};

// business_type -> matching homepage template key (mirrors tenants.py::_map_business_type's
// taxonomy exactly). Falls back to the generic template for "other"/unrecognised.
export const HOME_TEMPLATE_BY_BUSINESS_TYPE = {
  food: "home_food", retail: "home_retail", trades: "home_trades",
  services: "home_services", health: "home_health",
};
export function homeTemplateFor(businessType) {
  return TEMPLATES[HOME_TEMPLATE_BY_BUSINESS_TYPE[businessType]] || TEMPLATES.home_other;
}


export function usePages({ tenantId }) {
  const [pages, setPages] = useState(null);
  const [editing, setEditing] = useState(null);   // { slug, title, data, status, seo }
  const [creating, setCreating] = useState(false);
  const [openMenuSlug, setOpenMenuSlug] = useState(null);  // which page row's "⋯" menu is open
  const [newForm, setNewForm] = useState({ title: "", template: "blank" });
  const [showSeo, setShowSeo] = useState(false);
  const [showHistory, setShowHistory] = useState(false);
  const [versions, setVersions] = useState(null);
  const [msg, setMsg] = useState("");
  const [storeUrl, setStoreUrl] = useState(null);
  const [businessType, setBusinessType] = useState(null);  // drives which niche template "Customize homepage" seeds
  const [showLivePreview, setShowLivePreview] = useState(false);
  const [storeSettings, setStoreSettings] = useState(null); // real hero copy — seeds "Customize homepage" with truth, not placeholder text
  const latestData = useRef(null);   // Puck's live document (survives re-renders/saves)
  const [brand, setBrand] = useState(() => getTenantTheme(tenantId));
  const [aiDraftBusy, setAiDraftBusy] = useState(false);
  const [aiDraftErr, setAiDraftErr] = useState("");
  // Puck's `data` prop is a one-shot useState initializer internally (confirmed in the installed
  // package) — it does not react to being replaced after mount. Bumping this key forces a real
  // remount whenever editing.data is replaced programmatically (AI-draft, restoreVersion), so the
  // canvas actually reflects the new content instead of silently staying stale.
  const [editorKey, setEditorKey] = useState(0);
  // AI-draft style-direction modal — "editor" (fill whatever's currently open) or "homepage"
  // (open+fill the homepage template) depending on which entry point was clicked.
  const [aiDraftTarget, setAiDraftTarget] = useState(null);
  const [aiDraftDescription, setAiDraftDescription] = useState("");
  const [aiDraftMood, setAiDraftMood] = useState("");
  const [aiDraftRefImageUrl, setAiDraftRefImageUrl] = useState("");
  // A theme suggestion (color/font) the AI resolved alongside the copy — offered as an explicit,
  // separate "Apply to brand kit" action, never silently written to real settings.
  const [aiDraftTheme, setAiDraftTheme] = useState(null);
  const [applyingTheme, setApplyingTheme] = useState(false);
  // Conversational refine — mirrors VulaAssistant.jsx's chat shape (message list + input +
  // send), scoped down: short confirmation bubbles instead of a general Q&A assistant, and each
  // successful reply also applies the returned content to the open page.
  const [showRefine, setShowRefine] = useState(false);
  const [refineMessages, setRefineMessages] = useState([]);
  const [refineInput, setRefineInput] = useState("");
  const [refineSending, setRefineSending] = useState(false);
  // Supported features found in the tenant's last-shared reference URL(s), offered as quick-add
  // buttons under the analysis reply — cleared once the tenant adds one or sends a new message.
  const [refineFoundFeatures, setRefineFoundFeatures] = useState([]);
  const refineEndRef = useRef(null);
  useEffect(() => { refineEndRef.current?.scrollIntoView({ behavior: "smooth" }); }, [refineMessages, refineSending]);
  // A refine conversation is scoped to the page it happened on — switching to a different page
  // (or back to the list) should never carry a stale thread/instruction context forward.
  useEffect(() => { setRefineMessages([]); setShowRefine(false); setRefineFoundFeatures([]); }, [editing?.slug]);

  // Live product blocks in the editor preview need the tenant + API globals.
  useEffect(() => {
    window.__VULA_PAGE_TENANT = tenantId;
    window.__VULA_API = VULA_API;
  }, [tenantId]);

  // Real tenant brand for the editor canvas (2026-07-21) — previously only the public renderer
  // (VulaPageRender.jsx) fetched this, so a tenant editing their page always saw the hardcoded
  // teal/black fallback instead of their actual colors while working. Same source, same shape.
  useEffect(() => {
    setBrand(getTenantTheme(tenantId));
    fetch(`${VULA_API}/v1/commerce/${tenantId}/brand`)
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
  }, [tenantId]);
  const fontDef = (brand.fontPairing && FONT_PAIRINGS[brand.fontPairing]) || null;
  // Puck's editor canvas renders inside an iframe (its AutoFrame helper) — it mirrors <style>/
  // <link rel="stylesheet"> ELEMENTS it finds in the host document into that iframe (confirmed
  // directly in @measured/puck's bundled source: collectStyles() + a MutationObserver that keeps
  // mirroring on change), but it has no way to see an inline `style={{...}}` attribute on a host
  // div — that never reaches the iframe at all. So the brand vars have to be actual CSS text
  // inside a <style> tag, not inline styles, unlike VulaPageRender.jsx's non-iframed <Render>.
  const brandCss = `:root {
    --brand-accent: ${brand.accent || "var(--accent)"};
    --brand-accent-fg: white;
    --brand-ink: ${brand.ink || "var(--ink)"};
    ${fontDef ? `--brand-font-display: '${fontDef.family}', ${fontDef.fallback};` : ""}
  }`;

  // The tenant's actual live website — so "what does my site look like right now" is answerable
  // without leaving the dashboard. Falls back to the Vula-hosted page renderer if no custom
  // domain is configured yet.
  useEffect(() => {
    fetch(`${VULA_API}/v1/tenants/${tenantId}`).then((r) => r.json())
      .then((d) => { setStoreUrl(d.store_url || null); setBusinessType(d.business_type || null); })
      .catch(() => { setStoreUrl(null); setBusinessType(null); });
    fetch(`${VULA_API}/v1/commerce/${tenantId}/settings`).then((r) => r.json())
      .then((d) => setStoreSettings(d.settings || null)).catch(() => setStoreSettings(null));
  }, [tenantId]);
  const liveUrl = storeUrl || `https://${tenantId}.${STOREFRONT_ROOT_DOMAIN}`;

  const load = () =>
    fetch(`${VULA_API}/v1/commerce/${tenantId}/admin/pages`)
      .then((r) => r.json()).then((d) => setPages(d.pages || [])).catch(() => setPages([]));
  useEffect(() => { load(); }, [tenantId]);  // eslint-disable-line

  const open = async (slug, title) => {
    const p = await fetch(`${VULA_API}/v1/commerce/${tenantId}/admin/pages/${slug}`).then((r) => r.json()).catch(() => ({}));
    setEditing({ slug, title: p.title || title || slug, data: norm(p.puck_data), status: p.status || "draft", seo: p.seo || {} });
  };

  // The "Customize homepage" starter used flat placeholder copy ("Fresh from the harbour")
  // regardless of what's actually live, which made the editor feel disconnected from reality —
  // and used to be the SAME fish-shop template for every tenant regardless of business_type.
  // Now picks the niche template that actually matches this tenant (homeTemplateFor), then still
  // overlays the tenant's real hero_tagline/hero_subtitle (same source their live homepage
  // reads) on top when available.
  const homeSeed = () => {
    const tpl = homeTemplateFor(businessType).data;
    if (!storeSettings?.hero_tagline && !storeSettings?.hero_subtitle) return tpl;
    return {
      ...tpl,
      content: tpl.content.map((block) => block.type !== "Hero" ? block : {
        ...block,
        props: {
          ...block.props,
          title: storeSettings.hero_tagline || block.props.title,
          subtitle: storeSettings.hero_subtitle || block.props.subtitle,
        },
      }),
    };
  };

  const createNew = () => {
    const title = newForm.title.trim();
    if (!title) return;
    const slug = (title.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "").slice(0, 50)) || "page";
    const tpl = TEMPLATES[newForm.template] || TEMPLATES.blank;
    setCreating(false);
    setNewForm({ title: "", template: "blank" });
    setEditing({ slug, title, data: norm(tpl.data), status: "draft", seo: {} });
  };

  const persist = async (data, status) => {
    setMsg("Saving…");
    await fetch(`${VULA_API}/v1/commerce/${tenantId}/admin/pages/${editing.slug}`, {
      method: "PUT", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ title: editing.title, status, puck_data: data, seo: editing.seo || {} }),
    }).catch(() => {});
    setEditing((e) => ({ ...e, data: norm(data), status }));
    setMsg(status === "published" ? "Published ✓" : "Draft saved ✓");
    setTimeout(() => setMsg(""), 1800);
    load();
  };

  const rename = async (p) => {
    const raw = await promptDialog("New page name:", p.title || p.slug);
    if (!raw || !raw.trim()) return;
    await fetch(`${VULA_API}/v1/commerce/${tenantId}/admin/pages/${p.slug}`, {
      method: "PUT", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ title: raw.trim(), status: p.status }),
    }).catch(() => {});
    load();
  };

  const duplicate = async (p) => {
    const full = await fetch(`${VULA_API}/v1/commerce/${tenantId}/admin/pages/${p.slug}`).then((r) => r.json()).catch(() => ({}));
    const slug = `${p.slug}-copy`;
    await fetch(`${VULA_API}/v1/commerce/${tenantId}/admin/pages/${slug}`, {
      method: "PUT", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ title: `${p.title || p.slug} (copy)`, status: "draft", puck_data: full.puck_data || {}, seo: full.seo || {} }),
    }).catch(() => {});
    load();
  };

  const del = async (slug, title) => {
    if (!(await confirmDialog(`Delete the page "${title || slug}"? This can't be undone.`, { danger: true, confirmLabel: 'Yes' }))) return;
    await fetch(`${VULA_API}/v1/commerce/${tenantId}/admin/pages/${slug}`, { method: "DELETE" }).catch(() => {});
    load();
  };

  // Safe "upgrade my existing (possibly already-published) page" path. vula_pages has ONE row
  // per slug with a single status — there's no "draft revision of a published page" — so loading
  // a new template straight into an already-live page's editor and clicking Save would actually
  // UNPUBLISH it. Instead: create a separate {slug}-preview page (always draft, never touches
  // the original), seeded with the niche template that matches this tenant. Reviewed/edited like
  // any normal page; "Make this live" (below) does the actual swap once they're happy.
  const tryNewTemplate = async (p) => {
    const previewSlug = `${p.slug}-preview`;
    const seed = norm(homeSeed());
    await fetch(`${VULA_API}/v1/commerce/${tenantId}/admin/pages/${previewSlug}`, {
      method: "PUT", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ title: `${p.title || p.slug} (new template preview)`, status: "draft", puck_data: seed, seo: {} }),
    }).catch(() => {});
    load();
    open(previewSlug, `${p.title || p.slug} (new template preview)`);
  };

  // Promotes a "{slug}-preview" page's content to replace the original slug's live content.
  // Reuses the existing upsert_page endpoint as-is — it already snapshots the slug's PRE-swap
  // content into vula_page_versions before overwriting (same safety net "Publish" always gets),
  // so this swap is restorable via the existing History panel even though it's a new action.
  const makeLive = async (previewPage) => {
    const originalSlug = previewPage.slug.replace(/-preview$/, "");
    if (originalSlug === previewPage.slug) return;  // not actually a preview page — refuse
    if (!(await confirmDialog(`Replace the live "${originalSlug}" page with this template? The previous version stays in History if you want it back.`, { danger: true, confirmLabel: 'Yes' }))) return;
    const full = await fetch(`${VULA_API}/v1/commerce/${tenantId}/admin/pages/${previewPage.slug}`).then((r) => r.json()).catch(() => ({}));
    await fetch(`${VULA_API}/v1/commerce/${tenantId}/admin/pages/${originalSlug}`, {
      method: "PUT", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ title: previewPage.title?.replace(/ \(new template preview\)$/, "") || originalSlug, status: "published", puck_data: full.puck_data || {}, seo: full.seo || {} }),
    }).catch(() => {});
    await fetch(`${VULA_API}/v1/commerce/${tenantId}/admin/pages/${previewPage.slug}`, { method: "DELETE" }).catch(() => {});
    setEditing(null);
    load();
  };

  const move = async (index, dir) => {
    const next = [...pages];
    const j = index + dir;
    if (j < 0 || j >= next.length) return;
    [next[index], next[j]] = [next[j], next[index]];
    setPages(next);   // optimistic
    await fetch(`${VULA_API}/v1/commerce/${tenantId}/admin/pages/reorder`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ order: next.map((p) => p.slug) }),
    }).catch(() => {});
  };

  const loadVersions = async () => {
    const d = await fetch(`${VULA_API}/v1/commerce/${tenantId}/admin/pages/${editing.slug}/versions`)
      .then((r) => r.json()).catch(() => ({}));
    setVersions(d.versions || []);
  };

  const restoreVersion = async (versionId) => {
    if (!(await confirmDialog("Restore this version? It'll come back as a draft — review before publishing."))) return;
    await fetch(`${VULA_API}/v1/commerce/${tenantId}/admin/pages/${editing.slug}/versions/${versionId}/restore`, { method: "POST" }).catch(() => {});
    const p = await fetch(`${VULA_API}/v1/commerce/${tenantId}/admin/pages/${editing.slug}`).then((r) => r.json()).catch(() => ({}));
    setEditing((e) => ({ ...e, data: norm(p.puck_data), status: p.status || "draft", title: p.title || e.title, seo: p.seo || {} }));
    setEditorKey((k) => k + 1);  // editing.data was just replaced programmatically — force Puck to remount and pick it up
    setShowHistory(false);
    setMsg("Restored as draft ✓");
    setTimeout(() => setMsg(""), 2000);
  };

  // ✨ AI-drafted copy + design — opens the style-direction modal, which on confirm fills the
  // marketing-copy fields (headlines, section text, button labels, WhatsApp message) of whatever
  // page is being targeted with real, business-specific writing, grounded server-side in this
  // tenant's real settings/persona/KB (page_copy.py). Never touches Testimonials (customer
  // quotes) and never invents phone/email/address — those are always the tenant's real settings
  // or left blank, enforced server-side, not here. Same "review before it's live" boundary as
  // any manual edit: this only changes what's loaded in the Puck editor / a suggested theme
  // strip; Save draft / Publish and the separate "Apply theme" action are still explicit steps.
  const openAiDraft = (target) => {
    setAiDraftErr("");
    setAiDraftTarget(target);
  };

  const runAiDraft = async () => {
    const target = aiDraftTarget;
    const baseContent = target === "homepage"
      ? norm(homeSeed()).content
      : (latestData.current || editing.data).content || [];

    setAiDraftBusy(true);
    setAiDraftErr("");
    try {
      let resolvedMood = aiDraftMood;
      let resolvedTheme = null;
      let styleNote = "";
      if (aiDraftRefImageUrl) {
        const ref = await fetch(`${VULA_API}/v1/commerce/${tenantId}/admin/pages/design-reference`, {
          method: "POST", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ file_url: aiDraftRefImageUrl }),
        }).then((r) => r.json());
        if (ref.theme) { resolvedTheme = ref.theme; resolvedMood = ref.theme.mood; styleNote = ref.style_note || ""; }
      }

      const res = await fetch(`${VULA_API}/v1/commerce/${tenantId}/admin/pages/ai-draft`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ content: baseContent, description: aiDraftDescription.trim(), mood: resolvedMood }),
      }).then((r) => r.json());
      if (res.error) { setAiDraftErr(res.error); return; }

      const finalTheme = resolvedTheme || res.theme || null;
      const nextData = { content: res.content, root: { props: {} } };
      if (target === "homepage") {
        setEditing({ slug: "home", title: "Home", data: nextData, status: "draft", seo: {} });
      } else {
        latestData.current = nextData;
        setEditing((e) => ({ ...e, data: nextData }));
      }
      setEditorKey((k) => k + 1); // force Puck remount so the canvas reflects the new data
      if (finalTheme) setAiDraftTheme({ ...finalTheme, styleNote });
      setMsg("Drafted ✓ — review before saving");
      setTimeout(() => setMsg(""), 2500);
      setAiDraftTarget(null);
      setAiDraftDescription("");
      setAiDraftMood("");
      setAiDraftRefImageUrl("");
    } catch {
      setAiDraftErr("Could not draft this right now — please try again.");
    } finally {
      setAiDraftBusy(false);
    }
  };

  // Explicit, separate action — never auto-applied. Only writes the 3 theme fields (a safe
  // partial patch; the invoice-settings endpoint merges rather than overwrites).
  const applyAiDraftTheme = async () => {
    if (!aiDraftTheme) return;
    setApplyingTheme(true);
    try {
      await fetch(`${VULA_API}/v1/commerce/${tenantId}/admin/invoice-settings`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          accent_color: aiDraftTheme.accent_color, ink_color: aiDraftTheme.ink_color,
          font_pairing: aiDraftTheme.font_pairing,
        }),
      }).catch(() => {});
      setBrand((b) => ({ ...b, accent: aiDraftTheme.accent_color, ink: aiDraftTheme.ink_color, fontPairing: aiDraftTheme.font_pairing }));
      setMsg("Theme applied ✓");
      setTimeout(() => setMsg(""), 2000);
      setAiDraftTheme(null);
    } finally {
      setApplyingTheme(false);
    }
  };

  // Conversational refine — adjusts the CURRENT draft in place per a plain-language instruction,
  // instead of re-rolling the whole page from scratch. Same safety guarantees as AI-draft
  // (contact facts real-data-only, Testimonials never touched) enforced server-side.
  // Applies whatever content ai-refine/analyze-reference produced to the open editor — shared by
  // sendRefine's instruction path and addFeature's confirm path.
  const applyRefinedContent = (base, content) => {
    const next = { ...base, content };
    latestData.current = next;
    setEditing((e) => ({ ...e, data: next }));
    setEditorKey((k) => k + 1);
  };

  const sendRefine = async (text) => {
    const raw = (text ?? refineInput).trim();
    if (!raw || refineSending || !editing) return;
    setRefineInput("");
    setRefineFoundFeatures([]);
    setRefineMessages((m) => [...m, { role: "user", text: raw }]);
    setRefineSending(true);
    try {
      const base = latestData.current || editing.data;
      const urls = raw.match(URL_RE);

      // A pasted URL means "here's a site I like" — analyze it for features instead of treating
      // the whole message as a copy-edit instruction.
      if (urls && urls.length) {
        const res = await fetch(`${VULA_API}/v1/commerce/${tenantId}/admin/pages/analyze-reference`, {
          method: "POST", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ urls }),
        }).then((r) => r.json());
        if (res.error) {
          setRefineMessages((m) => [...m, { role: "assistant", text: res.error }]);
          return;
        }
        const addable = (res.features_found || []).filter((f) => ADDABLE_FEATURES[f]);
        const unsupported = (res.features_found || []).filter((f) => UNSUPPORTED_FEATURE_LABELS[f]);
        let reply = res.notes ? `I looked at that — ${res.notes} ` : "I looked at that. ";
        if (addable.length) {
          reply += `I can add ${addable.map((f) => ADDABLE_FEATURES[f]).join(" and ")} — tap below to add one.`;
        } else {
          reply += "I didn't spot anything I can add as a new section right now.";
        }
        if (unsupported.length) {
          reply += ` (It also has ${unsupported.map((f) => UNSUPPORTED_FEATURE_LABELS[f]).join(" and ")}, which Vula doesn't support yet.)`;
        }
        setRefineMessages((m) => [...m, { role: "assistant", text: reply }]);
        setRefineFoundFeatures(addable);
        return;
      }

      const res = await fetch(`${VULA_API}/v1/commerce/${tenantId}/admin/pages/ai-refine`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ content: base.content || [], instruction: raw }),
      }).then((r) => r.json());
      if (res.error) {
        setRefineMessages((m) => [...m, { role: "assistant", text: res.error }]);
        return;
      }
      applyRefinedContent(base, res.content);
      setRefineMessages((m) => [...m, { role: "assistant", text: "Done — take a look ✓" }]);
    } catch {
      setRefineMessages((m) => [...m, { role: "assistant", text: "Connection error — please try again." }]);
    } finally {
      setRefineSending(false);
    }
  };

  const addFeature = async (featureKey) => {
    if (refineSending || !editing) return;
    setRefineFoundFeatures((f) => f.filter((k) => k !== featureKey));
    setRefineSending(true);
    try {
      const base = latestData.current || editing.data;
      const res = await fetch(`${VULA_API}/v1/commerce/${tenantId}/admin/pages/ai-refine`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ content: base.content || [], add_features: [featureKey] }),
      }).then((r) => r.json());
      if (res.error) {
        setRefineMessages((m) => [...m, { role: "assistant", text: res.error }]);
        return;
      }
      applyRefinedContent(base, res.content);
      setRefineMessages((m) => [...m, { role: "assistant", text: `Added ${ADDABLE_FEATURES[featureKey] || "that"} ✓` }]);
    } catch {
      setRefineMessages((m) => [...m, { role: "assistant", text: "Connection error — please try again." }]);
    } finally {
      setRefineSending(false);
    }
  };
  return { pages, setPages, editing, setEditing, creating, setCreating, openMenuSlug, setOpenMenuSlug, newForm, setNewForm, showSeo, setShowSeo, showHistory, setShowHistory, versions, setVersions, msg, setMsg, storeUrl, setStoreUrl, businessType, setBusinessType, showLivePreview, setShowLivePreview, storeSettings, setStoreSettings, latestData, brand, setBrand, aiDraftBusy, setAiDraftBusy, aiDraftErr, setAiDraftErr, editorKey, setEditorKey, aiDraftTarget, setAiDraftTarget, aiDraftDescription, setAiDraftDescription, aiDraftMood, setAiDraftMood, aiDraftRefImageUrl, setAiDraftRefImageUrl, aiDraftTheme, setAiDraftTheme, applyingTheme, setApplyingTheme, showRefine, setShowRefine, refineMessages, setRefineMessages, refineInput, setRefineInput, refineSending, setRefineSending, refineFoundFeatures, setRefineFoundFeatures, refineEndRef, fontDef, brandCss, liveUrl, load, open, homeSeed, createNew, persist, rename, duplicate, del, tryNewTemplate, makeLive, move, loadVersions, restoreVersion, openAiDraft, runAiDraft, applyAiDraftTheme, applyRefinedContent, sendRefine, addFeature };
}
