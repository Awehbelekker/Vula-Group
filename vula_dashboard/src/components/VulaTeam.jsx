/**
 * VulaTeam.jsx — tenant team directory. Add members with their own WhatsApp, choose what
 * each can see (access) and which notifications they get by WhatsApp (per event).
 */
import { useState, useEffect, useCallback } from "react";

const VULA_API = import.meta.env.VITE_API_URL || "https://vula-group-production.up.railway.app";
const C = { surface: "#FFFFFF", border: "#DDD8CE", green: "var(--accent)", text: "#2A2A2A", muted: "#8A8680", alt: "#F0EDE5" };

// Access modules map 1:1 to dashboard tab ids.
const MODULES = [
  ["overview", "Overview"], ["assistant", "Assistant"], ["orders", "Orders"], ["products", "Products"],
  ["invoices", "Invoices"], ["finances", "Finances"], ["budget", "Budget"], ["customers", "Customers"],
  ["contacts", "Contacts"], ["followups", "Follow-ups"], ["broadcast", "Broadcast"],
  ["projects", "Projects"], ["qsrates", "QS Rates"], ["documents", "Documents"], ["scanner", "Scanner"],
];
const EVENTS = [
  ["which_project", "Which-project?"], ["followup_digest", "Follow-up digest"],
  ["payment_received", "Payments"], ["new_invoice", "New invoices"],
  ["new_order", "New orders"], ["low_stock", "Low stock"],
];
const ROLES = ["owner", "manager", "bookkeeper", "staff"];

export default function VulaTeam({ tenantId }) {
  const [members, setMembers] = useState([]);
  const [form, setForm] = useState({ name: "", whatsapp: "", role: "staff" });

  const load = useCallback(async () => {
    if (!tenantId) return;
    const r = await fetch(`${VULA_API}/v1/team/${tenantId}`);
    const d = await r.json();
    setMembers(d.members || []);
  }, [tenantId]);
  useEffect(() => { load(); }, [load]);

  const add = async () => {
    if (!form.name && !form.whatsapp) return;
    await fetch(`${VULA_API}/v1/team/${tenantId}`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ ...form, access: [], notify: ["which_project", "followup_digest"] }),
    });
    setForm({ name: "", whatsapp: "", role: "staff" }); load();
  };
  const patch = async (id, body) => {
    await fetch(`${VULA_API}/v1/team/${tenantId}/${id}`, {
      method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
    });
    load();
  };
  const del = async (id) => { await fetch(`${VULA_API}/v1/team/${tenantId}/${id}`, { method: "DELETE" }); load(); };
  const toggle = (arr, key) => (arr || []).includes(key) ? arr.filter((k) => k !== key) : [...(arr || []), key];

  const Chip = ({ on, label, onClick }) => (
    <button onClick={onClick} style={{ padding: "3px 9px", borderRadius: 14, fontSize: 11, cursor: "pointer",
      border: `1px solid ${on ? C.green : C.border}`, background: on ? C.green : C.surface, color: on ? "#fff" : C.muted }}>{label}</button>
  );

  return (
    <div style={{ maxWidth: 1000, margin: "0 auto", padding: 24 }}>
      <h1 style={{ fontFamily: "'Cormorant Garamond', serif", fontSize: 28, fontWeight: 700, color: C.text, margin: "0 0 2px" }}>Team</h1>
      <p style={{ fontSize: 13, color: C.muted, margin: "0 0 18px" }}>Add people, give each their own WhatsApp, and choose what they see and get notified about.</p>

      <div style={{ display: "flex", gap: 8, marginBottom: 18, flexWrap: "wrap" }}>
        <input placeholder="Name" value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} style={{ padding: "8px 10px", border: `1px solid ${C.border}`, borderRadius: 8, fontSize: 13 }} />
        <input placeholder="WhatsApp e.g. 2782…" value={form.whatsapp} onChange={(e) => setForm({ ...form, whatsapp: e.target.value })} style={{ padding: "8px 10px", border: `1px solid ${C.border}`, borderRadius: 8, fontSize: 13 }} />
        <select value={form.role} onChange={(e) => setForm({ ...form, role: e.target.value })} style={{ padding: "8px 10px", border: `1px solid ${C.border}`, borderRadius: 8, fontSize: 13 }}>
          {ROLES.map((r) => <option key={r} value={r}>{r}</option>)}
        </select>
        <button onClick={add} style={{ padding: "8px 16px", background: C.green, color: "#fff", border: "none", borderRadius: 8, fontSize: 13, fontWeight: 600, cursor: "pointer" }}>+ Add member</button>
      </div>

      {members.length === 0 && <div style={{ padding: 16, fontSize: 13, color: C.muted, background: C.surface, border: `1px solid ${C.border}`, borderRadius: 10 }}>No team members yet — add one above. (Until then, notifications go to the tenant's default number.)</div>}

      {members.map((m) => (
        <div key={m.id} style={{ background: C.surface, border: `1px solid ${C.border}`, borderRadius: 10, padding: 16, marginBottom: 12, opacity: m.active ? 1 : 0.55 }}>
          <div style={{ display: "flex", alignItems: "center", gap: 10, marginBottom: 10, flexWrap: "wrap" }}>
            <span style={{ fontWeight: 700, color: C.text }}>{m.name || m.whatsapp}</span>
            <span style={{ fontSize: 12, color: C.muted }}>{m.whatsapp}</span>
            <select value={m.role} onChange={(e) => patch(m.id, { role: e.target.value })} style={{ padding: "3px 7px", border: `1px solid ${C.border}`, borderRadius: 6, fontSize: 11.5 }}>
              {ROLES.map((r) => <option key={r} value={r}>{r}</option>)}
            </select>
            <div style={{ marginLeft: "auto", display: "flex", gap: 8 }}>
              <button onClick={() => patch(m.id, { active: !m.active })} style={{ fontSize: 11.5, color: C.muted, background: "none", border: `1px solid ${C.border}`, borderRadius: 6, padding: "3px 9px", cursor: "pointer" }}>{m.active ? "Disable" : "Enable"}</button>
              <button onClick={() => del(m.id)} style={{ fontSize: 11.5, color: "#A23B2D", background: "none", border: `1px solid ${C.border}`, borderRadius: 6, padding: "3px 9px", cursor: "pointer" }}>Remove</button>
            </div>
          </div>
          <div style={{ fontSize: 11, color: C.muted, marginBottom: 4, textTransform: "uppercase" }}>Can access {m.role === "owner" || m.role === "manager" ? "· (owner/manager see everything)" : ""}</div>
          <div style={{ display: "flex", gap: 5, flexWrap: "wrap", marginBottom: 12 }}>
            {MODULES.map(([k, label]) => <Chip key={k} label={label} on={(m.access || []).includes(k)} onClick={() => patch(m.id, { access: toggle(m.access, k) })} />)}
          </div>
          <div style={{ fontSize: 11, color: C.muted, marginBottom: 4, textTransform: "uppercase" }}>Notify by WhatsApp</div>
          <div style={{ display: "flex", gap: 5, flexWrap: "wrap" }}>
            {EVENTS.map(([k, label]) => <Chip key={k} label={label} on={(m.notify || []).includes(k)} onClick={() => patch(m.id, { notify: toggle(m.notify, k) })} />)}
          </div>
        </div>
      ))}
      <p style={{ textAlign: "center", fontSize: 11, color: "#B5B0A8", marginTop: 12 }}>Powered by Vula · each member gets only what you select</p>
    </div>
  );
}
