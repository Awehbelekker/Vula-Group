/**
 * Puck block library for the Vula dashboard page builder — shared by the editor (VulaPages) and
 * the public render route (/pages/:tenant/:slug). Block types + prop names match the OTH storefront
 * config (off_the_hook/src/puck.config.tsx) so a page built here also renders on a tenant storefront
 * that has the matching renderer. Uses inline styles + <a> (no Tailwind / next-link) so it renders
 * self-contained anywhere. Brand flows through CSS vars with sensible fallbacks.
 *
 * Depth + animation (2026-07-21): every block now has a scroll-triggered entrance, and interactive
 * elements (buttons/cards/product tiles/gallery images) get a hover lift/zoom + shadow. Animation
 * technique is deliberately plain CSS keyframes + a tiny IntersectionObserver hook — no framer-motion
 * — so it's hand-portable, unchanged in spirit, to the off_the_hook and kelp copies of this file,
 * which run in a different framework (Next.js/Tailwind) and can't share this file directly.
 * VULA_PUCK_STYLES (exported) is injected once by each renderer (VulaPages.jsx, VulaPageRender.jsx),
 * not per block — the dashboard config has no <style> tag anywhere otherwise, and :hover/@keyframes
 * can't be expressed as inline styles.
 */
import { useEffect, useState, useRef } from "react";
import VulaImageUpload from "../components/VulaImageUpload";

const ACCENT = "var(--brand-accent, #0E7C7B)";
const ACCENT_FG = "var(--brand-accent-fg, #ffffff)";
const INK = "var(--brand-ink, #1a1a1a)";
const FONT_DISPLAY = "var(--brand-font-display, inherit)";
const wrap = { maxWidth: 1100, margin: "0 auto", padding: "0 16px" };

// Self-contained depth constants (not var()-references to theme/tokens.js, which only exists in
// the dashboard shell — off_the_hook/kelp don't load it, so these need to be literal here too).
const SHADOW_SM = "0 1px 3px rgba(0,0,0,0.08), 0 1px 2px rgba(0,0,0,0.06)";
const SHADOW_MD = "0 4px 12px rgba(0,0,0,0.10), 0 2px 4px rgba(0,0,0,0.06)";
const SHADOW_LG = "0 12px 32px rgba(0,0,0,0.14), 0 4px 8px rgba(0,0,0,0.08)";

// Injected once per mount point (VulaPages.jsx editor canvas, VulaPageRender.jsx public page) via
// <style>{VULA_PUCK_STYLES}</style> — never per block. Keyframe names/timing/classes are the exact
// contract the off_the_hook/kelp copies replicate, so a page looks identical regardless of renderer.
export const VULA_PUCK_STYLES = `
@keyframes vula-anim-fadeUp { from { opacity:0; transform:translateY(24px); } to { opacity:1; transform:translateY(0); } }
@keyframes vula-anim-fadeIn { from { opacity:0; } to { opacity:1; } }
@keyframes vula-anim-scaleIn { from { opacity:0; transform:scale(0.94); } to { opacity:1; transform:scale(1); } }
@keyframes vula-anim-slideInLeft { from { opacity:0; transform:translateX(-32px); } to { opacity:1; transform:translateX(0); } }
@keyframes vula-anim-slideInRight { from { opacity:0; transform:translateX(32px); } to { opacity:1; transform:translateX(0); } }
@keyframes vula-anim-shimmer { 0% { background-position: -400px 0; } 100% { background-position: 400px 0; } }

.vula-reveal { opacity: 0; }
.vula-reveal.vula-play-fadeUp { animation: vula-anim-fadeUp 0.6s cubic-bezier(0.16,1,0.3,1) both; }
.vula-reveal.vula-play-fadeIn { animation: vula-anim-fadeIn 0.6s cubic-bezier(0.16,1,0.3,1) both; }
.vula-reveal.vula-play-scaleIn { animation: vula-anim-scaleIn 0.6s cubic-bezier(0.16,1,0.3,1) both; }
.vula-reveal.vula-play-slideInLeft { animation: vula-anim-slideInLeft 0.6s cubic-bezier(0.16,1,0.3,1) both; }
.vula-reveal.vula-play-slideInRight { animation: vula-anim-slideInRight 0.6s cubic-bezier(0.16,1,0.3,1) both; }

.vula-stagger-item { opacity: 0; }
.vula-stagger-play .vula-stagger-item { animation: vula-anim-fadeUp 0.6s cubic-bezier(0.16,1,0.3,1) both; }

.vula-btn-lift { transition: transform 0.2s ease, box-shadow 0.2s ease; }
.vula-btn-lift:hover { transform: translateY(-2px); box-shadow: ${SHADOW_LG}; }

.vula-card-lift { transition: transform 0.25s ease, box-shadow 0.25s ease; overflow: hidden; }
.vula-card-lift:hover { transform: translateY(-4px); box-shadow: ${SHADOW_MD}; }
.vula-card-lift .vula-zoom-target { transition: transform 0.35s ease; }
.vula-card-lift:hover .vula-zoom-target { transform: scale(1.06); }

.vula-img-zoom { overflow: hidden; display: block; border-radius: inherit; }
.vula-img-zoom img { transition: transform 0.35s ease; display: block; width: 100%; height: 100%; object-fit: cover; }
.vula-img-zoom:hover img { transform: scale(1.06); }

.vula-skeleton { background: linear-gradient(90deg, #f0f0f0 0%, #f8f8f8 50%, #f0f0f0 100%); background-size: 800px 100%; animation: vula-anim-shimmer 1.4s linear infinite; }

.vula-lightbox-overlay { position: fixed; inset: 0; background: rgba(0,0,0,0.9); z-index: 9999; display: flex; align-items: center; justify-content: center; padding: 24px; cursor: zoom-out; animation: vula-anim-fadeIn 0.2s ease both; }
.vula-lightbox-img { max-width: 100%; max-height: 100%; border-radius: 8px; animation: vula-anim-scaleIn 0.25s cubic-bezier(0.16,1,0.3,1) both; }
.vula-lightbox-close { position: absolute; top: 20px; right: 24px; color: #fff; font-size: 32px; line-height: 1; cursor: pointer; background: none; border: none; }

.vula-anim-off .vula-reveal, .vula-anim-off .vula-stagger-item { opacity: 1 !important; animation: none !important; }

@media (prefers-reduced-motion: reduce) {
  .vula-reveal, .vula-stagger-item { opacity: 1 !important; animation: none !important; }
  .vula-btn-lift:hover, .vula-card-lift:hover, .vula-card-lift:hover .vula-zoom-target, .vula-img-zoom:hover img { transform: none !important; }
  .vula-skeleton { animation: none !important; }
}
`;

