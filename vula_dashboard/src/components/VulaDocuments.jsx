import { EXT_ICON, extOf, isImageDoc, needsProject as docNeedsProject, useFiledLibrary } from "./documents/useDocuments";
import DocumentsV2 from "./documents/DocumentsV2";

const C = {
  bg: "var(--bg)", surface: "var(--surface)", border: "var(--border)",
  green: "var(--accent)", amber: "var(--warn)", red: "var(--danger)",
  text: "var(--text)", muted: "var(--muted)", surfaceAlt: "var(--surface-alt)",
};

const EXT_COLOR = {
  ".pdf": "var(--danger)", ".docx": "var(--info)", ".doc": "var(--info)",
  ".xlsx": "var(--ok)", ".xls": "var(--ok)", ".csv": "var(--ok)",
  ".dxf": "#8E44AD", ".dwg": "#8E44AD",
};

function FileBadge({ ext }) {
  const label = EXT_ICON[ext] || "FILE";
  const color = EXT_COLOR[ext] || C.muted;
  return (
    <span style={{
      display: "inline-block", padding: "3px 8px",
      background: `color-mix(in srgb, ${color} 9%, transparent)`, color,
      borderRadius: 4, fontSize: 10,
      fontWeight: 700, letterSpacing: "0.05em",
      fontFamily: "'Source Code Pro', monospace",
    }}>{label}</span>
  );
}

// Modern grid+lightbox viewing — same visual language as Puck's Gallery block (thumbnail grid,
// hover-zoom, fade/scale-in lightbox) so this feels like part of the same product, not a bolted-on
// file manager. Self-contained styles (not dependent on Puck's global VULA_PUCK_STYLES injection,
// which this shell never loads) via a <style> tag scoped to this component's class names.
const GRID_STYLES = `
.vdoc-tile { position: relative; border-radius: 10px; overflow: hidden; border: 1px solid ${C.border};
  background: ${C.surface}; cursor: pointer; transition: transform .2s ease, box-shadow .2s ease; }
.vdoc-tile:hover { transform: translateY(-3px); box-shadow: 0 8px 20px rgba(0,0,0,.10); }
.vdoc-thumb { width: 100%; height: 140px; object-fit: cover; display: block; transition: transform .3s ease; }
.vdoc-tile:hover .vdoc-thumb { transform: scale(1.05); }
.vdoc-icon-tile { width: 100%; height: 140px; display: flex; align-items: center; justify-content: center;
  background: ${C.surfaceAlt}; }
.vdoc-lightbox { position: fixed; inset: 0; background: rgba(0,0,0,.88); z-index: 9999; display: flex;
  align-items: center; justify-content: center; padding: 24px; cursor: zoom-out;
  animation: vdocFadeIn .18s ease both; }
.vdoc-lightbox img { max-width: 100%; max-height: 100%; border-radius: 8px;
  animation: vdocScaleIn .2s cubic-bezier(.16,1,.3,1) both; }
.vdoc-lightbox-close { position: absolute; top: 20px; right: 24px; color: #fff; font-size: 32px;
  line-height: 1; cursor: pointer; background: none; border: none; }
@keyframes vdocFadeIn { from { opacity: 0; } to { opacity: 1; } }
@keyframes vdocScaleIn { from { opacity: 0; transform: scale(.94); } to { opacity: 1; transform: scale(1); } }
`;

// The categories actually used, from the backend (2026-10-02) — this list used to be lowercase
// doc_types ("invoice", "delivery_note") that never matched a filed document's category.

