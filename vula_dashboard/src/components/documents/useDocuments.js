/**
 * useDocuments.js — the Documents screen's data and actions (2026-10-19, UX redesign Phase F).
 * Shared by VulaDocuments.jsx (and its FiledLibrary) and documents/DocumentsV2.jsx; moved out
 * unchanged — same endpoints, same bodies, same messages. One hook per panel, as the panels each
 * load on their own and hide themselves when there's nothing to show.
 */
import { useCallback, useEffect, useState } from "react";
import { VULA_API } from "../../lib/authFetch";
import { takeHandOff } from "../../lib/sharedFiles";

export const EXT_ICON = {
  ".pdf": "PDF", ".docx": "DOC", ".doc": "DOC",
  ".xlsx": "XLS", ".xls": "XLS", ".csv": "CSV",
  ".txt": "TXT", ".png": "IMG", ".jpg": "IMG",
  ".jpeg": "IMG", ".tiff": "IMG", ".dxf": "DXF", ".dwg": "DWG",
};
export const IMG_EXT = new Set([".png", ".jpg", ".jpeg", ".gif", ".webp"]);
export const ACCEPT = ".pdf,.docx,.doc,.xlsx,.xls,.csv,.txt,.png,.jpg,.jpeg,.tiff,.dxf,.dwg";

export function formatSize(kb) {
  if (kb < 1000) return `${kb} KB`;
  return `${(kb / 1024).toFixed(1)} MB`;
}
export const extOf = (filename) => "." + (filename || "").split(".").pop().toLowerCase();
export const isImageDoc = (doc) => IMG_EXT.has(extOf(doc.filename)) || (doc.mime || "").startsWith("image/");
export const needsProject = (doc) => doc.status === "pending_project" || (!doc.project && doc.category !== "media");

// 2026-10-17 (KB retrieval 1b): who can see each knowledge-base document. The customer assistant,
// the public web page and anyone messaging who isn't on the team only get documents marked
// "Customers can see" — everything else stays with the team. Untagged = stored before this
// existed; until the backfill runs (and strict mode is on) those are still reachable.
export const AUDIENCE_BADGE = {
  public:   { label: "Customers can see", color: "var(--ok)" },
  staff:    { label: "Team only",          color: "var(--muted)" },
  owner:    { label: "Owner only",         color: "var(--info)" },
  mixed:    { label: "Mixed",              color: "var(--warn)" },
  untagged: { label: "Not set yet",        color: "var(--warn)" },
};
export const SORT_REASON = { named: "named in the document", paid: "paid by a payment on that project", usual_supplier: "the supplier's usual project", month: "that month's main project" };
export const num = (v) => Number(v || 0).toLocaleString("en-ZA");

/** The tenant's customers, for the "which customer" pickers (skipped when `skip`). */
export function useCustomerOptions(tenantId, skip = false) {
  const [customers, setCustomers] = useState([]);
  useEffect(() => {
    if (skip || !tenantId?.trim()) return;
    fetch(`${VULA_API}/v1/commerce/${tenantId.trim()}/admin/customers`)
      .then((r) => r.json()).then((d) => setCustomers(d.customers || [])).catch(() => {});
  }, [tenantId, skip]);
  return customers;
}

