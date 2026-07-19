/**
 * VulaContacts.jsx — the email-derived contact / supplier / co-worker library (KM).
 * Built automatically from the connected mailbox; re-tag any contact; sync on demand.
 */
import { useState, useEffect, useCallback, useRef } from "react";
import { downloadCsv } from "../lib/csv";

const VULA_API = import.meta.env.VITE_API_URL || "https://vula-group-production.up.railway.app";
const C = { surface: "#FFFFFF", border: "#DDD8CE", green: "var(--accent)", text: "#2A2A2A", muted: "#8A8680", surfaceAlt: "#F0EDE5" };
const KIND = { internal: { label: "Co-worker", color: "var(--accent)" }, supplier: { label: "Supplier", color: "#C4861A" }, client: { label: "Client", color: "#2B5797" }, external: { label: "External", color: "#8A8680" } };
const KINDS = ["internal", "client", "supplier", "external"];

function parseCsv(text) {
  const lines = text.split(/\r?\n/).filter(l => l.trim());
  if (!lines.length) return [];
  const header = lines[0].split(",").map(h => h.trim().toLowerCase());
  return lines.slice(1).map(line => {
    const cells = line.split(",").map(c => c.trim().replace(/^"|"$/g, ""));
    const row = {};
    header.forEach((h, i) => { row[h] = cells[i] || ""; });
    return row;
  });
}