// Shared scroll-reveal primitive: attaches an IntersectionObserver to the returned ref, adding
// `playClass` (once, then unobserving) when the element enters the viewport. No re-render — the
// class is toggled directly via classList, matching the "reveal once, never on scroll back up"
// pattern of most page-builder animation libraries.
function useRevealRef(playClass) {
  const ref = useRef(null);
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    if (typeof window === "undefined" || !("IntersectionObserver" in window)) {
      el.classList.add(playClass);
      return;
    }
    const io = new IntersectionObserver((entries) => {
      entries.forEach((entry) => {
        if (entry.isIntersecting) {
          el.classList.add(playClass);
          io.unobserve(el);
        }
      });
    }, { threshold: 0.15, rootMargin: "0px 0px -10% 0px" });
    io.observe(el);
    return () => io.disconnect();
  }, [playClass]);
  return ref;
}
// Single-element entrance — pair with className="vula-reveal" on the same node.
function useReveal(anim = "fadeUp", delay = 0) {
  const ref = useRevealRef(`vula-play-${anim}`);
  useEffect(() => {
    if (ref.current && delay) ref.current.style.animationDelay = `${delay}ms`;
  }, [delay]);
  return ref;
}
// Container-level trigger for staggered children — pair with className="vula-stagger-item" (+ an
// animationDelay computed from index) on each child. One IntersectionObserver for the whole group
// instead of one per item (hooks can't be called inside a .map() loop).
function useStaggerReveal() {
  return useRevealRef("vula-stagger-play");
}
const staggerDelay = (i) => ({ animationDelay: `${Math.min(i, 6) * 80}ms` });

// Per-block animation choice (2026-07-22 depth pass): every single-entrance block previously
// hardcoded which of the 5 reveal styles it used — a tenant could turn animation on/off for the
// whole page (root `pageAnimations`) but never pick "this block slides in" vs "that one fades."
// `ANIM_FIELD`/`useBlockReveal` are the shared plumbing so every block wires this the same way.
// `animation` defaults to undefined on already-published pages (saved before this field existed)
// — useBlockReveal treats that identically to the block's original hardcoded animation, so no
// existing page changes appearance until the tenant deliberately picks something else.
const ANIM_FIELD = {
  type: "select", label: "Entrance animation",
  options: [
    { label: "Fade up", value: "fadeUp" }, { label: "Fade in", value: "fadeIn" },
    { label: "Scale in", value: "scaleIn" }, { label: "Slide from left", value: "slideInLeft" },
    { label: "Slide from right", value: "slideInRight" }, { label: "None", value: "none" },
  ],
};
function useBlockReveal(animation, fallbackAnim) {
  const anim = animation === "none" ? null : (animation || fallbackAnim);
  const ref = useReveal(anim || fallbackAnim);
  return anim ? { ref, className: "vula-reveal" } : {};
}

// Custom Puck field: real image UPLOAD (to the tenant's Supabase bucket) instead of a bare URL
// text box — with URL paste still available as a fallback. Dashboard editor only; the storefront
// config copies keep plain text fields (they only render, never edit).
const imageField = (label) => ({
  type: "custom",
  label,
  render: ({ value, onChange }) => (
    <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
      <span style={{ fontSize: 12, fontWeight: 600 }}>{label}</span>
      {value ? <img src={value} alt="" style={{ width: "100%", borderRadius: 8, maxHeight: 120, objectFit: "cover" }} /> : null}
      <VulaImageUpload
        tenantId={(typeof window !== "undefined" && window.__VULA_PAGE_TENANT) || "off-the-hook"}
        maxFiles={1}
        onUploaded={(urls) => urls[0] && onChange(urls[0])}
      />
      <input
        value={value || ""}
        onChange={(e) => onChange(e.target.value)}
        placeholder="…or paste an image URL"
        style={{ padding: "6px 8px", border: "1px solid #ddd", borderRadius: 6, fontSize: 12 }}
      />
    </div>
  ),
});