/** Filed documents — durable copies, filterable (project/customer/category/date/search), paged. */
export function useFiledLibrary({ tenantId, customerPhone, defaultFiledBy, project }) {
  const [docs, setDocs] = useState([]);
  const [projects, setProjects] = useState([]);
  const [categories, setCategories] = useState([]);
  const [loading, setLoading] = useState(false);
  const [total, setTotal] = useState(0);
  const [lightboxDoc, setLightboxDoc] = useState(null);
  const [filters, setFilters] = useState({ search: "", category: "", customer_phone: customerPhone || "", since: "", until: "", filed_by: defaultFiledBy || "", project: project || "" });
  // A project's page shows only that project's documents (2026-10-02).
  useEffect(() => { setFilters((f) => ({ ...f, project: project || "" })); }, [project]);
  const customers = useCustomerOptions(tenantId, !!customerPhone);   // customer-scoped view (Phase 4) skips its own picker
  const PAGE = 24;

  // `from` is where the page starts: 0 for a fresh list, the number already shown for "Load more".
  const load = useCallback(async (append = false, from = 0) => {
    if (!tenantId?.trim()) return;
    setLoading(true);
    try {
      const params = new URLSearchParams({ limit: String(PAGE), offset: String(append ? from : 0) });
      Object.entries(filters).forEach(([k, v]) => { if (v) params.set(k, v); });
      const [dr, pr] = await Promise.all([
        fetch(`${VULA_API}/v1/documents/${tenantId.trim()}/filed?${params}`).then((r) => r.json()),
        projects.length ? Promise.resolve({ projects }) : fetch(`${VULA_API}/v1/documents/${tenantId.trim()}/projects`).then((r) => r.json()),
      ]);
      setDocs((prev) => append ? [...prev, ...(dr.documents || [])] : (dr.documents || []));
      setTotal(dr.total ?? (dr.documents || []).length);
      if (!projects.length) setProjects(pr.projects || []);
    } catch { /* ignore */ }
    finally { setLoading(false); }
  }, [tenantId, filters]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => { load(false); }, [tenantId, filters]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    if (customerPhone || !tenantId?.trim()) return;
    fetch(`${VULA_API}/v1/documents/${tenantId.trim()}/health`)
      .then((r) => r.json()).then((d) => setCategories(Object.entries(d.categories || {})))
      .catch(() => setCategories([]));
  }, [tenantId, customerPhone]);

  const setFilter = (k, v) => setFilters((f) => ({ ...f, [k]: v }));
  const loadMore = () => load(true, docs.length);

  const assign = async (docId, label) => {
    const proj = projects.find((p) => p.label === label);
    if (!proj) return;
    await fetch(`${VULA_API}/v1/documents/${docId}/assign-project`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ tenant_id: tenantId.trim(), project: proj.label, clickup_list_id: proj.clickup_list_id }),
    });
    load(false);
  };

  return { docs, projects, customers, categories, loading, total, lightboxDoc, setLightboxDoc, filters, setFilter, loadMore, assign };
}

/** "Just store this" — a photo/form/file with nothing to extract, no OCR/KB-ingest attempted. */
export function useMediaUpload(tenantId, onUploaded) {
  const customers = useCustomerOptions(tenantId);
  const [customerPhone, setCustomerPhone] = useState("");
  const [uploading, setUploading] = useState(false);
  const [msg, setMsg] = useState("");
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
  return { customers, customerPhone, setCustomerPhone, uploading, msg, handle };
}

// Google Drive import — search-based, not a folder browser. The drive.file OAuth scope (a
// deliberate least-privilege choice on the backend) means Vula only sees files it created itself
// or ones the tenant explicitly opened through it before, so a generic "browse my whole Drive"
// tree would show nothing useful for most tenants under this scope.
export function useDriveImport(tenantId, onImported) {
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
  return { status, q, setQ, results, searching, importingId, search, doImport };
}

// Documents › Health (2026-10-02): how many documents are in a catch-all type, missing a detail
// Vula couldn't read, waiting on a project, or filed under a name that isn't on the project list.
export function useDocumentsHealth(tenantId) {
  const [h, setH] = useState(null);
  const [register, setRegister] = useState([]);
  const [pick, setPick] = useState({});
  const [msg, setMsg] = useState("");
  const load = useCallback(() => {
    if (!tenantId?.trim()) return;
    fetch(`${VULA_API}/v1/documents/${tenantId.trim()}/health`)
      .then((r) => r.json()).then(setH).catch(() => setH(null));
    fetch(`${VULA_API}/v1/projects/${tenantId.trim()}`)
      .then((r) => r.json()).then((d) => setRegister(d.projects || [])).catch(() => setRegister([]));
  }, [tenantId]);
  useEffect(() => { load(); }, [load]);
  // A name that isn't on the project list: make it another name for a project, or a project.
  const addAlias = async (name) => {
    const pid = pick[name];
    if (!pid) return;
    const r = await fetch(`${VULA_API}/v1/projects/${tenantId.trim()}/p/${pid}/aliases`, {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ alias: name }) });
    const d = await r.json().catch(() => ({}));
    setMsg(r.ok ? `“${name}” now files under ${register.find((p) => p.id === pid)?.name} — ${(d.moved || {}).vula_filed_documents || 0} document(s) moved.` : (d.detail || "Couldn't add that name."));
    load();
  };
  const createFrom = async (name) => {
    const r = await fetch(`${VULA_API}/v1/projects/${tenantId.trim()}`, {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ name }) });
    const d = await r.json().catch(() => ({}));
    setMsg(r.ok && d.id ? `${name} is now on your project list.` : (d.detail || d.error || "Couldn't create it."));
    load();
  };
  return { h, register, pick, setPick, msg, addAlias, createFrom };
}

