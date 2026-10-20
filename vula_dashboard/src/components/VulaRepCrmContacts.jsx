/**
 * VulaRepCrmContacts.jsx — a sales rep's own contacts (commerce_contacts, created_by-scoped).
 * Distinct from VulaContacts.jsx, which is an unrelated email-derived contact directory.
 */
import { useRepContacts } from "./rep/useRepContacts";
import RepContactsV2 from "./rep/RepContactsV2";

const C = { surface: "var(--surface)", border: "var(--border)", green: "var(--accent)", text: "var(--text)", muted: "var(--muted)" };

function VulaRepCrmContactsV1({ tenantId, repPhone }) {
  const { contacts, search, setSearch, form, setForm, msg, save } = useRepContacts(tenantId, repPhone);

  return (
    <div style={{ maxWidth: 900, margin: "0 auto", padding: 20 }}>
      <h2 style={{ fontSize: 20, fontWeight: 700, color: C.text, margin: "0 0 4px" }}>My Contacts</h2>
      <p style={{ fontSize: 13, color: C.muted, margin: "0 0 16px" }}>
        Contacts you've saved via WhatsApp (log_meeting, create_contact) or added here.
      </p>

      <div style={{ background: C.surface, border: `1px solid ${C.border}`, borderRadius: 10, padding: 16, marginBottom: 16 }}>
        <div style={{ fontWeight: 700, marginBottom: 10 }}>Add a contact</div>
        <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
          <input placeholder="Name*" value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })}
                 style={inputStyle} />
          <input placeholder="Phone" value={form.phone} onChange={(e) => setForm({ ...form, phone: e.target.value })}
                 style={inputStyle} />
          <input placeholder="Email" value={form.email} onChange={(e) => setForm({ ...form, email: e.target.value })}
                 style={inputStyle} />
          <input placeholder="Company" value={form.company} onChange={(e) => setForm({ ...form, company: e.target.value })}
                 style={inputStyle} />
          <input placeholder="Title" value={form.title} onChange={(e) => setForm({ ...form, title: e.target.value })}
                 style={inputStyle} />
        </div>
        <input placeholder="Notes" value={form.notes} onChange={(e) => setForm({ ...form, notes: e.target.value })}
               style={{ ...inputStyle, width: "100%", marginTop: 8 }} />
        {msg && <div style={{ fontSize: 12.5, color: "var(--danger)", marginTop: 8 }}>{msg}</div>}
        <button onClick={save} style={{ marginTop: 10, padding: "8px 16px", background: C.green, color: "var(--on-accent)",
                 border: "none", borderRadius: 8, fontSize: 13, fontWeight: 600, cursor: "pointer" }}>+ Save contact</button>
      </div>

      <input placeholder="Search by name…" value={search} onChange={(e) => setSearch(e.target.value)}
             style={{ ...inputStyle, width: "100%", marginBottom: 12 }} />

      {contacts.length === 0 && <div style={{ padding: 16, fontSize: 13, color: C.muted, background: C.surface, border: `1px solid ${C.border}`, borderRadius: 10 }}>No contacts yet.</div>}
      {contacts.map((c) => (
        <div key={c.id || c.phone} style={{ background: C.surface, border: `1px solid ${C.border}`, borderRadius: 10, padding: "12px 16px", marginBottom: 8 }}>
          <div style={{ fontWeight: 700, color: C.text }}>{c.name}</div>
          <div style={{ fontSize: 12.5, color: C.muted }}>
            {[c.title, c.company].filter(Boolean).join(" · ")}
            {(c.phone && !c.phone.startsWith("nophone")) ? ` · ${c.phone}` : ""}{c.email ? ` · ${c.email}` : ""}
          </div>
          {c.notes && <div style={{ fontSize: 12.5, color: C.muted, marginTop: 4 }}>{c.notes}</div>}
        </div>
      ))}
    </div>
  );
}

const inputStyle = { padding: "8px 10px", border: `1px solid ${C.border}`, borderRadius: 8, fontSize: 13, flex: "1 1 160px" };

export default function VulaRepCrmContacts({ v2 = false, ...props }) {
  return v2 ? <RepContactsV2 {...props} /> : <VulaRepCrmContactsV1 {...props} />;
}
