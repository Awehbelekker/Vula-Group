import { useState, useRef, useCallback, useEffect } from "react";

const VULA_API = import.meta.env.VITE_API_URL || "https://vula-group-production.up.railway.app";

const C = {
  bg: "#F7F4EE", surface: "#FFFFFF", border: "#DDD8CE",
  green: "var(--accent)", amber: "#C4861A", red: "#C0392B",
  text: "#2A2A2A", muted: "#8A8680", surfaceAlt: "#F0EDE5",
};

const EXT_ICON = {
  ".pdf": "PDF", ".docx": "DOC", ".doc": "DOC",
  ".xlsx": "XLS", ".xls": "XLS", ".csv": "CSV",
  ".txt": "TXT", ".png": "IMG", ".jpg": "IMG",
  ".jpeg": "IMG", ".tiff": "IMG", ".dxf": "DXF", ".dwg": "DWG",
};

const EXT_COLOR = {
  ".pdf": "#C0392B", ".docx": "#2B5797", ".doc": "#2B5797",
  ".xlsx": "#1E7145", ".xls": "#1E7145", ".csv": "#1E7145",
  ".dxf": "#8E44AD", ".dwg": "#8E44AD",
};

function FileBadge({ ext }) {
  const label = EXT_ICON[ext] || "FILE";
  const color = EXT_COLOR[ext] || C.muted;
  return (
    <span style={{
      display: "inline-block", padding: "3px 8px",
      background: `${color}18`, color,
      borderRadius: 4, fontSize: 10,
      fontWeight: 700, letterSpacing: "0.05em",
      fontFamily: "'Source Code Pro', monospace",
    }}>{label}</span>
  );
}

function formatSize(kb) {
  if (kb < 1000) return `${kb} KB`;
  return `${(kb / 1024).toFixed(1)} MB`;
}

function DropZone({ onFiles, disabled }) {
  const [dragging, setDragging] = useState(false);
  const inputRef = useRef();

  const handle = (files) => {
    if (!files?.length || disabled) return;
    onFiles(Array.from(files));
  };

  return (
    <div
      onDragOver={(e) => { e.preventDefault(); setDragging(true); }}
      onDragLeave={() => setDragging(false)}
      onDrop={(e) => { e.preventDefault(); setDragging(false); handle(e.dataTransfer.files); }}
      onClick={() => inputRef.current?.click()}
      style={{
        border: `2px dashed ${dragging ? C.green : C.border}`,
        borderRadius: 10,
        padding: "28px 20px",
        textAlign: "center",
        cursor: disabled ? "not-allowed" : "pointer",
        background: dragging ? `${C.green}08` : C.surfaceAlt,
        transition: "all 0.15s",
        opacity: disabled ? 0.5 : 1,
      }}
    >
      <input
        ref={inputRef}
        type="file"
        multiple
        accept=".pdf,.docx,.doc,.xlsx,.xls,.csv,.txt,.png,.jpg,.jpeg,.tiff,.dxf,.dwg"
        style={{ display: "none" }}
        onChange={(e) => handle(e.target.files)}
      />
      <p style={{ margin: 0, fontSize: 14, color: C.muted }}>
        Drag files here or click to browse
      </p>
      <p style={{ margin: "6px 0 0", fontSize: 12, color: C.muted }}>
        PDF, DOCX, XLSX, CSV, DXF, DWG, images — max 50 MB each
      </p>
    </div>
  );
}