export function useKnowledgeVisibility(tenantId, refreshKey) {
  const [docs, setDocs] = useState(null);
  const [strict, setStrict] = useState(false);
  const [busy, setBusy] = useState("");
  const [msg, setMsg] = useState("");
  const [showAll, setShowAll] = useState(false);
  const load = useCallback(() => {
    if (!tenantId?.trim()) return;
    fetch(`${VULA_API}/documents/${tenantId.trim()}/kb`).then((r) => r.json())
      .then((d) => { setDocs(d.documents || []); setStrict(!!d.strict); })
      .catch(() => setDocs(null));
  }, [tenantId]);
  useEffect(() => { load(); }, [load, refreshKey]);
  const setAudience = async (doc, audience) => {
    setBusy(doc.doc_id);
    const r = await fetch(`${VULA_API}/documents/${tenantId.trim()}/kb/${encodeURIComponent(doc.doc_id)}/audience`, {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ audience }) });
    const d = await r.json().catch(() => ({}));
    setMsg(r.ok ? `${doc.filename}: ${AUDIENCE_BADGE[audience].label.toLowerCase()}.` : (d.detail || "Couldn't change that."));
    setBusy("");
    load();
  };
  return { docs, strict, busy, msg, showAll, setShowAll, setAudience };
}

// "What customers can see": the documents a customer's search can reach, and what one question
// would actually return — the same filter the customer assistant uses.
export function useCustomerPreview(tenantId) {
  const [q, setQ] = useState("");
  const [res, setRes] = useState(null);
  const [loading, setLoading] = useState(false);
  const run = async () => {
    if (!tenantId?.trim()) return;
    setLoading(true);
    try {
      const r = await fetch(`${VULA_API}/documents/${tenantId.trim()}/customer-view?q=${encodeURIComponent(q)}`);
      setRes(await r.json());
    } catch { setRes(null); }
    setLoading(false);
  };
  return { q, setQ, res, loading, run };
}

// Filed PDFs whose first read failed ("Email attachment") or that have no amount/supplier —
// re-analysed in the background with today's pipeline (vula/commerce/reread.py). Fills in
// the fields only; nothing is booked.
export function useReread(tenantId) {
  const [info, setInfo] = useState(null);
  const [msg, setMsg] = useState("");
  const load = useCallback(() => {
    if (!tenantId.trim()) return;
    fetch(`${VULA_API}/v1/commerce/${tenantId.trim()}/admin/documents/reread`)
      .then((r) => r.json()).then(setInfo).catch(() => setInfo(null));
  }, [tenantId]);
  useEffect(() => { load(); }, [load]);
  useEffect(() => {
    if (!info?.status?.running) return;
    const t = setInterval(load, 5000);
    return () => clearInterval(t);
  }, [info?.status?.running, load]);
  const start = async () => {
    setMsg("");
    const r = await fetch(`${VULA_API}/v1/commerce/${tenantId.trim()}/admin/documents/reread`, { method: "POST" });
    const d = await r.json().catch(() => ({}));
    if (!r.ok) { setMsg(d.detail || "Could not start the re-read."); return; }
    setMsg(d.started ? `Re-reading ${d.candidates} document${d.candidates === 1 ? "" : "s"} in the background…` : "A re-read is already running.");
    load();
  };
  const st = info?.status || {};
  const show = !!info && !!(info.candidates || st.total);
  const cats = Object.entries(st.categories || {}).map(([k, v]) => `${v} ${k}`).join(", ");
  return { info, st, show, cats, msg, start };
}

// Documents waiting on "which project?" that Vula can place itself (2026-09-28): the project
// named in the document, the bank payment that settled it, or the supplier's usual project.
export function useSortProjects(tenantId) {
  const [info, setInfo] = useState(null);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState("");
  const load = useCallback(() => {
    if (!tenantId.trim()) return;
    fetch(`${VULA_API}/v1/commerce/${tenantId.trim()}/admin/documents/sort-projects`)
      .then((r) => r.json()).then(setInfo).catch(() => setInfo(null));
  }, [tenantId]);
  useEffect(() => { load(); }, [load]);
  const apply = async (withSuggested) => {
    setBusy(true); setMsg("");
    const r = await fetch(`${VULA_API}/v1/commerce/${tenantId.trim()}/admin/documents/sort-projects${withSuggested ? "?include_suggested=1" : ""}`, { method: "POST" });
    const d = await r.json().catch(() => ({}));
    setBusy(false);
    setMsg(r.ok ? `Filed ${d.filed} document${d.filed === 1 ? "" : "s"} into projects.` : (d.detail || "Could not file them."));
    load();
  };
  return { info, busy, msg, apply, show: !!info && !!info.waiting };
}