export const config = {
  root: {
    fields: {
      pageAnimations: {
        type: "radio",
        label: "Page animations",
        options: [{ label: "On", value: true }, { label: "Off", value: false }],
      },
    },
    defaultProps: { pageAnimations: true },
    render: ({ children, pageAnimations }) => (
      <div className={pageAnimations === false ? "vula-anim-off" : undefined}>{children}</div>
    ),
  },
  components: {
    Hero: {
      label: "Hero",
      fields: {
        title: { type: "text" }, subtitle: { type: "textarea" },
        image: imageField("Background image"),
        overlayOpacity: { type: "number", label: "Dark overlay strength 0-100" },
        ctaText: { type: "text", label: "Primary button text" }, ctaHref: { type: "text", label: "Primary button link" },
        ctaText2: { type: "text", label: "Second button text (optional)" }, ctaHref2: { type: "text", label: "Second button link" },
        animation: ANIM_FIELD,
      },
      defaultProps: { title: "Your headline", subtitle: "A short supporting line.", image: "", overlayOpacity: 55,
        ctaText: "Shop now", ctaHref: "/shop", ctaText2: "", ctaHref2: "" },
      render: ({ title, subtitle, image, overlayOpacity, ctaText, ctaHref, ctaText2, ctaHref2, animation }) => {
        const reveal = useBlockReveal(animation, "fadeIn");
        const overlay = overlayOpacity ?? 55;
        return (
          <section {...reveal} style={{ position: "relative", display: "flex", alignItems: "center", justifyContent: "center",
            textAlign: "center", color: "#fff", minHeight: 420, backgroundColor: ACCENT,
            backgroundImage: image ? `url(${image})` : undefined, backgroundSize: "cover", backgroundPosition: "center" }}>
            <div style={{ position: "absolute", inset: 0, backgroundImage: `linear-gradient(180deg, rgba(0,0,0,${Math.min(overlay, 100) * 0.27 / 100}) 0%, rgba(0,0,0,${Math.min(overlay, 100) / 100}) 100%)` }} />
            <div style={{ position: "relative", zIndex: 1, maxWidth: 760, padding: "80px 16px" }}>
              <h1 style={{ fontSize: 44, fontWeight: 800, marginBottom: 16, fontFamily: FONT_DISPLAY }}>{title}</h1>
              {subtitle ? <p style={{ fontSize: 18, opacity: 0.9, marginBottom: 24 }}>{subtitle}</p> : null}
              {(ctaText || ctaText2) && (
                <div style={{ display: "flex", gap: 12, justifyContent: "center", flexWrap: "wrap" }}>
                  {ctaText ? (
                    <a href={ctaHref || "#"} className="vula-btn-lift" style={{ display: "inline-block", borderRadius: 999, padding: "12px 28px",
                      fontWeight: 600, background: ACCENT_FG, color: INK, textDecoration: "none", boxShadow: SHADOW_MD }}>{ctaText}</a>
                  ) : null}
                  {ctaText2 ? (
                    <a href={ctaHref2 || "#"} className="vula-btn-lift" style={{ display: "inline-block", borderRadius: 999, padding: "12px 28px",
                      fontWeight: 600, border: "1px solid rgba(255,255,255,0.7)", color: "#fff", textDecoration: "none" }}>{ctaText2}</a>
                  ) : null}
                </div>
              )}
            </div>
          </section>
        );
      },
    },
    Heading: {
      label: "Heading",
      fields: {
        text: { type: "text" },
        level: { type: "select", options: [{ label: "H1", value: "h1" }, { label: "H2", value: "h2" }, { label: "H3", value: "h3" }] },
        align: { type: "select", options: [{ label: "Left", value: "left" }, { label: "Center", value: "center" }] },
        animation: ANIM_FIELD,
      },
      defaultProps: { text: "Section heading", level: "h2", align: "left" },
      render: ({ text, level, align, animation }) => {
        const size = level === "h1" ? 40 : level === "h2" ? 30 : 24;
        const Tag = level || "h2";
        const reveal = useBlockReveal(animation, "fadeUp");
        return (
          <div {...reveal} style={{ ...wrap, padding: "24px 16px" }}>
            <Tag style={{ fontSize: size, fontWeight: 800, textAlign: align === "center" ? "center" : "left", color: INK, margin: 0, fontFamily: FONT_DISPLAY }}>{text}</Tag>
          </div>
        );
      },
    },
    Text: {
      label: "Text",
      fields: {
        text: { type: "textarea" },
        align: { type: "select", options: [{ label: "Left", value: "left" }, { label: "Center", value: "center" }] },
        animation: ANIM_FIELD,
      },
      defaultProps: { text: "Write your content here.", align: "left" },
      render: ({ text, align, animation }) => {
        const reveal = useBlockReveal(animation, "fadeUp");
        return (
          <div {...reveal} style={{ ...wrap, padding: "12px 16px" }}>
            <p style={{ lineHeight: 1.7, whiteSpace: "pre-line", maxWidth: 760, color: "#555",
              margin: align === "center" ? "0 auto" : 0, textAlign: align === "center" ? "center" : "left" }}>{text}</p>
          </div>
        );
      },
    },
    ImageBlock: {
      label: "Image",
      fields: {
        src: imageField("Image"), alt: { type: "text" },
        rounded: { type: "radio", options: [{ label: "Rounded", value: true }, { label: "Square", value: false }] },
        animation: ANIM_FIELD,
      },
      defaultProps: { src: "", alt: "", rounded: true },
      render: ({ src, alt, rounded, animation }) => {
        const reveal = useBlockReveal(animation, "scaleIn");
        return (
          <div {...reveal} style={{ ...wrap, padding: "24px 16px" }}>
            {src ? <img src={src} alt={alt} style={{ width: "100%", objectFit: "cover", borderRadius: rounded ? 16 : 0, boxShadow: SHADOW_SM }} />
                 : <div style={{ height: 192, background: "#f1f1f1", borderRadius: rounded ? 16 : 0 }} />}
          </div>
        );
      },
    },
    CTA: {
      label: "Button",
      fields: {
        text: { type: "text" }, href: { type: "text" },
        variant: { type: "select", options: [{ label: "Solid", value: "solid" }, { label: "Outline", value: "outline" }] },
        animation: ANIM_FIELD,
      },
      defaultProps: { text: "Get in touch", href: "/contact", variant: "solid" },
      render: ({ text, href, variant, animation }) => {
        const reveal = useBlockReveal(animation, "fadeUp");
        return (
          <div {...reveal} style={{ ...wrap, padding: "20px 16px", textAlign: "center" }}>
            <a href={href || "#"} className="vula-btn-lift" style={{ display: "inline-block", borderRadius: 999, padding: "12px 28px",
              fontWeight: 600, border: `1px solid ${ACCENT}`, textDecoration: "none", boxShadow: SHADOW_SM,
              ...(variant === "solid" ? { background: ACCENT, color: ACCENT_FG } : { color: ACCENT }) }}>{text}</a>
          </div>
        );
      },
    },
    Features: {
      label: "Feature row (3)",
      fields: {
        title: { type: "text" },
        items: {
          type: "array", arrayFields: { heading: { type: "text" }, body: { type: "textarea" } },
          defaultItemProps: { heading: "Feature", body: "Describe it." },
        },
      },
      defaultProps: {
        title: "Why us",
        items: [
          { heading: "Fresh", body: "Caught daily." },
          { heading: "Local", body: "Cape Town sourced." },
          { heading: "Delivered", body: "To your door." },
        ],
      },
      render: ({ title, items }) => {
        const gridRef = useStaggerReveal();
        return (
          <section style={{ ...wrap, padding: "48px 16px" }}>
            {title ? <h2 style={{ fontSize: 30, fontWeight: 800, textAlign: "center", marginBottom: 32, color: INK }}>{title}</h2> : null}
            <div ref={gridRef} style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit,minmax(220px,1fr))", gap: 24 }}>
              {(items || []).map((it, i) => (
                <div key={i} className="vula-stagger-item vula-card-lift" style={{ ...staggerDelay(i), borderRadius: 16, border: "1px solid #e5e5e5", padding: 24, textAlign: "center", boxShadow: SHADOW_SM, background: "#fff" }}>
                  <h3 style={{ fontWeight: 600, fontSize: 18, marginBottom: 8, color: INK }}>{it.heading}</h3>
                  <p style={{ color: "#666", fontSize: 14 }}>{it.body}</p>
                </div>
              ))}
            </div>
          </section>
        );
      },
    },
    Spacer: {
      label: "Spacer",
      fields: { size: { type: "select", options: [{ label: "Small", value: "sm" }, { label: "Medium", value: "md" }, { label: "Large", value: "lg" }] } },
      defaultProps: { size: "md" },
      render: ({ size }) => <div style={{ height: size === "sm" ? 24 : size === "lg" ? 96 : 48 }} />,
    },
    ProductGrid: {
      label: "Product grid (live)",
      fields: {
        title: { type: "text" },
        category: { type: "text", label: "Category key (blank = all)" },
        count: { type: "number", label: "Max products" },
        linkBase: { type: "text", label: "Product link base" },
      },
      defaultProps: { title: "Shop the range", category: "", count: 8, linkBase: "/shop" },
      render: (props) => <LiveProducts {...props} mode="all" />,
    },
    FeaturedProducts: {
      label: "Featured products (live)",
      fields: {
        title: { type: "text" },
        count: { type: "number", label: "Max products" },
        linkBase: { type: "text", label: "Product link base" },
      },
      defaultProps: { title: "Today's catch", count: 4, linkBase: "/shop" },
      render: (props) => <LiveProducts {...props} mode="featured" />,
    },
    CategoryNav: {
      label: "Category tiles (live)",
      fields: { title: { type: "text" }, linkBase: { type: "text", label: "Shop link base" } },
      defaultProps: { title: "Browse by category", linkBase: "/shop" },
      render: (props) => <LiveCategories {...props} />,
    },
    TwoColumns: {
      label: "Two columns",
      fields: {
        leftImage: imageField("Left image (blank = text only)"),
        leftHeading: { type: "text", label: "Left heading" },
        leftBody: { type: "textarea", label: "Left body" },
        rightImage: imageField("Right image (blank = text only)"),
        rightHeading: { type: "text", label: "Right heading" },
        rightBody: { type: "textarea", label: "Right body" },
      },
      defaultProps: {
        leftImage: "", leftHeading: "Our story", leftBody: "Tell it here.",
        rightImage: "", rightHeading: "What makes us different", rightBody: "Tell it here.",
      },
      render: ({ leftImage, leftHeading, leftBody, rightImage, rightHeading, rightBody }) => {
        // Fixed 2 columns (not a loop) — safe to call the reveal hook twice unconditionally.
        const leftRef = useReveal("slideInLeft");
        const rightRef = useReveal("slideInRight");
        const cols = [
          { ref: leftRef, img: leftImage, h: leftHeading, b: leftBody },
          { ref: rightRef, img: rightImage, h: rightHeading, b: rightBody },
        ];
        return (
          <section style={{ ...wrap, padding: "40px 16px", display: "grid", gridTemplateColumns: "repeat(auto-fit,minmax(260px,1fr))", gap: 32 }}>
            {cols.map((col, i) => (
              <div key={i} ref={col.ref} className="vula-reveal">
                {col.img ? <img src={col.img} alt="" style={{ width: "100%", borderRadius: 12, marginBottom: 16, objectFit: "cover", maxHeight: 240, boxShadow: SHADOW_SM }} /> : null}
                {col.h ? <h3 style={{ fontSize: 22, fontWeight: 800, color: INK, marginBottom: 8 }}>{col.h}</h3> : null}
                {col.b ? <p style={{ color: "#666", lineHeight: 1.7, whiteSpace: "pre-line" }}>{col.b}</p> : null}
              </div>
            ))}
          </section>
        );
      },
    },
    Gallery: {
      label: "Gallery",
      fields: {
        title: { type: "text" },
        images: {
          type: "array",
          arrayFields: { src: imageField("Image"), alt: { type: "text" } },
          defaultItemProps: { src: "", alt: "" },
        },
      },
      defaultProps: { title: "Gallery", images: [] },
      render: ({ title, images }) => {
        const gridRef = useStaggerReveal();
        const [openIndex, setOpenIndex] = useState(null);
        const shown = (images || []).filter((im) => im.src);
        useEffect(() => {
          if (openIndex === null) return;
          const onKey = (e) => { if (e.key === "Escape") setOpenIndex(null); };
          window.addEventListener("keydown", onKey);
          return () => window.removeEventListener("keydown", onKey);
        }, [openIndex]);
        return (
          <section style={{ ...wrap, padding: "40px 16px" }}>
            {title ? <h2 style={{ fontSize: 30, fontWeight: 800, textAlign: "center", marginBottom: 28, color: INK }}>{title}</h2> : null}
            <div ref={gridRef} style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill,minmax(180px,1fr))", gap: 12 }}>
              {shown.map((im, i) => (
                <div key={i} className="vula-stagger-item vula-img-zoom" style={{ ...staggerDelay(i), height: 160, borderRadius: 12, cursor: "zoom-in" }}
                  onClick={() => setOpenIndex(i)}>
                  <img src={im.src} alt={im.alt || ""} />
                </div>
              ))}
            </div>
            {shown.length === 0 && <p style={{ textAlign: "center", color: "#888" }}>Add images in the editor →</p>}
            {openIndex !== null && shown[openIndex] ? (
              <div className="vula-lightbox-overlay" onClick={() => setOpenIndex(null)}>
                <button className="vula-lightbox-close" onClick={(e) => { e.stopPropagation(); setOpenIndex(null); }} aria-label="Close">×</button>
                <img className="vula-lightbox-img" src={shown[openIndex].src} alt={shown[openIndex].alt || ""} onClick={(e) => e.stopPropagation()} />
              </div>
            ) : null}
          </section>
        );
      },
    },
    Testimonials: {
      label: "Testimonials",
      fields: {
        title: { type: "text" },
        items: {
          type: "array",
          arrayFields: { quote: { type: "textarea" }, author: { type: "text" }, role: { type: "text", label: "Role / location" } },
          defaultItemProps: { quote: "They made it so easy.", author: "A happy customer", role: "" },
        },
      },
      defaultProps: {
        title: "What customers say",
        items: [{ quote: "They made it so easy.", author: "A happy customer", role: "" }],
      },
      render: ({ title, items }) => {
        const gridRef = useStaggerReveal();
        return (
          <section style={{ ...wrap, padding: "40px 16px" }}>
            {title ? <h2 style={{ fontSize: 30, fontWeight: 800, textAlign: "center", marginBottom: 28, color: INK }}>{title}</h2> : null}
            <div ref={gridRef} style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit,minmax(240px,1fr))", gap: 20 }}>
              {(items || []).map((it, i) => (
                <div key={i} className="vula-stagger-item vula-card-lift" style={{ ...staggerDelay(i), borderRadius: 16, border: "1px solid #e5e5e5", padding: 24, background: "#fafafa", boxShadow: SHADOW_SM }}>
                  <p style={{ fontStyle: "italic", color: "#444", lineHeight: 1.6, marginBottom: 12 }}>&ldquo;{it.quote}&rdquo;</p>
                  <div style={{ fontWeight: 700, color: INK, fontSize: 14 }}>{it.author}</div>
                  {it.role ? <div style={{ color: "#888", fontSize: 12.5 }}>{it.role}</div> : null}
                </div>
              ))}
            </div>
          </section>
        );
      },
    },
    VideoEmbed: {
      label: "Video",
      fields: { url: { type: "text", label: "YouTube / Vimeo / direct video URL" }, caption: { type: "text" }, animation: ANIM_FIELD },
      defaultProps: { url: "", caption: "" },
      render: ({ url, caption, animation }) => {
        const yt = (url || "").match(/(?:youtu\.be\/|youtube\.com\/watch\?v=|youtube\.com\/embed\/)([\w-]+)/);
        const vimeo = (url || "").match(/vimeo\.com\/(\d+)/);
        const embedSrc = yt ? `https://www.youtube.com/embed/${yt[1]}` : vimeo ? `https://player.vimeo.com/video/${vimeo[1]}` : null;
        const reveal = useBlockReveal(animation, "fadeUp");
        return (
          <section {...reveal} style={{ ...wrap, padding: "40px 16px" }}>
            <div style={{ position: "relative", paddingTop: "56.25%", borderRadius: 14, overflow: "hidden", background: "#000", boxShadow: SHADOW_MD }}>
              {embedSrc ? (
                <iframe src={embedSrc} title={caption || "Video"} allowFullScreen
                  style={{ position: "absolute", inset: 0, width: "100%", height: "100%", border: "none" }} />
              ) : url ? (
                <video src={url} controls style={{ position: "absolute", inset: 0, width: "100%", height: "100%", objectFit: "cover" }} />
              ) : (
                <div style={{ position: "absolute", inset: 0, display: "flex", alignItems: "center", justifyContent: "center", color: "#888" }}>Add a video URL →</div>
              )}
            </div>
            {caption ? <p style={{ textAlign: "center", color: "#888", fontSize: 13, marginTop: 10 }}>{caption}</p> : null}
          </section>
        );
      },
    },
    WhatsAppCTA: {
      label: "WhatsApp button",
      fields: {
        phone: { type: "text", label: "Phone (27…, no +)" },
        message: { type: "text", label: "Pre-filled message" },
        buttonText: { type: "text" },
        animation: ANIM_FIELD,
      },
      defaultProps: { phone: "", message: "Hi! I'd like to order.", buttonText: "💬 Chat on WhatsApp" },
      render: ({ phone, message, buttonText, animation }) => {
        const n = (phone || "").replace(/\D/g, "");
        const href = n ? `https://wa.me/${n}${message ? `?text=${encodeURIComponent(message)}` : ""}` : "#";
        const reveal = useBlockReveal(animation, "fadeUp");
        return (
          <div {...reveal} style={{ ...wrap, padding: "24px 16px", textAlign: "center" }}>
            <a href={href} target="_blank" rel="noreferrer" className="vula-btn-lift" style={{ display: "inline-block", borderRadius: 999, padding: "14px 32px",
              fontWeight: 700, background: "#25D366", color: "#fff", textDecoration: "none", fontSize: 16, boxShadow: SHADOW_MD }}>{buttonText}</a>
          </div>
        );
      },
    },
    ContactCard: {
      label: "Contact card",
      fields: {
        title: { type: "text" },
        phone: { type: "text" }, email: { type: "text" },
        address: { type: "textarea" }, hours: { type: "textarea" },
        animation: ANIM_FIELD,
      },
      defaultProps: { title: "Get in touch", phone: "", email: "", address: "", hours: "" },
      render: ({ title, phone, email, address, hours, animation }) => {
        const reveal = useBlockReveal(animation, "fadeUp");
        return (
          <section {...reveal} style={{ ...wrap, padding: "40px 16px" }}>
            <div className="vula-card-lift" style={{ borderRadius: 16, border: "1px solid #e5e5e5", padding: 28, maxWidth: 420, margin: "0 auto", boxShadow: SHADOW_SM, background: "#fff" }}>
              {title ? <h3 style={{ fontSize: 22, fontWeight: 800, color: INK, marginBottom: 14 }}>{title}</h3> : null}
              {phone ? <p style={{ margin: "6px 0", color: "#444" }}>📞 <a href={`tel:${phone}`} style={{ color: "#444" }}>{phone}</a></p> : null}
              {email ? <p style={{ margin: "6px 0", color: "#444" }}>✉️ <a href={`mailto:${email}`} style={{ color: "#444" }}>{email}</a></p> : null}
              {address ? <p style={{ margin: "6px 0", color: "#444", whiteSpace: "pre-line" }}>📍 {address}</p> : null}
              {hours ? <p style={{ margin: "6px 0", color: "#444", whiteSpace: "pre-line" }}>🕐 {hours}</p> : null}
            </div>
          </section>
        );
      },
    },
    AnnouncementBar: {
      label: "Announcement bar",
      fields: {
        text: { type: "text" }, linkText: { type: "text", label: "Link text (optional)" }, href: { type: "text" },
      },
      defaultProps: { text: "🎉 Free delivery on orders over R500", linkText: "", href: "" },
      render: ({ text, linkText, href }) => (
        <div style={{ background: ACCENT, color: ACCENT_FG, padding: "10px 16px", textAlign: "center", fontSize: 14, fontWeight: 600 }}>
          {text} {linkText ? <a href={href || "#"} style={{ color: ACCENT_FG, textDecoration: "underline", marginLeft: 6 }}>{linkText}</a> : null}
        </div>
      ),
    },
    Divider: {
      label: "Divider",
      fields: {
        style: { type: "select", options: [{ label: "Solid", value: "solid" }, { label: "Dashed", value: "dashed" }] },
      },
      defaultProps: { style: "solid" },
      render: ({ style }) => (
        <div style={{ ...wrap, padding: "8px 16px" }}>
          <hr style={{ border: "none", borderTop: `1px ${style || "solid"} #e5e5e5` }} />
        </div>
      ),
    },
    Section: {
      label: "Section (background + nested blocks)",
      fields: {
        background: { type: "select", label: "Background", options: [
          { label: "None", value: "none" }, { label: "Solid color", value: "solid" },
          { label: "Gradient", value: "gradient" }, { label: "Image", value: "image" },
        ] },
        backgroundColor: { type: "text", label: "Background color (hex — Solid/Gradient)" },
        backgroundImage: imageField("Background image (if Image)"),
        overlayOpacity: { type: "number", label: "Dark overlay opacity 0-100 (if Image)" },
        padding: { type: "select", label: "Padding", options: [
          { label: "None", value: "none" }, { label: "Small", value: "sm" },
          { label: "Medium", value: "md" }, { label: "Large", value: "lg" },
        ] },
        tinted: { type: "radio", label: "Tint", options: [{ label: "Light brand tint", value: true }, { label: "Plain", value: false }] },
        animation: ANIM_FIELD,
        content: { type: "slot" },
      },
      defaultProps: {
        background: "none", backgroundColor: "", backgroundImage: "", overlayOpacity: 40,
        padding: "md", tinted: false, content: [],
      },
      render: ({ background, backgroundColor, backgroundImage, overlayOpacity, padding, tinted, animation, content: Content }) => {
        const padPx = padding === "none" ? 0 : padding === "sm" ? 24 : padding === "lg" ? 96 : 48;
        const bgStyle =
          background === "solid" ? { backgroundColor: backgroundColor || ACCENT } :
          background === "gradient" ? { backgroundImage: `linear-gradient(135deg, ${backgroundColor || ACCENT} 0%, ${INK} 100%)` } :
          background === "image" && backgroundImage ? { backgroundImage: `url(${backgroundImage})`, backgroundSize: "cover", backgroundPosition: "center" } :
          tinted ? { background: "color-mix(in srgb, var(--brand-accent, #0E7C7B) 6%, white)" } : {};
        const reveal = useBlockReveal(animation, "fadeIn");
        return (
          <section {...reveal} style={{ position: "relative", padding: `${padPx}px 16px`, ...bgStyle }}>
            {background === "image" && backgroundImage ? (
              <div style={{ position: "absolute", inset: 0, background: `rgba(0,0,0,${(overlayOpacity ?? 40) / 100})` }} />
            ) : null}
            <div style={{ position: "relative", zIndex: 1, ...wrap }}>
              <Content style={{ display: "flex", flexDirection: "column", gap: 16 }} />
            </div>
          </section>
        );
      },
    },
    Booking: {
      label: "Booking calendar (live)",
      fields: {
        title: { type: "text" },
        subtitle: { type: "text" },
      },
      defaultProps: { title: "Book an appointment", subtitle: "Pick a service and a time that works for you." },
      render: (props) => <LiveBooking {...props} />,
    },
    FAQ: {
      label: "FAQ",
      fields: {
        title: { type: "text" },
        items: {
          type: "array", arrayFields: { question: { type: "text" }, answer: { type: "textarea" } },
          defaultItemProps: { question: "Question", answer: "Answer." },
        },
      },
      defaultProps: {
        title: "Frequently asked questions",
        items: [
          { question: "Question one", answer: "Answer one." },
          { question: "Question two", answer: "Answer two." },
        ],
      },
      render: ({ title, items }) => {
        const [openIndex, setOpenIndex] = useState(null);
        const listRef = useStaggerReveal();
        return (
          <section style={{ ...wrap, padding: "48px 16px" }}>
            {title ? <h2 style={{ fontSize: 30, fontWeight: 800, textAlign: "center", marginBottom: 32, color: INK }}>{title}</h2> : null}
            <div ref={listRef} style={{ maxWidth: 720, margin: "0 auto", display: "flex", flexDirection: "column", gap: 10 }}>
              {(items || []).map((it, i) => {
                const open = openIndex === i;
                return (
                  <div key={i} className="vula-stagger-item" style={{ ...staggerDelay(i), border: "1px solid #e5e5e5", borderRadius: 12, overflow: "hidden", background: "#fff" }}>
                    <button onClick={() => setOpenIndex(open ? null : i)} style={{ width: "100%", textAlign: "left", padding: "14px 18px", background: "none", border: "none", cursor: "pointer", display: "flex", alignItems: "center", justifyContent: "space-between", fontWeight: 600, fontSize: 15, color: INK }}>
                      {it.question}
                      <span style={{ marginLeft: 12, color: ACCENT, transform: open ? "rotate(45deg)" : "none", transition: "transform 0.2s ease" }}>+</span>
                    </button>
                    {open ? <div style={{ padding: "0 18px 16px", color: "#666", lineHeight: 1.6 }}>{it.answer}</div> : null}
                  </div>
                );
              })}
            </div>
          </section>
        );
      },
    },
    PricingTable: {
      label: "Pricing table",
      fields: {
        title: { type: "text" },
        tiers: {
          type: "array",
          arrayFields: {
            name: { type: "text" }, price: { type: "text" },
            features: { type: "array", arrayFields: { value: { type: "text" } } },
            ctaText: { type: "text", label: "Button text" },
          },
          defaultItemProps: { name: "Plan", price: "R0", features: [], ctaText: "Get started" },
        },
      },
      defaultProps: {
        title: "Pricing",
        tiers: [
          { name: "Basic", price: "R0", features: [{ value: "Feature one" }, { value: "Feature two" }], ctaText: "Get started" },
          { name: "Standard", price: "R0", features: [{ value: "Feature one" }, { value: "Feature two" }, { value: "Feature three" }], ctaText: "Get started" },
        ],
      },
      render: ({ title, tiers }) => {
        const gridRef = useStaggerReveal();
        return (
          <section style={{ ...wrap, padding: "48px 16px" }}>
            {title ? <h2 style={{ fontSize: 30, fontWeight: 800, textAlign: "center", marginBottom: 32, color: INK }}>{title}</h2> : null}
            <div ref={gridRef} style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit,minmax(220px,1fr))", gap: 24 }}>
              {(tiers || []).map((t, i) => (
                <div key={i} className="vula-stagger-item vula-card-lift" style={{ ...staggerDelay(i), border: "1px solid #e5e5e5", borderRadius: 16, padding: 28, textAlign: "center", boxShadow: SHADOW_SM, background: "#fff", display: "flex", flexDirection: "column" }}>
                  <h3 style={{ fontWeight: 700, fontSize: 18, marginBottom: 6, color: INK }}>{t.name}</h3>
                  <div style={{ fontWeight: 800, fontSize: 28, color: ACCENT, marginBottom: 16 }}>{t.price}</div>
                  <ul style={{ listStyle: "none", padding: 0, margin: "0 0 20px", flex: 1, color: "#666", fontSize: 14, lineHeight: 2 }}>
                    {(t.features || []).map((f, j) => <li key={j}>{f.value}</li>)}
                  </ul>
                  {t.ctaText ? (
                    <button className="vula-btn-lift" style={{ padding: "10px 20px", borderRadius: 999, border: "none", background: ACCENT, color: ACCENT_FG, fontWeight: 600, cursor: "pointer" }}>{t.ctaText}</button>
                  ) : null}
                </div>
              ))}
            </div>
          </section>
        );
      },
    },
  },
};