function UploadQueue({ items, onClear }) {
  if (!items.length) return null;
  return (
    <div style={{ marginTop: 16 }}>
      {items.map((item, i) => (
        <div key={i} style={{
          display: "flex", alignItems: "center", gap: 12,
          padding: "10px 14px",
          background: C.surface,
          border: `1px solid ${C.border}`,
          borderRadius: 8,
          marginBottom: 6,
          fontSize: 13,
        }}>
          <FileBadge ext={item.ext} />
          <span style={{ flex: 1, color: C.text, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
            {item.name}
          </span>
          <span style={{ color: C.muted, fontSize: 12 }}>{item.size}</span>
          <StatusChip status={item.status} />
        </div>
      ))}
      {items.every((i) => i.status !== "uploading") && (
        <button onClick={onClear} style={{
          marginTop: 8, padding: "6px 14px",
          background: "none", border: `1px solid ${C.border}`,
          borderRadius: 6, fontSize: 12, color: C.muted,
          cursor: "pointer",
        }}>Clear queue</button>
      )}
    </div>
  );
}

function StatusChip({ status }) {
  const map = {
    pending:  { label: "Pending",    color: C.muted },
    uploading:{ label: "Uploading…", color: C.amber },
    queued:   { label: "Queued",     color: C.green },
    error:    { label: "Error",      color: C.red },
  };
  const { label, color } = map[status] || map.pending;
  return (
    <span style={{ fontSize: 11, color, fontWeight: 600 }}>{label}</span>
  );
}

function FieldsPreview({ fields }) {
  const entries = Object.entries(fields || {})
    .filter(([k, v]) => v != null && v !== "" && k !== "items" && typeof v !== "object")
    .slice(0, 5);
  if (!entries.length) return null;
  return (
    <div style={{ display: "flex", flexWrap: "wrap", gap: 8, marginTop: 6 }}>
      {entries.map(([k, v]) => (
        <span key={k} style={{ fontSize: 11, color: C.muted, background: C.surfaceAlt, padding: "2px 8px", borderRadius: 4 }}>
          {k.replace(/_/g, " ")}: <strong style={{ color: C.text }}>{String(v)}</strong>
        </span>
      ))}
    </div>
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

const IMG_EXT = new Set([".png", ".jpg", ".jpeg", ".gif", ".webp"]);
const CATEGORIES = ["invoice", "receipt", "quote", "delivery_note", "media", "meeting_notes", "other"];

function DocTile({ doc, projects, onAssign, onOpenImage }) {
  const ext = "." + (doc.filename || "").split(".").pop().toLowerCase();
  const isImage = IMG_EXT.has(ext) || (doc.mime || "").startsWith("image/");
  const needsProject = doc.status === "pending_project" || (!doc.project && doc.category !== "media");

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
          <select defaultValue="" onClick={(e) => e.stopPropagation()} onChange={(e) => onAssign(doc.id, e.target.value)}
            style={{ marginTop: 6, width: "100%", fontSize: 11, padding: "5px 6px", border: `1px solid ${C.border}`, borderRadius: 6, color: C.text, background: C.surface }}>
            <option value="" disabled>File under…</option>
            {projects.map((p) => <option key={p.label} value={p.label}>{p.label}</option>)}
          </select>
        )}
      </div>
    </div>
  );
}

export function FiledLibrary({ tenantId, customerPhone, defaultFiledBy, title = "📂 Documents & media" }) {
  const [docs, setDocs] = useState([]);
  const [projects, setProjects] = useState([]);
  const [customers, setCustomers] = useState([]);
  const [loading, setLoading] = useState(false);
  const [total, setTotal] = useState(0);
  const [lightboxDoc, setLightboxDoc] = useState(null);
  const [filters, setFilters] = useState({ search: "", category: "", customer_phone: customerPhone || "", since: "", until: "", filed_by: defaultFiledBy || "" });
  const [offset, setOffset] = useState(0);
  const PAGE = 24;

  const load = useCallback(async (append = false) => {
    if (!tenantId?.trim()) return;
    setLoading(true);
    try {
      const params = new URLSearchParams({ limit: String(PAGE), offset: String(append ? offset : 0) });
      Object.entries(filters).forEach(([k, v]) => { if (v) params.set(k, v); });
      const [dr, pr] = await Promise.all([
        fetch(`${VULA_API}/v1/documents/${tenantId.trim()}/filed?${params}`).then((r) => r.json()),
        projects.length ? Promise.resolve({ projects }) : fetch(`${VULA_API}/v1/documents/${tenantId.trim()}/projects`).then((r) => r.json()),
      ]);
      setDocs((prev) => append ? [...prev, ...(dr.documents || [])] : (dr.documents || []));
      setTotal(dr.total ?? (dr.documents || []).length);
      if (!projects.length) setProjects(pr.projects || []);
      if (!append) setOffset(PAGE);
    } catch { /* ignore */ }
    finally { setLoading(false); }
  }, [tenantId, filters]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => { load(false); }, [tenantId, filters]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    if (customerPhone || !tenantId?.trim()) return; // customer-scoped view (Phase 4) skips its own picker
    fetch(`${VULA_API}/v1/commerce/${tenantId.trim()}/admin/customers`)
      .then((r) => r.json()).then((d) => setCustomers(d.customers || [])).catch(() => {});
  }, [tenantId, customerPhone]);

  const setFilter = (k, v) => setFilters((f) => ({ ...f, [k]: v }));

  const assign = async (docId, label) => {
    const proj = projects.find((p) => p.label === label);
    if (!proj) return;
    await fetch(`${VULA_API}/v1/documents/${docId}/assign-project`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ project: proj.label, clickup_list_id: proj.clickup_list_id }),
    });
    load(false);
  };

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
          <input value={filters.search} onChange={(e) => setFilter("search", e.target.value)} placeholder="🔍 Search filename or summary…"
            style={{ flex: 1, minWidth: 160, fontSize: 12, padding: "6px 10px", border: `1px solid ${C.border}`, borderRadius: 6, background: C.surface }} />
          <select value={filters.category} onChange={(e) => setFilter("category", e.target.value)}
            style={{ fontSize: 12, padding: "6px 10px", border: `1px solid ${C.border}`, borderRadius: 6, background: C.surface, color: C.text }}>
            <option value="">All categories</option>
            {CATEGORIES.map((c) => <option key={c} value={c}>{c}</option>)}
          </select>
          <select value={filters.customer_phone} onChange={(e) => setFilter("customer_phone", e.target.value)}
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
          <button onClick={() => { setOffset(docs.length); load(true); }} disabled={loading}
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

