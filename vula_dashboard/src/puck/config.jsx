/**
 * Puck block library for the Vula dashboard page builder — shared by the editor (VulaPages) and
 * the public render route (/pages/:tenant/:slug). Block types + prop names match the OTH storefront
 * config (off_the_hook/src/puck.config.tsx) so a page built here also renders on a tenant storefront
 * that has the matching renderer. Uses inline styles + <a> (no Tailwind / next-link) so it renders
 * self-contained anywhere. Brand flows through CSS vars with sensible fallbacks.
 */
import VulaImageUpload from "../components/VulaImageUpload";

const ACCENT = "var(--brand-accent, #0E7C7B)";
const ACCENT_FG = "var(--brand-accent-fg, #ffffff)";
const INK = "var(--brand-ink, #1a1a1a)";
const FONT_DISPLAY = "var(--brand-font-display, inherit)";
const wrap = { maxWidth: 1100, margin: "0 auto", padding: "0 16px" };

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
  components: {
    Hero: {
      label: "Hero",
      fields: {
        title: { type: "text" }, subtitle: { type: "textarea" },
        image: imageField("Background image"),
        ctaText: { type: "text", label: "Button text" }, ctaHref: { type: "text", label: "Button link" },
      },
      defaultProps: { title: "Your headline", subtitle: "A short supporting line.", image: "", ctaText: "Shop now", ctaHref: "/shop" },
      render: ({ title, subtitle, image, ctaText, ctaHref }) => (
        <section style={{ position: "relative", display: "flex", alignItems: "center", justifyContent: "center",
          textAlign: "center", color: "#fff", minHeight: 420, backgroundColor: ACCENT,
          backgroundImage: image ? `url(${image})` : undefined, backgroundSize: "cover", backgroundPosition: "center" }}>
          <div style={{ position: "absolute", inset: 0, background: "rgba(0,0,0,0.4)" }} />
          <div style={{ position: "relative", zIndex: 1, maxWidth: 760, padding: "80px 16px" }}>
            <h1 style={{ fontSize: 44, fontWeight: 800, marginBottom: 16, fontFamily: FONT_DISPLAY }}>{title}</h1>
            {subtitle ? <p style={{ fontSize: 18, opacity: 0.9, marginBottom: 24 }}>{subtitle}</p> : null}
            {ctaText ? (
              <a href={ctaHref || "#"} style={{ display: "inline-block", borderRadius: 999, padding: "12px 28px",
                fontWeight: 600, background: ACCENT_FG, color: INK, textDecoration: "none" }}>{ctaText}</a>
            ) : null}
          </div>
        </section>
      ),
    },
    Heading: {
      label: "Heading",
      fields: {
        text: { type: "text" },
        level: { type: "select", options: [{ label: "H1", value: "h1" }, { label: "H2", value: "h2" }, { label: "H3", value: "h3" }] },
        align: { type: "select", options: [{ label: "Left", value: "left" }, { label: "Center", value: "center" }] },
      },
      defaultProps: { text: "Section heading", level: "h2", align: "left" },
      render: ({ text, level, align }) => {
        const size = level === "h1" ? 40 : level === "h2" ? 30 : 24;
        const Tag = level || "h2";
        return (
          <div style={{ ...wrap, padding: "24px 16px" }}>
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
      },
      defaultProps: { text: "Write your content here.", align: "left" },
      render: ({ text, align }) => (
        <div style={{ ...wrap, padding: "12px 16px" }}>
          <p style={{ lineHeight: 1.7, whiteSpace: "pre-line", maxWidth: 760, color: "#555",
            margin: align === "center" ? "0 auto" : 0, textAlign: align === "center" ? "center" : "left" }}>{text}</p>
        </div>
      ),
    },
    ImageBlock: {
      label: "Image",
      fields: {
        src: imageField("Image"), alt: { type: "text" },
        rounded: { type: "radio", options: [{ label: "Rounded", value: true }, { label: "Square", value: false }] },
      },
      defaultProps: { src: "", alt: "", rounded: true },
      render: ({ src, alt, rounded }) => (
        <div style={{ ...wrap, padding: "24px 16px" }}>
          {src ? <img src={src} alt={alt} style={{ width: "100%", objectFit: "cover", borderRadius: rounded ? 16 : 0 }} />
               : <div style={{ height: 192, background: "#f1f1f1", borderRadius: rounded ? 16 : 0 }} />}
        </div>
      ),
    },
    CTA: {
      label: "Button",
      fields: {
        text: { type: "text" }, href: { type: "text" },
        variant: { type: "select", options: [{ label: "Solid", value: "solid" }, { label: "Outline", value: "outline" }] },
      },
      defaultProps: { text: "Get in touch", href: "/contact", variant: "solid" },
      render: ({ text, href, variant }) => (
        <div style={{ ...wrap, padding: "20px 16px", textAlign: "center" }}>
          <a href={href || "#"} style={{ display: "inline-block", borderRadius: 999, padding: "12px 28px",
            fontWeight: 600, border: `1px solid ${ACCENT}`, textDecoration: "none",
            ...(variant === "solid" ? { background: ACCENT, color: ACCENT_FG } : { color: ACCENT }) }}>{text}</a>
        </div>
      ),
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
      render: ({ title, items }) => (
        <section style={{ ...wrap, padding: "48px 16px" }}>
          {title ? <h2 style={{ fontSize: 30, fontWeight: 800, textAlign: "center", marginBottom: 32, color: INK }}>{title}</h2> : null}
          <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit,minmax(220px,1fr))", gap: 24 }}>
            {(items || []).map((it, i) => (
              <div key={i} style={{ borderRadius: 16, border: "1px solid #e5e5e5", padding: 24, textAlign: "center" }}>
                <h3 style={{ fontWeight: 600, fontSize: 18, marginBottom: 8, color: INK }}>{it.heading}</h3>
                <p style={{ color: "#666", fontSize: 14 }}>{it.body}</p>
              </div>
            ))}
          </div>
        </section>
      ),
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
      render: ({ leftImage, leftHeading, leftBody, rightImage, rightHeading, rightBody }) => (
        <section style={{ ...wrap, padding: "40px 16px", display: "grid", gridTemplateColumns: "repeat(auto-fit,minmax(260px,1fr))", gap: 32 }}>
          {[{ img: leftImage, h: leftHeading, b: leftBody }, { img: rightImage, h: rightHeading, b: rightBody }].map((col, i) => (
            <div key={i}>
              {col.img ? <img src={col.img} alt="" style={{ width: "100%", borderRadius: 12, marginBottom: 16, objectFit: "cover", maxHeight: 240 }} /> : null}
              {col.h ? <h3 style={{ fontSize: 22, fontWeight: 800, color: INK, marginBottom: 8 }}>{col.h}</h3> : null}
              {col.b ? <p style={{ color: "#666", lineHeight: 1.7, whiteSpace: "pre-line" }}>{col.b}</p> : null}
            </div>
          ))}
        </section>
      ),
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
      render: ({ title, images }) => (
        <section style={{ ...wrap, padding: "40px 16px" }}>
          {title ? <h2 style={{ fontSize: 30, fontWeight: 800, textAlign: "center", marginBottom: 28, color: INK }}>{title}</h2> : null}
          <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill,minmax(180px,1fr))", gap: 12 }}>
            {(images || []).filter(im => im.src).map((im, i) => (
              <img key={i} src={im.src} alt={im.alt || ""} style={{ width: "100%", height: 160, objectFit: "cover", borderRadius: 12 }} />
            ))}
          </div>
          {(!images || images.every(im => !im.src)) && <p style={{ textAlign: "center", color: "#888" }}>Add images in the editor →</p>}
        </section>
      ),
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
      render: ({ title, items }) => (
        <section style={{ ...wrap, padding: "40px 16px" }}>
          {title ? <h2 style={{ fontSize: 30, fontWeight: 800, textAlign: "center", marginBottom: 28, color: INK }}>{title}</h2> : null}
          <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit,minmax(240px,1fr))", gap: 20 }}>
            {(items || []).map((it, i) => (
              <div key={i} style={{ borderRadius: 16, border: "1px solid #e5e5e5", padding: 24, background: "#fafafa" }}>
                <p style={{ fontStyle: "italic", color: "#444", lineHeight: 1.6, marginBottom: 12 }}>&ldquo;{it.quote}&rdquo;</p>
                <div style={{ fontWeight: 700, color: INK, fontSize: 14 }}>{it.author}</div>
                {it.role ? <div style={{ color: "#888", fontSize: 12.5 }}>{it.role}</div> : null}
              </div>
            ))}
          </div>
        </section>
      ),
    },
    VideoEmbed: {
      label: "Video",
      fields: { url: { type: "text", label: "YouTube / Vimeo / direct video URL" }, caption: { type: "text" } },
      defaultProps: { url: "", caption: "" },
      render: ({ url, caption }) => {
        const yt = (url || "").match(/(?:youtu\.be\/|youtube\.com\/watch\?v=|youtube\.com\/embed\/)([\w-]+)/);
        const vimeo = (url || "").match(/vimeo\.com\/(\d+)/);
        const embedSrc = yt ? `https://www.youtube.com/embed/${yt[1]}` : vimeo ? `https://player.vimeo.com/video/${vimeo[1]}` : null;
        return (
          <section style={{ ...wrap, padding: "40px 16px" }}>
            <div style={{ position: "relative", paddingTop: "56.25%", borderRadius: 14, overflow: "hidden", background: "#000" }}>
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
      },
      defaultProps: { phone: "", message: "Hi! I'd like to order.", buttonText: "💬 Chat on WhatsApp" },
      render: ({ phone, message, buttonText }) => {
        const n = (phone || "").replace(/\D/g, "");
        const href = n ? `https://wa.me/${n}${message ? `?text=${encodeURIComponent(message)}` : ""}` : "#";
        return (
          <div style={{ ...wrap, padding: "24px 16px", textAlign: "center" }}>
            <a href={href} target="_blank" rel="noreferrer" style={{ display: "inline-block", borderRadius: 999, padding: "14px 32px",
              fontWeight: 700, background: "#25D366", color: "#fff", textDecoration: "none", fontSize: 16 }}>{buttonText}</a>
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
      },
      defaultProps: { title: "Get in touch", phone: "", email: "", address: "", hours: "" },
      render: ({ title, phone, email, address, hours }) => (
        <section style={{ ...wrap, padding: "40px 16px" }}>
          <div style={{ borderRadius: 16, border: "1px solid #e5e5e5", padding: 28, maxWidth: 420, margin: "0 auto" }}>
            {title ? <h3 style={{ fontSize: 22, fontWeight: 800, color: INK, marginBottom: 14 }}>{title}</h3> : null}
            {phone ? <p style={{ margin: "6px 0", color: "#444" }}>📞 <a href={`tel:${phone}`} style={{ color: "#444" }}>{phone}</a></p> : null}
            {email ? <p style={{ margin: "6px 0", color: "#444" }}>✉️ <a href={`mailto:${email}`} style={{ color: "#444" }}>{email}</a></p> : null}
            {address ? <p style={{ margin: "6px 0", color: "#444", whiteSpace: "pre-line" }}>📍 {address}</p> : null}
            {hours ? <p style={{ margin: "6px 0", color: "#444", whiteSpace: "pre-line" }}>🕐 {hours}</p> : null}
          </div>
        </section>
      ),
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
  },
};

/* ── Live product blocks (2026-07-17) — the "storefront editor" finally shows the catalog.
   Tenant + API base are injected by whichever renderer mounts the page (window.__VULA_PAGE_TENANT
   / __VULA_API, set by VulaPageRender, VulaPages editor, and the storefront PuckRender). Client-
   side fetch so the same block works in the dashboard editor, the Vula public route, and the
   Next.js storefronts. Sale-aware pricing (migration 073). */
import { useEffect, useState } from "react";

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

function LiveProducts({ title, category, count, linkBase, mode }) {
  const [products, setProducts] = useState(null);
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
        <p style={{ textAlign: "center", color: "#888" }}>Loading products…</p>
      ) : rows.length === 0 ? (
        <p style={{ textAlign: "center", color: "#888" }}>No products to show yet.</p>
      ) : (
        <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill,minmax(200px,1fr))", gap: 20 }}>
          {rows.map((p) => {
            const pr = priceParts(p);
            const img = (p.images && p.images[0]) || p.image_url;
            return (
              <a key={p.id || p.slug} href={`${linkBase || "/shop"}/${p.slug}`}
                 style={{ textDecoration: "none", color: INK, borderRadius: 14, border: "1px solid #e5e5e5", overflow: "hidden", background: "#fff", display: "block" }}>
                <div style={{ height: 150, background: "#f3f1ea", backgroundImage: img ? `url(${img})` : undefined, backgroundSize: "cover", backgroundPosition: "center" }} />
                <div style={{ padding: "10px 12px" }}>
                  <div style={{ fontWeight: 600, fontSize: 14.5, marginBottom: 4 }}>{p.name}</div>
                  <div style={{ fontSize: 14 }}>
                    <span style={{ fontWeight: 700, color: ACCENT }}>{R(pr.now)}</span>
                    {pr.was ? <span style={{ marginLeft: 6, color: "#999", textDecoration: "line-through", fontSize: 12.5 }}>{R(pr.was)}</span> : null}
                    <span style={{ color: "#888", fontSize: 12 }}>{p.sold_by === "kg" ? " /kg" : ""}</span>
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
      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit,minmax(160px,1fr))", gap: 16 }}>
        {(cats || []).map((c) => (
          <a key={c.key} href={`${linkBase || "/shop"}?category=${c.key}`}
             style={{ textDecoration: "none", textAlign: "center", padding: "26px 12px", borderRadius: 14, border: "1px solid #e5e5e5", color: INK, background: "#fff" }}>
            <div style={{ fontWeight: 700, fontSize: 15 }}>{label(c.key)}</div>
            <div style={{ color: "#888", fontSize: 12, marginTop: 4 }}>{c.n} item{c.n === 1 ? "" : "s"}</div>
          </a>
        ))}
      </div>
    </section>
  );
}

export default config;