// "Learn from history" (2026-09-28): one run over everything already filed — re-read what's
// missing, read every BOQ in full, and put every priced invoice/quote/BOQ line and the workers'
// day rates into the price book the QS Rates, Quick Cost, QS Pro and Takeoff read.
export function useLearn(tenantId) {
  const [info, setInfo] = useState(null);
  const [msg, setMsg] = useState("");
  const load = useCallback(() => {
    if (!tenantId.trim()) return;
    fetch(`${VULA_API}/v1/commerce/${tenantId.trim()}/admin/documents/learn`)
      .then((r) => r.json()).then(setInfo).catch(() => setInfo(null));
  }, [tenantId]);
  useEffect(() => { load(); }, [load]);
  useEffect(() => {
    if (!info?.status?.running) return;
    const t = setInterval(load, 5000);
    return () => clearInterval(t);
  }, [info?.status?.running, load]);
  const start = async () => {
    setMsg("");
    const r = await fetch(`${VULA_API}/v1/commerce/${tenantId.trim()}/admin/documents/learn`, { method: "POST" });
    const d = await r.json().catch(() => ({}));
    if (!r.ok) { setMsg(d.detail || "Could not start."); return; }
    setMsg(d.started ? "Learning from your documents in the background — this can take a few minutes…" : "Already running.");
    load();
  };
  return { info, st: info?.status || {}, book: info?.price_book || {}, msg, start };
}

// "Import from your website" (2026-10-12, onboarding SeedleMe): the shop's products (with their
// options) and the pages customers ask about — About, FAQ, delivery, returns, care, blog — read
// from the business's own site into Products and the knowledge base. Re-running updates.
export function useWebsiteImport(tenantId) {
  const [url, setUrl] = useState("");
  const [st, setSt] = useState(null);
  const [msg, setMsg] = useState("");
  const base = `${VULA_API}/v1/commerce/${tenantId.trim()}/admin/site-import`;
  const load = useCallback(() => {
    if (!tenantId.trim()) return;
    fetch(base).then((r) => r.json()).then((d) => setSt(d.status || null)).catch(() => setSt(null));
  }, [base, tenantId]);
  useEffect(() => { load(); }, [load]);
  useEffect(() => {
    if (!st?.running) return;
    const t = setInterval(load, 4000);
    return () => clearInterval(t);
  }, [st?.running, load]);
  useEffect(() => { if (st?.url && !url) setUrl(st.url); }, [st?.url]); // eslint-disable-line react-hooks/exhaustive-deps
  const start = async () => {
    setMsg("");
    const r = await fetch(base, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ url }) });
    const d = await r.json().catch(() => ({}));
    if (!r.ok) { setMsg(d.detail || "Could not start."); return; }
    setMsg(d.started ? `Reading ${d.url} in the background — products first, then the pages…` : "Already running.");
    load();
  };
  return { url, setUrl, st, msg, start };
}

/** The knowledge-base files list plus the upload queue (batch ingest, one-by-one fallback). */
export function useKbUpload(tenantId) {
  const [docs, setDocs] = useState([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [queue, setQueue] = useState([]);
  const [uploading, setUploading] = useState(false);
  const [libraryKey, setLibraryKey] = useState(0);
  // Uploads are team-only unless ticked (2026-10-17): an invoice must never reach a customer.
  const [forCustomers, setForCustomers] = useState(false);

  const load = useCallback(async () => {
    if (!tenantId.trim()) return;
    setLoading(true);
    setError("");
    try {
      const resp = await fetch(`${VULA_API}/documents/${tenantId.trim()}`);
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
      const data = await resp.json();
      setDocs(data.documents || []);
    } catch (e) {
      setError(e.message);
    } finally {
      setLoading(false);
    }
  }, [tenantId]);

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
  // Files shared into Vula from the phone (share/ShareSheet.jsx) join the queue; Upload still sends them.
  const [fromShare, setFromShare] = useState(false);
  useEffect(() => {
    const shared = takeHandOff("documents");
    if (shared?.length) { onFiles(shared); setFromShare(true); }
  }, []);  // eslint-disable-line react-hooks/exhaustive-deps

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
      fd.append("audience", forCustomers ? "public" : "staff");
      pending.forEach((item) => fd.append("files", item.file));
      const resp = await fetch(`${VULA_API}/ingest/batch`, { method: "POST", body: fd });

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
            fd2.append("audience", forCustomers ? "public" : "staff");
            fd2.append("file", item.file);
            const r = await fetch(`${VULA_API}/ingest`, { method: "POST", body: fd2 });
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
    setTimeout(() => setLibraryKey((k) => k + 1), 8000);   // the visibility list, once ingested
  };

  const bumpLibrary = () => setLibraryKey((k) => k + 1);
  const clearQueue = () => setQueue([]);
  const pendingCount = queue.filter((q) => q.status === "pending").length;
  return { fromShare, docs, loading, error, load, queue, uploading, libraryKey, bumpLibrary, forCustomers, setForCustomers, onFiles, uploadAll, clearQueue, pendingCount };
}