/* ── Live product blocks (2026-07-17) — the "storefront editor" finally shows the catalog.
   Tenant + API base are injected by whichever renderer mounts the page (window.__VULA_PAGE_TENANT
   / __VULA_API, set by VulaPageRender, VulaPages editor, and the storefront PuckRender). Client-
   side fetch so the same block works in the dashboard editor, the Vula public route, and the
   Next.js storefronts. Sale-aware pricing (migration 073). Skeleton loading (2026-07-21) replaces
   the old plain "Loading…" text with shimmer placeholders shaped like the real cards. */

function pageTenant() {
  return (typeof window !== "undefined" && window.__VULA_PAGE_TENANT) || "off-the-hook";
}
function apiBase() {
  return (typeof window !== "undefined" && window.__VULA_API) || "https://vula-group-production.up.railway.app";
}
function priceParts(p) {
  const base = p.price_cents || 0;
  const sale = p.sale_price_cents;
  const active = sale && (!p.sale_ends_at || new Date(p.sale_ends_at) > new Date());
  return { now: active ? sale : base, was: active ? base : null };
}
const R = (c) => `R${(c / 100).toFixed(2)}`;

function ProductSkeletonGrid({ count }) {
  return (
    <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill,minmax(200px,1fr))", gap: 20 }}>
      {Array.from({ length: count || 8 }).map((_, i) => (
        <div key={i} style={{ borderRadius: 14, overflow: "hidden", border: "1px solid #e5e5e5" }}>
          <div className="vula-skeleton" style={{ height: 150 }} />
          <div style={{ padding: "10px 12px" }}>
            <div className="vula-skeleton" style={{ height: 14, width: "70%", borderRadius: 4, marginBottom: 8 }} />
            <div className="vula-skeleton" style={{ height: 14, width: "40%", borderRadius: 4 }} />
          </div>
        </div>
      ))}
    </div>
  );
}