function DocTile({ doc, projects, onAssign, onOpenImage }) {
  const ext = extOf(doc.filename);
  const isImage = isImageDoc(doc);
  const needsProject = docNeedsProject(doc);

  return (
    <div className="vdoc-tile" onClick={() => (isImage ? onOpenImage(doc) : doc.file_url && window.open(doc.file_url, "_blank"))}>
      {isImage && doc.file_url ? (
        <img className="vdoc-thumb" src={doc.file_url} alt={doc.filename} loading="lazy" />
      ) : (
        <div className="vdoc-icon-tile"><FileBadge ext={ext} /></div>
      )}
      <div style={{ padding: "8px 10px" }}>
        <div style={{ fontSize: 12, color: C.text, fontWeight: 600, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
          {doc.filename}
        </div>
        <div style={{ display: "flex", alignItems: "center", gap: 6, marginTop: 3, flexWrap: "wrap" }}>
          {(doc.fields?.labels || []).map((l) => (
            <span key={l} style={{ fontSize: 9.5, fontWeight: 700, color: "var(--bg)", background: "var(--warn)", padding: "2px 6px", borderRadius: 4, marginRight: 4 }}>{l}</span>
          ))}
          {doc.category && (
            <span style={{ fontSize: 9.5, fontWeight: 700, color: C.muted, background: C.surfaceAlt, padding: "2px 6px", borderRadius: 4 }}>{doc.category}</span>
          )}
          {doc.customer_phone && (
            <span style={{ fontSize: 9.5, color: C.green }} title="Linked to a customer">👤 {doc.customer_phone}</span>
          )}
          {doc.clickup_task_id && <span style={{ fontSize: 9.5, color: "#7B68EE" }} title="Attached in ClickUp">🗂️</span>}
        </div>
        {doc.summary && <p style={{ margin: "4px 0 0", fontSize: 11, color: C.muted, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{doc.summary}</p>}
        {needsProject && (
          <select defaultValue="" aria-label="File under a project" onClick={(e) => e.stopPropagation()} onChange={(e) => onAssign(doc.id, e.target.value)}
            style={{ marginTop: 6, width: "100%", fontSize: 11, padding: "5px 6px", border: `1px solid ${C.border}`, borderRadius: 6, color: C.text, background: C.surface }}>
            <option value="" disabled>File under…</option>
            {projects.map((p) => <option key={p.label} value={p.label}>{p.label}</option>)}
          </select>
        )}
      </div>
    </div>
  );
}

export function FiledLibrary({ tenantId, customerPhone, defaultFiledBy, project, title = "📂 Documents & media" }) {
  const { docs, projects, customers, categories, loading, total, lightboxDoc, setLightboxDoc, filters, setFilter, loadMore, assign } =
    useFiledLibrary({ tenantId, customerPhone, defaultFiledBy, project });

  return (
    <div style={{ background: C.surface, border: `1px solid ${C.border}`, borderRadius: 12, overflow: "hidden", marginBottom: 28 }}>
      <style>{GRID_STYLES}</style>
      <div style={{ padding: "14px 20px", borderBottom: `1px solid ${C.border}`, display: "flex", justifyContent: "space-between", alignItems: "center", flexWrap: "wrap", gap: 8 }}>
        <span style={{ fontSize: 13, fontWeight: 600, color: C.text }}>{title}</span>
        <span style={{ fontSize: 12, color: C.muted }}>
          {loading && !docs.length ? "Loading…" : `${total} ${total === 1 ? "document" : "documents"}`}
        </span>
      </div>

      {!customerPhone && (
        <div style={{ padding: "10px 20px", borderBottom: `1px solid ${C.border}`, display: "flex", gap: 8, flexWrap: "wrap", background: C.surfaceAlt }}>
          <input aria-label="Search documents" value={filters.search} onChange={(e) => setFilter("search", e.target.value)} placeholder="🔍 Search name, supplier, number, project…"
            style={{ flex: 1, minWidth: 160, fontSize: 12, padding: "6px 10px", border: `1px solid ${C.border}`, borderRadius: 6, background: C.surface }} />
          <select aria-label="Category" value={filters.category} onChange={(e) => setFilter("category", e.target.value)}
            style={{ fontSize: 12, padding: "6px 10px", border: `1px solid ${C.border}`, borderRadius: 6, background: C.surface, color: C.text }}>
            <option value="">All categories</option>
            {categories.map(([c, n]) => <option key={c} value={c}>{c} ({n})</option>)}
          </select>
          {!project && (
            <select aria-label="Project" value={filters.project} onChange={(e) => setFilter("project", e.target.value)}
              style={{ fontSize: 12, padding: "6px 10px", border: `1px solid ${C.border}`, borderRadius: 6, background: C.surface, color: C.text, maxWidth: 200 }}>
              <option value="">All projects</option>
              {projects.map((p) => <option key={p.label} value={p.label}>{p.label}</option>)}
            </select>
          )}
          <select aria-label="Customer" value={filters.customer_phone} onChange={(e) => setFilter("customer_phone", e.target.value)}
            style={{ fontSize: 12, padding: "6px 10px", border: `1px solid ${C.border}`, borderRadius: 6, background: C.surface, color: C.text, maxWidth: 180 }}>
            <option value="">All customers</option>
            {customers.map((c) => <option key={c.phone} value={c.phone}>{c.name || c.phone}</option>)}
          </select>
          <input type="date" value={filters.since} onChange={(e) => setFilter("since", e.target.value)}
            style={{ fontSize: 12, padding: "6px 10px", border: `1px solid ${C.border}`, borderRadius: 6, background: C.surface, color: C.text }} />
          <input type="date" value={filters.until} onChange={(e) => setFilter("until", e.target.value)}
            style={{ fontSize: 12, padding: "6px 10px", border: `1px solid ${C.border}`, borderRadius: 6, background: C.surface, color: C.text }} />
        </div>
      )}

      {!loading && docs.length === 0 && (
        <div style={{ padding: "28px 20px", textAlign: "center", color: C.muted, fontSize: 13 }}>
          No documents match{customerPhone ? " this customer" : ""} yet.
        </div>
      )}

      {docs.length > 0 && (
        <div style={{ padding: 16, display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(150px, 1fr))", gap: 12 }}>
          {docs.map((doc) => (
            <DocTile key={doc.id} doc={doc} projects={projects} onAssign={assign} onOpenImage={setLightboxDoc} />
          ))}
        </div>
      )}

      {docs.length < total && (
        <div style={{ textAlign: "center", padding: "0 20px 16px" }}>
          <button onClick={loadMore} disabled={loading}
            style={{ padding: "8px 18px", background: C.surfaceAlt, border: `1px solid ${C.border}`, borderRadius: 8, fontSize: 12, color: C.text, cursor: "pointer" }}>
            {loading ? "Loading…" : `Load more (${total - docs.length} left)`}
          </button>
        </div>
      )}

      {lightboxDoc && (
        <div className="vdoc-lightbox" onClick={() => setLightboxDoc(null)}>
          <button className="vdoc-lightbox-close" onClick={(e) => { e.stopPropagation(); setLightboxDoc(null); }} aria-label="Close">×</button>
          <img src={lightboxDoc.file_url} alt={lightboxDoc.filename} onClick={(e) => e.stopPropagation()} />
        </div>
      )}
    </div>
  );
}

export default function VulaDocuments({ ...props }) {
  return <DocumentsV2 {...props} />;
}

