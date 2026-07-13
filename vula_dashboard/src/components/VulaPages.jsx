/**
 * VulaPages.jsx — Puck drag-and-drop page builder, available to EVERY tenant from the dashboard.
 * Lists a tenant's pages, creates new ones, and edits them with the shared Puck config. Saves to
 * the tenant-scoped pages API. Published pages render at /pages/:tenant/:slug (public route) and,
 * for storefronts that ship the matching renderer (e.g. OTH), at the storefront's /p/:slug too.
 */
import { useState, useEffect } from "react";
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

const btn = (bg) => ({ padding: "8px 14px", border: "none", borderRadius: 8, fontSize: 13, fontWeight: 600, color: "#fff", background: bg, cursor: "pointer" });
const rowStyle = { textAlign: "left", padding: "10px 12px", border: `1px solid ${C.border}`, borderRadius: 8, background: "#FAF9F6", cursor: "pointer" };

export default function VulaPages({ tenantId }) {
  const [pages, setPages] = useState(null);
  const [editing, setEditing] = useState(null);   // { slug, title, data }
  const [msg, setMsg] = useState("");

  const load = () =>
    fetch(`${VULA_API}/v1/commerce/${tenantId}/admin/pages`)
      .then((r) => r.json()).then((d) => setPages(d.pages || [])).catch(() => setPages([]));
  useEffect(() => { load(); }, [tenantId]);

  const open = async (slug, title) => {
    const p = await fetch(`${VULA_API}/v1/commerce/${tenantId}/admin/pages/${slug}`).then((r) => r.json()).catch(() => ({}));
    setEditing({ slug, title: p.title || title || slug, data: norm(p.puck_data) });
  };

  const createNew = () => {
    const raw = window.prompt("Page name (e.g. About Us):");
    if (!raw) return;
    const title = raw.trim();
    const slug = (title.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "").slice(0, 50)) || "page";
    setEditing({ slug, title, data: norm({}) });
  };

  const save = async (data) => {
    setMsg("Saving…");
    await fetch(`${VULA_API}/v1/commerce/${tenantId}/admin/pages/${editing.slug}`, {
      method: "PUT", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ title: editing.title, status: "published", puck_data: data }),
    }).catch(() => {});
    setMsg("Published ✓"); setTimeout(() => setMsg(""), 1600);
    load();
  };

  const del = async (slug, title) => {
    if (!window.confirm(`Delete the page "${title || slug}"? This can't be undone.`)) return;
    await fetch(`${VULA_API}/v1/commerce/${tenantId}/admin/pages/${slug}`, { method: "DELETE" }).catch(() => {});
    load();
  };

  if (editing) {
    const publicUrl = `${window.location.origin}/#/page/${tenantId}/${editing.slug}`;
    return (
      <div>
        <div style={{ display: "flex", alignItems: "center", gap: 10, padding: "10px 0", flexWrap: "wrap" }}>
          <button onClick={() => { setEditing(null); load(); }} style={btn("#6B7280")}>← Pages</button>
          <strong style={{ color: C.text }}>{editing.title}</strong>
          <span style={{ color: C.muted, fontSize: 12 }}>/{editing.slug}</span>
          {msg && <span style={{ color: "#2C7A4B", fontSize: 12 }}>{msg}</span>}
          <a href={publicUrl} target="_blank" rel="noreferrer" style={{ marginLeft: "auto", fontSize: 13 }}>View live ↗</a>
        </div>
        <p style={{ fontSize: 12, color: C.muted, margin: "0 0 8px" }}>
          Drag blocks from the left, edit on the right, then hit <strong>Publish</strong>.
        </p>
        <div style={{ height: "78vh", border: `1px solid ${C.border}`, borderRadius: 10, overflow: "hidden" }}>
          <Puck config={config} data={editing.data} onPublish={save} />
        </div>
      </div>
    );
  }

  return (
    <div style={{ background: C.surface, border: `1px solid ${C.border}`, borderRadius: 10, padding: 16 }}>
      <div style={{ display: "flex", alignItems: "center" }}>
        <strong style={{ color: C.text }}>Pages</strong>
        <button onClick={createNew} style={{ ...btn("var(--accent)"), marginLeft: "auto" }}>+ New page</button>
      </div>
      <p style={{ fontSize: 12, color: C.muted, margin: "4px 0 12px" }}>
        Build landing, about or promo pages by drag-and-drop. Published pages are live at
        {" "}<code>/#/page/{tenantId}/&lt;slug&gt;</code>.
      </p>
      {pages === null ? <p style={{ color: C.muted }}>Loading…</p> :
        pages.length === 0 ? <p style={{ color: C.muted }}>No pages yet — create your first one.</p> :
          <div style={{ display: "grid", gap: 8 }}>
            {pages.map((p) => (
              <div key={p.slug} style={{ display: "flex", alignItems: "center", gap: 6 }}>
                <button onClick={() => open(p.slug, p.title)} style={{ ...rowStyle, flex: 1 }}>
                  <span style={{ fontWeight: 600, color: C.text }}>{p.title || p.slug}</span>
                  <span style={{ color: C.muted, fontSize: 12 }}>  /{p.slug} · {p.status}</span>
                </button>
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
