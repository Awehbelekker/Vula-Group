/**
 * VulaOrderWorkflow.jsx — per-tenant order workflow settings.
 * Require owner approval before fulfilment, and choose where the fulfilment ticket goes.
 */
import { useState, useEffect } from "react";

const VULA_API = import.meta.env.VITE_API_URL || "https://vula-group-production.up.railway.app";
const C = { surface: "#FFFFFF", border: "#DDD8CE", accent: "var(--accent)", text: "#2A2A2A", muted: "#8A8680" };

export default function VulaOrderWorkflow({ tenantId }) {
  const [s, setS] = useState(null);
  const [msg, setMsg] = useState("");

  useEffect(() => {
    fetch(`${VULA_API}/v1/commerce/${tenantId}/admin/order-settings`)
      .then((r) => r.json()).then((d) => setS(d.settings || {})).catch(() => setS({}));
  }, [tenantId]);

  const save = async (patch) => {
    const next = { ...s, ...patch };
    setS(next);
    const d = await fetch(`${VULA_API}/v1/commerce/${tenantId}/admin/order-settings`, {
      method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify(next),
    }).then((r) => r.json()).catch(() => ({}));
    if (d.settings) setS(d.settings);
    setMsg("Saved ✓"); setTimeout(() => setMsg(""), 1500);
  };

  if (!s) return null;
  const field = { padding: "8px 10px", border: `1px solid ${C.border}`, borderRadius: 8, fontSize: 13 };

  return (
    <div style={{ background: C.surface, border: `1px solid ${C.border}`, borderRadius: 10, padding: 16, marginBottom: 16 }}>
      <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
        <span style={{ fontWeight: 700, color: C.text }}>Order workflow</span>
        {msg && <span style={{ fontSize: 12, color: "#2C7A4B" }}>{msg}</span>}
      </div>
      <p style={{ fontSize: 12, color: C.muted, margin: "2px 0 12px" }}>Approve orders before fulfilment, and choose where the ticket is sent.</p>

      <label style={{ display: "flex", alignItems: "center", gap: 8, fontSize: 13, color: C.text, marginBottom: 12, cursor: "pointer" }}>
        <input type="checkbox" checked={!!s.require_approval} onChange={(e) => save({ require_approval: e.target.checked })} />
        Require owner approval before fulfilment (WhatsApp the owner to APPROVE/REJECT)
      </label>

      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit,minmax(200px,1fr))", gap: 10 }}>
        <label style={{ display: "flex", flexDirection: "column", gap: 4 }}>
          <span style={{ fontSize: 11, color: C.muted, textTransform: "uppercase" }}>Dispatch fulfilment via</span>
          <select value={s.dispatch_channel || "whatsapp"} onChange={(e) => save({ dispatch_channel: e.target.value })} style={field}>
            <option value="whatsapp">WhatsApp</option>
            <option value="email">Email</option>
            <option value="both">Both</option>
            <option value="none">Don't dispatch</option>
          </select>
        </label>
        <label style={{ display: "flex", flexDirection: "column", gap: 4 }}>
          <span style={{ fontSize: 11, color: C.muted, textTransform: "uppercase" }}>Fulfilment WhatsApp</span>
          <input defaultValue={s.fulfillment_whatsapp || ""} placeholder="2782…" style={field}
            onBlur={(e) => e.target.value !== (s.fulfillment_whatsapp || "") && save({ fulfillment_whatsapp: e.target.value })} />
        </label>
        <label style={{ display: "flex", flexDirection: "column", gap: 4 }}>
          <span style={{ fontSize: 11, color: C.muted, textTransform: "uppercase" }}>Fulfilment email</span>
          <input defaultValue={s.fulfillment_email || ""} placeholder="kitchen@…" style={field}
            onBlur={(e) => e.target.value !== (s.fulfillment_email || "") && save({ fulfillment_email: e.target.value })} />
        </label>
      </div>
    </div>
  );
}
