/**
 * VulaContacts.jsx — the email-derived contact / supplier / co-worker library (KM).
 * Built automatically from the connected mailbox; re-tag any contact; sync on demand.
 * Data and actions live in contacts/useContacts.js (shared with contacts/ContactsV2.jsx).
 */
import { useContacts } from "./contacts/useContacts";
import ContactsV2 from "./contacts/ContactsV2";
import { sidePad } from "../lib/layout";

const C = { surface: "var(--surface)", border: "var(--border)", green: "var(--accent)", text: "var(--text)", muted: "var(--muted)", surfaceAlt: "var(--surface-alt)" };
const KIND = { internal: { label: "Co-worker", color: "var(--accent)" }, supplier: { label: "Supplier", color: "var(--warn)" }, client: { label: "Client", color: "var(--info)" }, external: { label: "External", color: "var(--muted)" } };
const KINDS = ["internal", "client", "supplier", "external"];

/** v2: the redesigned screen (same hook, so the same data and actions). */
export default function VulaContacts({ tenantId, v2 = false }) {
  if (v2) return <ContactsV2 tenantId={tenantId} />;
  return <ContactsV1 tenantId={tenantId} />;
}

// data + actions in contacts/useContacts.js (shared with contacts/ContactsV2.jsx)
function ContactsV1({ tenantId }) {
  const { contacts, rows, counts, q, setQ, tab, setTab, syncing, importing, msg, fileRef, syncNow, retag, importCsv, exportCsv } = useContacts(tenantId);

  return (
    <div style={{ maxWidth: 980, margin: "0 auto", padding: `24px ${sidePad(24)}` }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", flexWrap: "wrap", gap: 10 }}>
        <div>
          <h1 style={{ fontFamily: "var(--font-display)", fontSize: 28, fontWeight: 700, color: C.text, margin: "0 0 2px" }}>Contacts</h1>
          <p style={{ fontSize: 13, color: C.muted, margin: 0 }}>Your email directory — co-workers, clients & suppliers. (Buyers who order/message you live in 👥 Customers.)</p>
        </div>
        <div style={{ display: "flex", gap: 8 }}>
          <button onClick={exportCsv} disabled={!contacts.length} style={{ padding: "9px 14px", background: C.surface, border: `1px solid ${C.border}`, borderRadius: 8, fontSize: 13, fontWeight: 600, cursor: "pointer", color: C.text }}>
            ⬇ Export CSV
          </button>
          <input ref={fileRef} type="file" accept=".csv" onChange={e => importCsv(e.target.files?.[0])} style={{ display: "none" }} />
          <button onClick={() => fileRef.current?.click()} disabled={importing} style={{ padding: "9px 14px", background: C.surface, border: `1px solid ${C.border}`, borderRadius: 8, fontSize: 13, fontWeight: 600, cursor: "pointer", color: C.text }}>
            {importing ? "Importing…" : "📥 Import CSV"}
          </button>
          <button onClick={syncNow} disabled={syncing} style={{ padding: "9px 16px", background: C.green, color: "var(--on-accent)", border: "none", borderRadius: 8, fontSize: 13, fontWeight: 600, cursor: "pointer" }}>
            {syncing ? "Syncing…" : "↻ Sync now"}
          </button>
        </div>
      </div>
      {msg && <p style={{ fontSize: 12, color: C.green, marginTop: 8 }}>{msg}</p>}
      <p style={{ fontSize: 11, color: "var(--faint)", marginTop: -4 }}>CSV columns: name, email, kind (internal/client/supplier/external — optional, defaults to external)</p>

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
      <p style={{ textAlign: "center", fontSize: 11, color: "var(--faint)", marginTop: 22 }}><span className="vula-powered">Powered by Vula · </span>auto-synced every 15 min</p>
    </div>
  );
}