function LiveProducts({ title, category, count, linkBase, mode }) {
  const [products, setProducts] = useState(null);
  const gridRef = useStaggerReveal();
  useEffect(() => {
    fetch(`${apiBase()}/v1/commerce/${pageTenant()}/products`)
      .then((r) => r.json())
      .then((d) => setProducts(d.products || d || []))
      .catch(() => setProducts([]));
  }, []);
  let rows = products || [];
  if (mode === "featured") rows = rows.filter((p) => p.is_daily_catch);
  if (category) rows = rows.filter((p) => p.category === category);
  rows = rows.slice(0, count || 8);
  return (
    <section style={{ ...wrap, padding: "40px 16px" }}>
      {title ? <h2 style={{ fontSize: 30, fontWeight: 800, textAlign: "center", marginBottom: 28, color: INK }}>{title}</h2> : null}
      {products === null ? (
        <ProductSkeletonGrid count={count} />
      ) : rows.length === 0 ? (
        <p style={{ textAlign: "center", color: "#888" }}>No products to show yet.</p>
      ) : (
        <div ref={gridRef} style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill,minmax(200px,1fr))", gap: 20 }}>
          {rows.map((p, i) => {
            const pr = priceParts(p);
            const img = (p.images && p.images[0]) || p.image_url;
            return (
              <a key={p.id || p.slug} href={`${linkBase || "/shop"}/${p.slug}`}
                 className="vula-stagger-item vula-card-lift" style={{ ...staggerDelay(i), textDecoration: "none", color: INK, borderRadius: 14, border: "1px solid #e5e5e5", background: "#fff", display: "block", boxShadow: SHADOW_SM }}>
                <div className="vula-zoom-target" style={{ height: 150, background: "#f3f1ea", backgroundImage: img ? `url(${img})` : undefined, backgroundSize: "cover", backgroundPosition: "center" }} />
                <div style={{ padding: "10px 12px" }}>
                  <div style={{ fontWeight: 600, fontSize: 14.5, marginBottom: 4 }}>{p.name}</div>
                  <div style={{ fontSize: 14 }}>
                    {p.variant_price_range ? (
                      <span style={{ fontWeight: 700, color: ACCENT }}>
                        {p.variant_price_range.min === p.variant_price_range.max ? R(p.variant_price_range.min) : `From ${R(p.variant_price_range.min)}`}
                      </span>
                    ) : (<>
                      <span style={{ fontWeight: 700, color: ACCENT }}>{R(pr.now)}</span>
                      {pr.was ? <span style={{ marginLeft: 6, color: "#999", textDecoration: "line-through", fontSize: 12.5 }}>{R(pr.was)}</span> : null}
                      <span style={{ color: "#888", fontSize: 12 }}>{p.sold_by === "kg" ? " /kg" : ""}</span>
                    </>)}
                  </div>
                </div>
              </a>
            );
          })}
        </div>
      )}
    </section>
  );
}

