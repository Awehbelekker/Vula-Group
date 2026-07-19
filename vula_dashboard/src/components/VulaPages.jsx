/**
 * VulaPages.jsx — Puck drag-and-drop page builder, available to EVERY tenant from the dashboard.
 * Lists a tenant's pages, creates new ones, and edits them with the shared Puck config. Saves to
 * the tenant-scoped pages API. Published pages render at /pages/:tenant/:slug (public route) and,
 * for storefronts that ship the matching renderer (e.g. OTH), at the storefront's /p/:slug too.
 *
 * Editor depth (2026-07-17): draft-vs-publish, SEO fields, rename, duplicate, starter templates,
 * and live product blocks (window.__VULA_PAGE_TENANT injected for them).
 */
import { useState, useEffect, useRef } from "react";
import { Puck } from "@measured/puck";
import "@measured/puck/puck.css";
import { config } from "../puck/config";

const VULA_API = import.meta.env.VITE_API_URL || "https://vula-group-production.up.railway.app";
const C = { surface: "#FFFFFF", border: "#DDD8CE", text: "#2A2A2A", muted: "#8A8680" };

function norm(data) {
  const d = data && typeof data === "object" ? data : {};
  return {
    content: Array.isArray(d.content) ? d.content : [],
    root: { props: (d.root && (d.root.props || d.root)) || {} },
  };
}

// Preview viewport switcher (P4) — Puck renders its own Monitor/Tablet/Smartphone toolbar icons
// once given a list, no custom UI needed.
const PREVIEW_VIEWPORTS = [
  { width: 1440, height: "auto", label: "Desktop", icon: "Monitor" },
  { width: 768, height: "auto", label: "Tablet", icon: "Tablet" },
  { width: 390, height: "auto", label: "Mobile", icon: "Smartphone" },
];

const btn = (bg) => ({ padding: "8px 14px", border: "none", borderRadius: 8, fontSize: 13, fontWeight: 600, color: "#fff", background: bg, cursor: "pointer" });
const rowStyle = { textAlign: "left", padding: "10px 12px", border: `1px solid #DDD8CE`, borderRadius: 8, background: "#FAF9F6", cursor: "pointer" };
const ghost = { padding: "8px 12px", border: `1px solid ${C.border}`, background: C.surface, color: C.text, borderRadius: 8, cursor: "pointer", fontSize: 13 };