// "Just store this" — a photo/form/file with nothing to extract, no OCR/KB-ingest attempted.
function MediaUpload({ tenantId, onUploaded }) {
  const [customers, setCustomers] = useState([]);
  const [customerPhone, setCustomerPhone] = useState("");
  const [uploading, setUploading] = useState(false);
  const [msg, setMsg] = useState("");
  const inputRef = useRef();

  useEffect(() => {
    if (!tenantId?.trim()) return;
    fetch(`${VULA_API}/v1/commerce/${tenantId.trim()}/admin/customers`)
      .then((r) => r.json()).then((d) => setCustomers(d.customers || [])).catch(() => {});
  }, [tenantId]);

  const handle = async (files) => {
    if (!files?.length || !tenantId?.trim()) return;
    setUploading(true);
    setMsg("");
    let ok = 0;
    for (const file of Array.from(files)) {
      const fd = new FormData();
      fd.append("file", file);
      if (customerPhone) fd.append("customer_phone", customerPhone);
      try {
        const r = await fetch(`${VULA_API}/v1/documents/${tenantId.trim()}/media`, { method: "POST", body: fd });
        if (r.ok) ok += 1;
      } catch { /* ignore */ }
    }
    setUploading(false);
    setMsg(`${ok}/${files.length} stored ✓`);
    onUploaded?.();
    setTimeout(() => setMsg(""), 3000);
  };

  return (
    <div style={{ background: C.surface, border: `1px solid ${C.border}`, borderRadius: 12, padding: 20, marginBottom: 28 }}>
      <h3 style={{ margin: "0 0 6px", fontSize: 14, fontWeight: 700, color: C.text }}>📷 Store media</h3>
      <p style={{ margin: "0 0 14px", fontSize: 12, color: C.muted }}>
        For anything with nothing to extract — a photo, a signed form, anything you just want to keep.
        No AI processing, stored straight into the library above.
      </p>
      <div style={{ display: "flex", gap: 10, alignItems: "center", flexWrap: "wrap" }}>
        <select value={customerPhone} onChange={(e) => setCustomerPhone(e.target.value)}
          style={{ fontSize: 12, padding: "8px 10px", border: `1px solid ${C.border}`, borderRadius: 6, background: C.surface, color: C.text, maxWidth: 200 }}>
          <option value="">No customer (general)</option>
          {customers.map((c) => <option key={c.phone} value={c.phone}>{c.name || c.phone}</option>)}
        </select>
        <input ref={inputRef} type="file" multiple style={{ display: "none" }} onChange={(e) => handle(e.target.files)} />
        <button onClick={() => inputRef.current?.click()} disabled={uploading}
          style={{ padding: "8px 16px", background: uploading ? C.muted : C.green, color: "#fff", border: "none", borderRadius: 8, fontSize: 13, fontWeight: 600, cursor: uploading ? "not-allowed" : "pointer" }}>
          {uploading ? "Storing…" : "+ Add photos / files"}
        </button>
        {msg && <span style={{ fontSize: 12, color: C.green, fontWeight: 600 }}>{msg}</span>}
      </div>
    </div>
  );
}

