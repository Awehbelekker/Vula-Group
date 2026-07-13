/**
 * Puck block library for the Vula dashboard page builder — shared by the editor (VulaPages) and
 * the public render route (/pages/:tenant/:slug). Block types + prop names match the OTH storefront
 * config (off_the_hook/src/puck.config.tsx) so a page built here also renders on a tenant storefront
 * that has the matching renderer. Uses inline styles + <a> (no Tailwind / next-link) so it renders
 * self-contained anywhere. Brand flows through CSS vars with sensible fallbacks.
 */
const ACCENT = "var(--brand-accent, #0E7C7B)";
const ACCENT_FG = "var(--brand-accent-fg, #ffffff)";
const INK = "var(--brand-ink, #1a1a1a)";
const wrap = { maxWidth: 1100, margin: "0 auto", padding: "0 16px" };

export const config = {
  components: {
    Hero: {
      label: "Hero",
      fields: {
        title: { type: "text" }, subtitle: { type: "textarea" },
        image: { type: "text", label: "Background image URL" },
        ctaText: { type: "text", label: "Button text" }, ctaHref: { type: "text", label: "Button link" },
      },
      defaultProps: { title: "Your headline", subtitle: "A short supporting line.", image: "", ctaText: "Shop now", ctaHref: "/shop" },
      render: ({ title, subtitle, image, ctaText, ctaHref }) => (
        <section style={{ position: "relative", display: "flex", alignItems: "center", justifyContent: "center",
          textAlign: "center", color: "#fff", minHeight: 420, backgroundColor: ACCENT,
          backgroundImage: image ? `url(${image})` : undefined, backgroundSize: "cover", backgroundPosition: "center" }}>
          <div style={{ position: "absolute", inset: 0, background: "rgba(0,0,0,0.4)" }} />
          <div style={{ position: "relative", zIndex: 1, maxWidth: 760, padding: "80px 16px" }}>
            <h1 style={{ fontSize: 44, fontWeight: 800, marginBottom: 16 }}>{title}</h1>
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
            <Tag style={{ fontSize: size, fontWeight: 800, textAlign: align === "center" ? "center" : "left", color: INK, margin: 0 }}>{text}</Tag>
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
      fields: { src: { type: "text", label: "Image URL" }, alt: { type: "text" } },
      defaultProps: { src: "", alt: "" },
      render: ({ src, alt }) => (
        <div style={{ ...wrap, padding: "24px 16px" }}>
          {src ? <img src={src} alt={alt} style={{ width: "100%", objectFit: "cover", borderRadius: 16 }} />
               : <div style={{ height: 192, background: "#f1f1f1", borderRadius: 16 }} />}
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
  },
};

export default config;