// Starter templates — a page is never a blank scary canvas.
const TEMPLATES = {
  blank: { label: "Blank page", data: {} },
  home: {
    label: "Store home (hero + featured + categories)",
    data: { content: [
      { type: "Hero", props: { id: "t-hero", title: "Fresh from the harbour", subtitle: "Order before 10am for same-day delivery.", image: "", ctaText: "Shop now", ctaHref: "/shop" } },
      { type: "FeaturedProducts", props: { id: "t-feat", title: "Today's catch", count: 4, linkBase: "/shop" } },
      { type: "CategoryNav", props: { id: "t-cats", title: "Browse by category", linkBase: "/shop" } },
      { type: "Features", props: { id: "t-why", title: "Why shop with us", items: [
        { heading: "Fresh", body: "Caught daily." }, { heading: "Local", body: "Cape Town sourced." }, { heading: "Delivered", body: "To your door." }] } },
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

export default function VulaPages({ tenantId }) {
  const [pages, setPages] = useState(null);
  const [editing, setEditing] = useState(null);   // { slug, title, data, status, seo }
  const [creating, setCreating] = useState(false);
  const [newForm, setNewForm] = useState({ title: "", template: "blank" });
  const [showSeo, setShowSeo] = useState(false);
  const [showHistory, setShowHistory] = useState(false);
  const [versions, setVersions] = useState(null);
  const [msg, setMsg] = useState("");
  const [storeUrl, setStoreUrl] = useState(null);
  const [showLivePreview, setShowLivePreview] = useState(false);
  const latestData = useRef(null);   // Puck's live document (survives re-renders/saves)

  // Live product blocks in the editor preview need the tenant + API globals.
  useEffect(() => {
    window.__VULA_PAGE_TENANT = tenantId;
    window.__VULA_API = VULA_API;
  }, [tenantId]);

  // The tenant's actual live website — so "what does my site look like right now" is answerable
  // without leaving the dashboard. Falls back to the Vula-hosted page renderer if no custom
  // domain is configured yet.
  useEffect(() => {
    fetch(`${VULA_API}/v1/tenants/${tenantId}`).then((r) => r.json())
      .then((d) => setStoreUrl(d.store_url || null)).catch(() => setStoreUrl(null));
  }, [tenantId]);
  const liveUrl = storeUrl || `${window.location.origin}/#/page/${tenantId}/home`;

  const load = () =>
    fetch(`${VULA_API}/v1/commerce/${tenantId}/admin/pages`)
      .then((r) => r.json()).then((d) => setPages(d.pages || [])).catch(() => setPages([]));
  useEffect(() => { load(); }, [tenantId]);  // eslint-disable-line

  const open = async (slug, title) => {
    const p = await fetch(`${VULA_API}/v1/commerce/${tenantId}/admin/pages/${slug}`).then((r) => r.json()).catch(() => ({}));
    setEditing({ slug, title: p.title || title || slug, data: norm(p.puck_data), status: p.status || "draft", seo: p.seo || {} });
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
    const raw = window.prompt("New page name:", p.title || p.slug);
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
    if (!window.confirm(`Delete the page "${title || slug}"? This can't be undone.`)) return;
    await fetch(`${VULA_API}/v1/commerce/${tenantId}/admin/pages/${slug}`, { method: "DELETE" }).catch(() => {});
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
    if (!window.confirm("Restore this version? It'll come back as a draft — review before publishing.")) return;
    await fetch(`${VULA_API}/v1/commerce/${tenantId}/admin/pages/${editing.slug}/versions/${versionId}/restore`, { method: "POST" }).catch(() => {});
    const p = await fetch(`${VULA_API}/v1/commerce/${tenantId}/admin/pages/${editing.slug}`).then((r) => r.json()).catch(() => ({}));
    setEditing((e) => ({ ...e, data: norm(p.puck_data), status: p.status || "draft", title: p.title || e.title, seo: p.seo || {} }));
    setShowHistory(false);
    setMsg("Restored as draft ✓");
    setTimeout(() => setMsg(""), 2000);
  };

  if (editing) {
    const publicUrl = `${window.location.origin}/#/page/${tenantId}/${editing.slug}`;
    return (
      <div>
        <div style={{ display: "flex", alignItems: "center", gap: 10, padding: "10px 0", flexWrap: "wrap" }}>
          <button onClick={() => { setEditing(null); load(); }} style={btn("#6B7280")}>← Pages</button>
          <strong style={{ color: C.text }}>{editing.title}</strong>
          <span style={{ color: C.muted, fontSize: 12 }}>/{editing.slug}</span>
          <span style={{ fontSize: 11, fontWeight: 600, borderRadius: 999, padding: "2px 9px",
            color: editing.status === "published" ? "#2C7A4B" : "#B7791F",
            background: editing.status === "published" ? "rgba(44,122,75,.12)" : "rgba(183,121,31,.12)" }}>
            {editing.status === "published" ? "Live" : "Draft"}
          </span>
          {msg && <span style={{ color: "#2C7A4B", fontSize: 12 }}>{msg}</span>}
          <div style={{ marginLeft: "auto", display: "flex", gap: 8, alignItems: "center" }}>
            <button onClick={() => setShowSeo(s => !s)} style={ghost}>🔍 SEO</button>
            <button onClick={() => { setShowHistory(v => !v); if (!showHistory) loadVersions(); }} style={ghost}>🕐 History</button>
            <a href={publicUrl} target="_blank" rel="noreferrer" style={{ fontSize: 13 }}>View live ↗</a>
          </div>
        </div>

        {showHistory && (
          <div style={{ margin: "0 0 10px", background: "#FAF9F6", border: `1px solid ${C.border}`, borderRadius: 8, padding: 10 }}>
            {versions === null ? <span style={{ fontSize: 12, color: C.muted }}>Loading…</span> :
              versions.length === 0 ? <span style={{ fontSize: 12, color: C.muted }}>No earlier versions yet — a snapshot is taken every time you publish over an existing page.</span> :
                <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
                  {versions.map((v) => (
                    <div key={v.id} style={{ display: "flex", alignItems: "center", gap: 8, fontSize: 12.5 }}>
                      <span style={{ color: C.muted, minWidth: 140 }}>{new Date(v.created_at).toLocaleString("en-ZA")}</span>
                      <span style={{ flex: 1 }}>{v.title}</span>
                      <button onClick={() => restoreVersion(v.id)} style={{ ...ghost, padding: "4px 10px", fontSize: 11 }}>Restore as draft</button>
                    </div>
                  ))}
                </div>}
          </div>
        )}

        {showSeo && (
          <div style={{ display: "flex", gap: 8, flexWrap: "wrap", margin: "0 0 10px", background: "#FAF9F6", border: `1px solid ${C.border}`, borderRadius: 8, padding: 10 }}>
            <input placeholder="SEO title (browser tab / Google)" value={editing.seo?.title || ""}
              onChange={(e) => setEditing((ed) => ({ ...ed, seo: { ...ed.seo, title: e.target.value } }))}
              style={{ flex: 1, minWidth: 200, padding: "7px 10px", border: `1px solid ${C.border}`, borderRadius: 6, fontSize: 13 }} />
            <input placeholder="SEO description (search result snippet)" value={editing.seo?.description || ""}
              onChange={(e) => setEditing((ed) => ({ ...ed, seo: { ...ed.seo, description: e.target.value } }))}
              style={{ flex: 2, minWidth: 260, padding: "7px 10px", border: `1px solid ${C.border}`, borderRadius: 6, fontSize: 13 }} />
            <span style={{ fontSize: 11, color: C.muted, alignSelf: "center" }}>Saved with the page on Publish / Save draft.</span>
          </div>
        )}

        <p style={{ fontSize: 12, color: C.muted, margin: "0 0 8px" }}>
          Drag blocks from the left (including <strong>live product blocks</strong>), edit on the right —
          <strong> Publish</strong> makes it live, or use <strong>Save draft</strong> below to keep working privately.
        </p>
        <div style={{ height: "74vh", border: `1px solid ${C.border}`, borderRadius: 10, overflow: "hidden" }}>
          <Puck config={config} data={editing.data} onPublish={(data) => persist(data, "published")}
            onChange={(data) => { latestData.current = data; }}
            viewports={PREVIEW_VIEWPORTS} />
        </div>
        <div style={{ display: "flex", gap: 8, marginTop: 8 }}>
          <button onClick={() => persist(latestData.current || editing.data, "draft")} style={ghost}>💾 Save draft</button>
          {editing.status === "published" && (
            <button onClick={() => persist(latestData.current || editing.data, "draft")} style={{ ...ghost, color: "#B7791F" }}>⏸ Unpublish (back to draft)</button>
          )}
        </div>
      </div>
    );
  }

  return (
    <div style={{ background: C.surface, border: `1px solid ${C.border}`, borderRadius: 10, padding: 16 }}>
      <div style={{ display: "flex", alignItems: "center" }}>
        <strong style={{ color: C.text }}>Storefront pages</strong>
        <button onClick={() => setCreating((c) => !c)} style={{ ...btn("var(--accent)"), marginLeft: "auto" }}>{creating ? "Close" : "+ New page"}</button>
      </div>
      <p style={{ fontSize: 12, color: C.muted, margin: "4px 0 12px" }}>
        Build your store's pages by drag-and-drop — including live product grids that always show your
        current catalog and prices. Published pages appear on your website automatically.
      </p>

      <div style={{ marginBottom: 16 }}>
        <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: showLivePreview ? 8 : 0 }}>
          <button onClick={() => setShowLivePreview((v) => !v)} style={ghost}>
            {showLivePreview ? "▲ Hide" : "▼ Show"} current live site
          </button>
          {storeUrl && <a href={liveUrl} target="_blank" rel="noreferrer" style={{ fontSize: 12 }}>Open in new tab ↗</a>}
          {!storeUrl && <span style={{ fontSize: 11, color: C.muted }}>(no custom domain configured — previewing the Vula-hosted page)</span>}
        </div>
        {showLivePreview && (
          <div style={{ border: `1px solid ${C.border}`, borderRadius: 8, overflow: "hidden", height: 480, background: "#FAF9F6" }}>
            <iframe
              key={liveUrl}
              src={liveUrl}
              title="Current live site"
              style={{ width: "100%", height: "100%", border: "none" }}
              onError={(e) => { e.target.style.display = "none"; }}
            />
          </div>
        )}
      </div>

      {creating && (
        <div style={{ display: "flex", gap: 8, flexWrap: "wrap", marginBottom: 12, background: "#FAF9F6", border: `1px solid ${C.border}`, borderRadius: 8, padding: 10 }}>
          <input placeholder="Page name (e.g. Winter Specials)" value={newForm.title} autoFocus
            onChange={(e) => setNewForm((f) => ({ ...f, title: e.target.value }))}
            onKeyDown={(e) => e.key === "Enter" && createNew()}
            style={{ flex: 1, minWidth: 180, padding: "8px 10px", border: `1px solid ${C.border}`, borderRadius: 6, fontSize: 13 }} />
          <select value={newForm.template} onChange={(e) => setNewForm((f) => ({ ...f, template: e.target.value }))}
            style={{ padding: "8px 10px", border: `1px solid ${C.border}`, borderRadius: 6, fontSize: 13 }}>
            {Object.entries(TEMPLATES).map(([k, t]) => <option key={k} value={k}>{t.label}</option>)}
          </select>
          <button onClick={createNew} style={btn("var(--accent)")}>Create</button>
        </div>
      )}

      {pages === null ? <p style={{ color: C.muted }}>Loading…</p> :
        pages.length === 0 ? <p style={{ color: C.muted }}>No pages yet — create your first one (try the "Store home" template).</p> :
          <div style={{ display: "grid", gap: 8 }}>
            {pages.map((p, i) => (
              <div key={p.slug} style={{ display: "flex", alignItems: "center", gap: 6 }}>
                <div style={{ display: "flex", flexDirection: "column" }}>
                  <button onClick={() => move(i, -1)} disabled={i === 0} title="Move up"
                    style={{ ...ghost, padding: "1px 8px", fontSize: 10, opacity: i === 0 ? 0.3 : 1, borderBottom: "none", borderRadius: "6px 6px 0 0" }}>▲</button>
                  <button onClick={() => move(i, 1)} disabled={i === pages.length - 1} title="Move down"
                    style={{ ...ghost, padding: "1px 8px", fontSize: 10, opacity: i === pages.length - 1 ? 0.3 : 1, borderRadius: "0 0 6px 6px" }}>▼</button>
                </div>
                <button onClick={() => open(p.slug, p.title)} style={{ ...rowStyle, flex: 1 }}>
                  <span style={{ fontWeight: 600, color: C.text }}>{p.title || p.slug}</span>
                  <span style={{ color: C.muted, fontSize: 12 }}>  /{p.slug}</span>
                  <span style={{ fontSize: 11, fontWeight: 600, marginLeft: 8,
                    color: p.status === "published" ? "#2C7A4B" : "#B7791F" }}>
                    {p.status === "published" ? "● Live" : "○ Draft"}
                  </span>
                </button>
                <button onClick={() => rename(p)} title="Rename" style={{ ...ghost, padding: "8px 10px" }}>✏️</button>
                <button onClick={() => duplicate(p)} title="Duplicate" style={{ ...ghost, padding: "8px 10px" }}>⧉</button>
                <button onClick={() => del(p.slug, p.title)} title="Delete page"
                  style={{ border: `1px solid ${C.border}`, background: C.surface, color: "#A23B2D", borderRadius: 8, padding: "8px 12px", cursor: "pointer", fontSize: 14 }}>
                  🗑
                </button>
              </div>
            ))}
          </div>}
    </div>
  );
}