// Google Drive import — search-based, not a folder browser. The drive.file OAuth scope (a
// deliberate least-privilege choice on the backend) means Vula only sees files it created itself
// or ones the tenant explicitly opened through it before, so a generic "browse my whole Drive"
// tree would show nothing useful for most tenants under this scope. Imported files land in the
// same library above (category='media'), viewed with the exact same grid — no separate UI.
function DriveImport({ tenantId, onImported }) {
  const [status, setStatus] = useState(null);
  const [q, setQ] = useState("");
  const [results, setResults] = useState(null);
  const [searching, setSearching] = useState(false);
  const [importingId, setImportingId] = useState(null);

  useEffect(() => {
    if (!tenantId?.trim()) return;
    fetch(`${VULA_API}/v1/google/status/${tenantId.trim()}`).then((r) => r.json())
      .then((d) => setStatus(d.status)).catch(() => setStatus("error"));
  }, [tenantId]);

  const search = async () => {
    setSearching(true);
    try {
      const r = await fetch(`${VULA_API}/v1/google/${tenantId.trim()}/drive/search?q=${encodeURIComponent(q)}`);
      const d = await r.json();
      setResults(d.files || []);
    } catch { setResults([]); }
    setSearching(false);
  };

  const doImport = async (file) => {
    setImportingId(file.id);
    try {
      await fetch(`${VULA_API}/v1/google/${tenantId.trim()}/drive/import`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ file_id: file.id }),
      });
      setResults((rs) => rs.filter((f) => f.id !== file.id));
      onImported?.();
    } catch { /* ignore */ }
    setImportingId(null);
  };

  if (status === null) return null; // still checking — don't flash a "connect" prompt unnecessarily
  if (status !== "connected") {
    return (
      <div style={{ background: C.surfaceAlt, border: `1px solid ${C.border}`, borderRadius: 12, padding: 16, marginBottom: 28, fontSize: 12.5, color: C.muted }}>
        🔵 Connect Google in Settings to import files from Drive into this library.
      </div>
    );
  }

  return (
    <div style={{ background: C.surface, border: `1px solid ${C.border}`, borderRadius: 12, padding: 20, marginBottom: 28 }}>
      <h3 style={{ margin: "0 0 6px", fontSize: 14, fontWeight: 700, color: C.text }}>🔵 Import from Google Drive</h3>
      <p style={{ margin: "0 0 14px", fontSize: 12, color: C.muted }}>
        Search files Vula can see in your Drive and pull one into this library.
      </p>
      <div style={{ display: "flex", gap: 8, marginBottom: 12 }}>
        <input value={q} onChange={(e) => setQ(e.target.value)} onKeyDown={(e) => e.key === "Enter" && search()}
          placeholder="Search Drive by filename…"
          style={{ flex: 1, fontSize: 13, padding: "8px 10px", border: `1px solid ${C.border}`, borderRadius: 6 }} />
        <button onClick={search} disabled={searching}
          style={{ padding: "8px 16px", background: searching ? C.muted : C.green, color: "#fff", border: "none", borderRadius: 8, fontSize: 13, fontWeight: 600, cursor: searching ? "not-allowed" : "pointer" }}>
          {searching ? "Searching…" : "Search"}
        </button>
      </div>
      {results !== null && results.length === 0 && (
        <p style={{ fontSize: 12, color: C.muted, margin: 0 }}>No matching files Vula can see. Files you've opened via Vula before, or created with Vula, are searchable here.</p>
      )}
      {results && results.length > 0 && (
        <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
          {results.map((f) => (
            <div key={f.id} style={{ display: "flex", alignItems: "center", gap: 10, padding: "8px 10px", border: `1px solid ${C.border}`, borderRadius: 8 }}>
              <span style={{ flex: 1, fontSize: 12.5, color: C.text, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{f.name}</span>
              <button onClick={() => doImport(f)} disabled={importingId === f.id}
                style={{ padding: "5px 12px", background: C.surfaceAlt, border: `1px solid ${C.border}`, borderRadius: 6, fontSize: 11.5, color: C.text, cursor: "pointer" }}>
                {importingId === f.id ? "Importing…" : "Import"}
              </button>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

export default function VulaDocuments({ tenantId: propTenantId, defaultFiledBy }) {
  const [tenantId, setTenantId] = useState(propTenantId || "default");
  useEffect(() => { if (propTenantId) setTenantId(propTenantId); }, [propTenantId]);
  const [docs, setDocs] = useState([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [queue, setQueue] = useState([]);
  const [uploading, setUploading] = useState(false);
  const [apiKey, setApiKey] = useState(import.meta.env.VITE_API_KEY ?? "");
  const [libraryKey, setLibraryKey] = useState(0);

  const load = useCallback(async () => {
    if (!tenantId.trim()) return;
    setLoading(true);
    setError("");
    try {
      const headers = apiKey ? { "X-API-Key": apiKey } : {};
      const resp = await fetch(`${VULA_API}/documents/${tenantId.trim()}`, { headers });
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
      const data = await resp.json();
      setDocs(data.documents || []);
    } catch (e) {
      setError(e.message);
    } finally {
      setLoading(false);
    }
  }, [tenantId, apiKey]);

  useEffect(() => { load(); }, [load]);

  const onFiles = (files) => {
    const items = files.map((f) => ({
      file: f,
      name: f.name,
      ext: "." + f.name.split(".").pop().toLowerCase(),
      size: formatSize(Math.round(f.size / 1024)),
      status: "pending",
    }));
    setQueue((q) => [...q, ...items]);
  };

  const uploadAll = async () => {
    if (!tenantId.trim() || uploading) return;
    setUploading(true);
    const pending = queue.filter((q) => q.status === "pending");
    if (!pending.length) { setUploading(false); return; }

    // Mark all as uploading immediately
    setQueue((q) => q.map((x) => x.status === "pending" ? { ...x, status: "uploading" } : x));

    try {
      // Batch upload — all files in one request, KB ingest runs in parallel on server
      const fd = new FormData();
      fd.append("tenant_id", tenantId.trim());
      pending.forEach((item) => fd.append("files", item.file));
      const headers = apiKey ? { "X-API-Key": apiKey } : {};
      const resp = await fetch(`${VULA_API}/ingest/batch`, { method: "POST", headers, body: fd });

      if (resp.ok) {
        const data = await resp.json();
        // Map per-file statuses from batch response
        const fileStatuses = {};
        (data.files || []).forEach((f) => { fileStatuses[f.filename] = f.status; });
        setQueue((q) => q.map((x) => ({
          ...x,
          status: fileStatuses[x.name] === "queued" ? "queued"
                : fileStatuses[x.name] === "error"  ? "error"
                : fileStatuses[x.name] === "skipped" ? "error"
                : x.status,
        })));
      } else {
        // Fallback: upload individually
        for (const item of pending) {
          try {
            const fd2 = new FormData();
            fd2.append("tenant_id", tenantId.trim());
            fd2.append("file", item.file);
            const r = await fetch(`${VULA_API}/ingest`, { method: "POST", headers, body: fd2 });
            setQueue((q) => q.map((x) => x.name === item.name
              ? { ...x, status: r.ok ? "queued" : "error" } : x));
          } catch {
            setQueue((q) => q.map((x) => x.name === item.name ? { ...x, status: "error" } : x));
          }
        }
      }
    } catch {
      setQueue((q) => q.map((x) => x.status === "uploading" ? { ...x, status: "error" } : x));
    }

    setUploading(false);
    setTimeout(load, 2000);
  };

  const pendingCount = queue.filter((q) => q.status === "pending").length;

  return (
    <div style={{ maxWidth: 900, margin: "0 auto", padding: "32px 24px" }}>
      <h1 style={{ fontFamily: "'Cormorant Garamond', serif", fontSize: 28, fontWeight: 700, color: C.text, margin: "0 0 4px" }}>
        Document Library
      </h1>
      <p style={{ fontSize: 13, color: C.muted, margin: "0 0 32px" }}>
        Manage ingested documents per tenant — upload new files and monitor processing status.
      </p>

      {/* Controls row */}
      <div style={{ display: "flex", gap: 12, marginBottom: 24, flexWrap: "wrap" }}>
        <div style={{ flex: 1, minWidth: 180 }}>
          <label style={{ display: "block", fontSize: 11, fontWeight: 700, color: C.muted, textTransform: "uppercase", letterSpacing: "0.4px", marginBottom: 6 }}>
            Tenant ID
          </label>
          <input
            value={tenantId}
            onChange={(e) => setTenantId(e.target.value)}
            onBlur={load}
            placeholder="default"
            style={{ width: "100%", padding: "10px 12px", border: `1px solid ${C.border}`, borderRadius: 8, fontSize: 13, color: C.text, background: C.surface, boxSizing: "border-box" }}
          />
        </div>
        <div style={{ flex: 1, minWidth: 180 }}>
          <label style={{ display: "block", fontSize: 11, fontWeight: 700, color: C.muted, textTransform: "uppercase", letterSpacing: "0.4px", marginBottom: 6 }}>
            API Key
          </label>
          <input
            value={apiKey}
            onChange={(e) => setApiKey(e.target.value)}
            type="password"
            placeholder="Leave blank for dev mode"
            style={{ width: "100%", padding: "10px 12px", border: `1px solid ${C.border}`, borderRadius: 8, fontSize: 13, color: C.text, background: C.surface, boxSizing: "border-box" }}
          />
        </div>
        <div style={{ display: "flex", alignItems: "flex-end" }}>
          <button onClick={load} disabled={loading} style={{
            padding: "10px 18px", background: C.surface,
            border: `1px solid ${C.border}`, borderRadius: 8,
            fontSize: 13, cursor: "pointer", color: C.text,
          }}>
            {loading ? "Loading…" : "Refresh"}
          </button>
        </div>
      </div>

      {/* "Just store this" — no OCR/KB-extraction, straight into the library below */}
      <MediaUpload tenantId={tenantId} onUploaded={() => setLibraryKey((k) => k + 1)} />

      {/* Google Drive import — search-based (see component comment for why not a folder browser) */}
      <DriveImport tenantId={tenantId} onImported={() => setLibraryKey((k) => k + 1)} />

      {/* Filed documents — durable copies, modern grid+lightbox, filterable (project/customer/
          category/date/search). key bump forces a reload after a media upload lands. */}
      <FiledLibrary key={libraryKey} tenantId={tenantId} defaultFiledBy={defaultFiledBy} />

      {/* Raw KB-ingest status list */}
      <div style={{
        background: C.surface,
        border: `1px solid ${C.border}`,
        borderRadius: 12,
        overflow: "hidden",
        marginBottom: 28,
      }}>
        <div style={{
          padding: "14px 20px",
          borderBottom: `1px solid ${C.border}`,
          display: "flex", justifyContent: "space-between", alignItems: "center",
        }}>
          <span style={{ fontSize: 13, fontWeight: 600, color: C.text }}>
            Knowledge base files
          </span>
          <span style={{ fontSize: 12, color: C.muted }}>
            {docs.length} {docs.length === 1 ? "file" : "files"}
          </span>
        </div>

        {error && (
          <div style={{ padding: "16px 20px", fontSize: 13, color: C.red }}>
            Error: {error}
          </div>
        )}

        {!error && !loading && docs.length === 0 && (
          <div style={{ padding: "32px 20px", textAlign: "center", color: C.muted, fontSize: 13 }}>
            No documents ingested yet for tenant <strong>{tenantId}</strong>.
            Upload your first document below.
          </div>
        )}

        {docs.map((doc, i) => (
          <div key={doc.filename} style={{
            padding: "14px 20px",
            borderBottom: i < docs.length - 1 ? `1px solid ${C.border}` : "none",
            display: "flex", alignItems: "center", gap: 14,
          }}>
            <FileBadge ext={doc.type} />
            <span style={{ flex: 1, fontSize: 13, color: C.text }}>{doc.filename}</span>
            <span style={{ fontSize: 12, color: C.muted, fontFamily: "'Source Code Pro', monospace" }}>
              {formatSize(doc.size_kb)}
            </span>
          </div>
        ))}
      </div>

      {/* Upload section */}
      <div style={{
        background: C.surface,
        border: `1px solid ${C.border}`,
        borderRadius: 12,
        padding: 24,
      }}>
        <h3 style={{ margin: "0 0 16px", fontSize: 14, fontWeight: 700, color: C.text }}>
          Upload Documents
        </h3>
        <p style={{ margin: "0 0 16px", fontSize: 13, color: C.muted }}>
          Files are queued for AI ingestion. Processing takes 2–5 minutes per document.
          The AI extracts text, embeds it, and stores it in the tenant knowledge base.
        </p>

        <DropZone onFiles={onFiles} disabled={uploading} />

        <UploadQueue items={queue} onClear={() => setQueue([])} />

        {pendingCount > 0 && (
          <button
            onClick={uploadAll}
            disabled={uploading}
            style={{
              marginTop: 16,
              padding: "12px 24px",
              background: uploading ? C.muted : C.green,
              color: "#fff", border: "none",
              borderRadius: 8, fontSize: 14,
              fontWeight: 600, cursor: uploading ? "not-allowed" : "pointer",
              transition: "background 0.15s",
            }}
          >
            {uploading ? "Uploading…" : `Upload ${pendingCount} file${pendingCount > 1 ? "s" : ""}`}
          </button>
        )}
      </div>
    </div>
  );
}