export default function VulaContacts({ tenantId }) {
  const [contacts, setContacts] = useState([]);
  const [q, setQ] = useState("");
  const [tab, setTab] = useState("all");
  const [syncing, setSyncing] = useState(false);
  const [importing, setImporting] = useState(false);
  const [msg, setMsg] = useState("");
  const fileRef = useRef(null);

  const load = useCallback(async () => {
    if (!tenantId) return;
    const r = await fetch(`${VULA_API}/v1/email/contacts/${tenantId}`);
    const d = await r.json();
    setContacts(d.contacts || []);
  }, [tenantId]);
  useEffect(() => { load(); }, [load]);

  const syncNow = async () => {
    setSyncing(true); setMsg("");
    try {
      const r = await fetch(`${VULA_API}/v1/email/sync/${tenantId}`, { method: "POST" });
      const d = await r.json();
      setMsg(d.synced != null ? `Synced ${d.synced} new email(s) · ${d.filed_attachments || 0} attachment(s) filed` : (d.error || "No mailbox connected"));
      load();
    } catch (e) { setMsg("Sync failed"); } finally { setSyncing(false); }
  };
  const retag = async (id, kind) => {
    await fetch(`${VULA_API}/v1/email/contacts/${tenantId}/${id}`, {
      method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ kind }),
    });
    load();
  };

  const importCsv = async (file) => {
    if (!file) return;
    setImporting(true); setMsg("");
    try {
      const text = await file.text();
      const rows = parseCsv(text).filter(r => r.email);
      if (!rows.length) { setMsg("No 'email' column found — headers should include name,email,kind"); setImporting(false); return; }
      const r = await fetch(`${VULA_API}/v1/email/contacts/${tenantId}/import`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ contacts: rows }),
      }).then(r => r.json());
      setMsg(`Imported ${r.imported || 0} contact(s)${r.skipped ? ` · ${r.skipped} skipped` : ""}`);
      load();
    } catch (e) { setMsg("Import failed — check the CSV format"); }
    setImporting(false);
    if (fileRef.current) fileRef.current.value = "";
  };

  const counts = contacts.reduce((a, c) => { a[c.kind] = (a[c.kind] || 0) + 1; return a; }, {});
  let rows = tab === "all" ? contacts : contacts.filter(c => c.kind === tab);
  if (q) { const s = q.toLowerCase(); rows = rows.filter(c => (c.name || "").toLowerCase().includes(s) || (c.email || "").includes(s)); }

  return (
    <div style={{ maxWidth: 980, margin: "0 auto", padding: 24 }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", flexWrap: "wrap", gap: 10 }}>
        <div>
          <h1 style={{ fontFamily: "'Cormorant Garamond', serif", fontSize: 28, fontWeight: 700, color: C.text, margin: "0 0 2px" }}>Contacts</h1>
          <p style={{ fontSize: 13, color: C.muted, margin: 0 }}>Your email directory — co-workers, clients & suppliers. (Buyers who order/message you live in 👥 Customers.)</p>
        </div>
        <div style={{ display: "flex", gap: 8 }}>
          <button onClick={() => downloadCsv("contacts", contacts, [
            { key: "name", label: "Name" }, { key: "email", label: "Email" },
            { key: "kind", label: "Kind" }, { key: "message_count", label: "Messages" },
          ])} disabled={!contacts.length} style={{ padding: "9px 14px", background: C.surface, border: `1px solid ${C.border}`, borderRadius: 8, fontSize: 13, fontWeight: 600, cursor: "pointer", color: C.text }}>
            ⬇ Export CSV
          </button>
          <input ref={fileRef} type="file" accept=".csv" onChange={e => importCsv(e.target.files?.[0])} style={{ display: "none" }} />
          <button onClick={() => fileRef.current?.click()} disabled={importing} style={{ padding: "9px 14px", background: C.surface, border: `1px solid ${C.border}`, borderRadius: 8, fontSize: 13, fontWeight: 600, cursor: "pointer", color: C.text }}>
            {importing ? "Importing…" : "📥 Import CSV"}
          </button>
          <button onClick={syncNow} disabled={syncing} style={{ padding: "9px 16px", background: C.green, color: "#fff", border: "none", borderRadius: 8, fontSize: 13, fontWeight: 600, cursor: "pointer" }}>
            {syncing ? "Syncing…" : "↻ Sync now"}
          </button>
        </div>
      </div>
      {msg && <p style={{ fontSize: 12, color: C.green, marginTop: 8 }}>{msg}</p>}
      <p style={{ fontSize: 11, color: "#B5B0A8", marginTop: -4 }}>CSV columns: name, email, kind (internal/client/supplier/external — optional, defaults to external)</p>

      <div style={{ display: "flex", gap: 6, margin: "16px 0", flexWrap: "wrap" }}>
        {[["all", `All · ${contacts.length}`], ["internal", `Co-workers · ${counts.internal || 0}`], ["client", `Clients · ${counts.client || 0}`], ["supplier", `Suppliers · ${counts.supplier || 0}`], ["external", `External · ${counts.external || 0}`]].map(([id, label]) => (
          <button key={id} onClick={() => setTab(id)} style={{ padding: "7px 12px", borderRadius: 20, border: `1px solid ${C.border}`, background: tab === id ? C.green : C.surface, color: tab === id ? "#fff" : C.text, fontSize: 12, cursor: "pointer" }}>{label}</button>
        ))}
        <input value={q} onChange={e => setQ(e.target.value)} placeholder="Search…" style={{ marginLeft: "auto", padding: "7px 10px", border: `1px solid ${C.border}`, borderRadius: 8, fontSize: 13 }} />
      </div>

      <div style={{ background: C.surface, border: `1px solid ${C.border}`, borderRadius: 10, overflow: "hidden" }}>
        {rows.length === 0 && <div style={{ padding: 18, fontSize: 13, color: C.muted }}>No contacts yet — connect a mailbox in Settings, then ↻ Sync now.</div>}
        {rows.map(c => (
          <div key={c.id} style={{ padding: "11px 16px", borderBottom: `1px solid ${C.surfaceAlt}`, display: "grid", gridTemplateColumns: "1.6fr 1.6fr 70px 130px", gap: 10, alignItems: "center", fontSize: 13 }}>
            <span style={{ color: C.text, fontWeight: 600 }}>{c.name || c.email.split("@")[0]}</span>
            <span style={{ color: C.muted }}>{c.email}</span>
            <span style={{ color: C.muted, fontSize: 12 }}>{c.message_count} msg</span>
            <select value={c.kind} onChange={e => retag(c.id, e.target.value)}
              style={{ padding: "5px 7px", border: `1px solid ${C.border}`, borderRadius: 6, fontSize: 12, color: (KIND[c.kind] || KIND.external).color, fontWeight: 600 }}>
              {KINDS.map(k => <option key={k} value={k} style={{ color: C.text }}>{KIND[k].label}</option>)}
            </select>
          </div>
        ))}
      </div>
      <p style={{ textAlign: "center", fontSize: 11, color: "#B5B0A8", marginTop: 22 }}>Powered by Vula · auto-synced every 15 min</p>
    </div>
  );
}
