import { useState, useRef, useCallback, useEffect } from "react";

const VULA_API = import.meta.env.VITE_API_URL ?? "/api";

const C = {
  bg: "#F7F4EE", surface: "#FFFFFF", border: "#DDD8CE",
  green: "#2C5545", amber: "#C4861A", red: "#C0392B",
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

export default function VulaDocuments() {
  const [tenantId, setTenantId] = useState("default");
  const [docs, setDocs] = useState([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [queue, setQueue] = useState([]);
  const [uploading, setUploading] = useState(false);
  const [apiKey, setApiKey] = useState(import.meta.env.VITE_API_KEY ?? "");

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

    for (let i = 0; i < pending.length; i++) {
      const item = pending[i];
      setQueue((q) => q.map((x) => x.name === item.name ? { ...x, status: "uploading" } : x));

      try {
        const fd = new FormData();
        fd.append("tenant_id", tenantId.trim());
        fd.append("file", item.file);
        const headers = apiKey ? { "X-API-Key": apiKey } : {};
        const resp = await fetch(`${VULA_API}/ingest`, { method: "POST", headers, body: fd });
        if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
        setQueue((q) => q.map((x) => x.name === item.name ? { ...x, status: "queued" } : x));
      } catch {
        setQueue((q) => q.map((x) => x.name === item.name ? { ...x, status: "error" } : x));
      }
    }

    setUploading(false);
    setTimeout(load, 1500);
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

      {/* Document list */}
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
            Ingested documents
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