function LiveCategories({ title, linkBase }) {
  const [cats, setCats] = useState(null);
  const gridRef = useStaggerReveal();
  useEffect(() => {
    fetch(`${apiBase()}/v1/commerce/${pageTenant()}/products`)
      .then((r) => r.json())
      .then((d) => {
        const rows = d.products || d || [];
        const seen = {};
        rows.forEach((p) => { if (p.category) seen[p.category] = (seen[p.category] || 0) + 1; });
        setCats(Object.entries(seen).map(([key, n]) => ({ key, n })));
      })
      .catch(() => setCats([]));
  }, []);
  const label = (k) => k.replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());
  return (
    <section style={{ ...wrap, padding: "40px 16px" }}>
      {title ? <h2 style={{ fontSize: 30, fontWeight: 800, textAlign: "center", marginBottom: 28, color: INK }}>{title}</h2> : null}
      {cats === null ? (
        <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit,minmax(160px,1fr))", gap: 16 }}>
          {Array.from({ length: 4 }).map((_, i) => (
            <div key={i} className="vula-skeleton" style={{ height: 80, borderRadius: 14 }} />
          ))}
        </div>
      ) : (
        <div ref={gridRef} style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit,minmax(160px,1fr))", gap: 16 }}>
          {(cats || []).map((c, i) => (
            <a key={c.key} href={`${linkBase || "/shop"}?category=${c.key}`}
               className="vula-stagger-item vula-card-lift" style={{ ...staggerDelay(i), textDecoration: "none", textAlign: "center", padding: "26px 12px", borderRadius: 14, border: "1px solid #e5e5e5", color: INK, background: "#fff", boxShadow: SHADOW_SM }}>
              <div style={{ fontWeight: 700, fontSize: 15 }}>{label(c.key)}</div>
              <div style={{ color: "#888", fontSize: 12, marginTop: 4 }}>{c.n} item{c.n === 1 ? "" : "s"}</div>
            </a>
          ))}
        </div>
      )}
    </section>
  );
}

