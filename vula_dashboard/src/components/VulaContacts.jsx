/**
 * VulaContacts.jsx — the email-derived contact / supplier / co-worker library (KM).
 * Built automatically from the connected mailbox; re-tag any contact; sync on demand.
 */
import { useState, useEffect, useCallback } from "react";

const VULA_API = import.meta.env.VITE_API_URL || "https://vula-group-production.up.railway.app";
const C = { surface: "#FFFFFF", border: "#DDD8CE", green: "var(--accent)", text: "#2A2A2A", muted: "#8A8680", surfaceAlt: "#F0EDE5" };
const KIND = { internal: { label: "Co-worker", color: "var(--accent)" }, supplier: { label: "Supplier", color: "#C4861A" }, client: { label: "Client", color: "#2B5797" }, external: { label: "External", color: "#8A8680" } };
const KINDS = ["internal", "client", "supplier", "external"];

export default function VulaContacts({ tenantId }) {
  const [contacts, setContacts] = useState([]);
  const [q, setQ] = useState("");
  const [tab, setTab] = useState("all");
  const [syncing, setSyncing] = useState(false);
  const [msg, setMsg] = useState("");

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

  const counts = contacts.reduce((a, c) => { a[c.kind] = (a[c.kind] || 0) + 1; return a; }, {});
  let rows = tab === "all" ? contacts : contacts.filter(c => c.kind === tab);
  if (q) { const s = q.toLowerCase(); rows = rows.filter(c => (c.name || "").toLowerCase().includes(s) || (c.email || "").includes(s)); }

  return (
    <div style={{ maxWidth: 980, margin: "0 auto", padding: 24 }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", flexWrap: "wrap", gap: 10 }}>
        <div>
          <h1 style={{ fontFamily: "'Cormorant Garamond', serif", fontSize: 28, fontWeight: 700, color: C.text, margin: "0 0 2px" }}>Contacts</h1>
          <p style={{ fontSize: 13, color: C.muted, margin: 0 }}>Built automatically from your email — co-workers, clients & suppliers.</p>
        </div>
        <button onClick={syncNow} disabled={syncing} style={{ padding: "9px 16px", background: C.green, color: "#fff", border: "none", borderRadius: 8, fontSize: 13, fontWeight: 600, cursor: "pointer" }}>
          {syncing ? "Syncing…" : "↻ Sync now"}
        </button>
      </div>
      {msg && <p style={{ fontSize: 12, color: C.green, marginTop: 8 }}>{msg}</p>}

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