/* ── Live booking block (2026-08-14) — wired to the bookings backend (vula/api/bookings.py,
   migration 050), already public/live for the dashboard's own scheduling tab and WhatsApp
   booking flow ("book me Tue 2pm") — this just gives it a storefront face. Same
   pageTenant()/apiBase() plumbing as the live product blocks above. Three steps client-side:
   pick a service, pick a date (fetches that day's real availability), pick a slot + confirm. */

function LiveBooking({ title, subtitle }) {
  const [services, setServices] = useState(null);
  const [serviceId, setServiceId] = useState("");
  const [date, setDate] = useState(() => new Date().toISOString().slice(0, 10));
  const [slots, setSlots] = useState(null);
  const [slot, setSlot] = useState(null);
  const [name, setName] = useState("");
  const [phone, setPhone] = useState("");
  const [status, setStatus] = useState(""); // "" | "booking" | "done" | "error"
  const [errMsg, setErrMsg] = useState("");

  useEffect(() => {
    fetch(`${apiBase()}/v1/bookings/${pageTenant()}/services`)
      .then((r) => r.json())
      .then((d) => {
        const rows = d.services || [];
        setServices(rows);
        if (rows[0]) setServiceId(rows[0].id);
      })
      .catch(() => setServices([]));
  }, []);

  useEffect(() => {
    if (!date) return;
    setSlots(null);
    setSlot(null);
    const q = serviceId ? `&service_id=${serviceId}` : "";
    fetch(`${apiBase()}/v1/bookings/${pageTenant()}/availability?date=${date}${q}`)
      .then((r) => r.json())
      .then((d) => setSlots(d.slots || []))
      .catch(() => setSlots([]));
  }, [date, serviceId]);

  const confirm = async () => {
    if (!slot || !name.trim() || !phone.trim()) return;
    setStatus("booking");
    setErrMsg("");
    try {
      const res = await fetch(`${apiBase()}/v1/bookings/${pageTenant()}`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          service_id: serviceId || undefined, customer_name: name.trim(),
          customer_phone: phone.trim(), start: slot.start_local, channel: "web",
        }),
      }).then((r) => r.json());
      if (res.error) { setStatus("error"); setErrMsg(res.error); return; }
      setStatus("done");
    } catch {
      setStatus("error");
      setErrMsg("Could not complete the booking — please try again.");
    }
  };

  const inputStyle = { padding: "10px 12px", border: "1px solid #ddd", borderRadius: 8, fontSize: 14, width: "100%", boxSizing: "border-box" };
  const reveal = useReveal("fadeUp");

  if (status === "done") {
    return (
      <section {...reveal} className="vula-reveal" style={{ ...wrap, padding: "48px 16px", textAlign: "center" }}>
        <h2 style={{ fontSize: 26, fontWeight: 800, color: INK, marginBottom: 8 }}>Booked ✓</h2>
        <p style={{ color: "#666" }}>You're confirmed for {slot?.label || slot?.start_local}. We'll be in touch if anything changes.</p>
      </section>
    );
  }

  return (
    <section {...reveal} className="vula-reveal" style={{ ...wrap, padding: "48px 16px" }}>
      {title ? <h2 style={{ fontSize: 30, fontWeight: 800, textAlign: "center", marginBottom: 8, color: INK }}>{title}</h2> : null}
      {subtitle ? <p style={{ textAlign: "center", color: "#666", marginBottom: 28 }}>{subtitle}</p> : null}
      <div style={{ maxWidth: 480, margin: "0 auto", display: "flex", flexDirection: "column", gap: 14 }}>
        {services === null ? (
          <div className="vula-skeleton" style={{ height: 44, borderRadius: 8 }} />
        ) : services.length > 0 ? (
          <select value={serviceId} onChange={(e) => setServiceId(e.target.value)} style={inputStyle}>
            {services.map((s) => <option key={s.id} value={s.id}>{s.name}</option>)}
          </select>
        ) : null}
        <input type="date" value={date} min={new Date().toISOString().slice(0, 10)}
          onChange={(e) => setDate(e.target.value)} style={inputStyle} />
        {slots === null ? (
          <div className="vula-skeleton" style={{ height: 80, borderRadius: 8 }} />
        ) : slots.length === 0 ? (
          <p style={{ color: "#888", fontSize: 14, textAlign: "center" }}>No times available that day — try another date.</p>
        ) : (
          <div style={{ display: "flex", flexWrap: "wrap", gap: 8 }}>
            {slots.map((s) => (
              <button key={s.start_utc} onClick={() => setSlot(s)}
                style={{ padding: "8px 14px", borderRadius: 999, border: `1px solid ${slot?.start_utc === s.start_utc ? ACCENT : "#ddd"}`,
                  background: slot?.start_utc === s.start_utc ? ACCENT : "#fff", color: slot?.start_utc === s.start_utc ? ACCENT_FG : INK,
                  cursor: "pointer", fontSize: 13.5 }}>
                {s.label || s.start_local}
              </button>
            ))}
          </div>
        )}
        {slot ? (
          <>
            <input placeholder="Your name" value={name} onChange={(e) => setName(e.target.value)} style={inputStyle} />
            <input placeholder="Your phone (WhatsApp)" value={phone} onChange={(e) => setPhone(e.target.value)} style={inputStyle} />
            {errMsg ? <p style={{ color: "#c0392b", fontSize: 13 }}>{errMsg}</p> : null}
            <button onClick={confirm} disabled={status === "booking" || !name.trim() || !phone.trim()}
              className="vula-btn-lift" style={{ padding: "12px 20px", borderRadius: 999, border: "none", background: ACCENT, color: ACCENT_FG, fontWeight: 600, cursor: "pointer" }}>
              {status === "booking" ? "Booking…" : "Confirm booking"}
            </button>
          </>
        ) : null}
      </div>
    </section>
  );
}

export default config;
