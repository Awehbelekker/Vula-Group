/**
 * VulaInvoiceApproval.jsx — public, no-login page for a client to approve an invoice they were
 * sent (opt-in per invoice, migration 136). Mounted pre-auth on the hash route
 * #/approve-invoice/:tenant/:invoiceId?token=... — same pattern as VulaPageRender.jsx's public
 * #/page/:tenant/:slug route. Informational only on the Vula side: approving here never blocks
 * or changes anything about Mark paid/Record payment in the dashboard.
 */
import { useState, useEffect } from "react";

const VULA_API = import.meta.env.VITE_API_URL || "https://vula-group-production.up.railway.app";
const fmt = (c) => `R${((c || 0) / 100).toFixed(2)}`;

export default function VulaInvoiceApproval({ tenant, invoiceId }) {
  const [invoice, setInvoice] = useState(undefined); // undefined = loading, null = not found
  const [name, setName] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState("");
  const token = new URLSearchParams(window.location.hash.split("?")[1] || "").get("token") || "";

  useEffect(() => {
    if (!token) { setInvoice(null); return; }
    fetch(`${VULA_API}/v1/commerce/${tenant}/invoices/${invoiceId}/approve?token=${encodeURIComponent(token)}`)
      .then((r) => (r.ok ? r.json() : Promise.reject(r.status)))
      .then(setInvoice)
      .catch(() => setInvoice(null));
  }, [tenant, invoiceId, token]);

  async function approve() {
    setSubmitting(true);
    setError("");
    try {
      const r = await fetch(`${VULA_API}/v1/commerce/${tenant}/invoices/${invoiceId}/approve`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ token, approved_by: name || undefined }),
      });
      const d = await r.json().catch(() => ({}));
      if (!r.ok) { setError(d.detail || "Could not approve this invoice."); return; }
      setInvoice((prev) => ({ ...prev, approved_at: d.approved_at, approved_by: d.approved_by }));
    } catch {
      setError("Could not approve this invoice — please try again.");
    } finally {
      setSubmitting(false);
    }
  }

  if (invoice === undefined) return <div style={s.centre}>Loading…</div>;
  if (invoice === null) return <div style={s.centre}>This link is invalid or has expired.</div>;

  return (
    <div style={s.outer}>
      <div style={s.card}>
        <h1 style={s.heading}>Invoice {invoice.invoice_number}</h1>
        <p style={s.sub}>{invoice.customer_name}</p>

        <div style={s.items}>
          {(invoice.line_items || []).map((it, i) => (
            <div key={i} style={s.itemRow}>
              <span>{it.description}{it.quantity > 1 ? ` × ${it.quantity}` : ""}</span>
              <span>{fmt(it.total_cents)}</span>
            </div>
          ))}
        </div>
        <div style={s.totals}>
          <div style={s.totRow}><span>Subtotal</span><span>{fmt(invoice.subtotal_cents)}</span></div>
          <div style={s.totRow}><span>VAT</span><span>{fmt(invoice.vat_cents)}</span></div>
          <div style={{ ...s.totRow, ...s.totFinal }}><span>Total</span><span>{fmt(invoice.total_cents)}</span></div>
        </div>
        {invoice.due_date && <p style={s.sub}>Due {invoice.due_date}</p>}

        {invoice.approved_at ? (
          <div style={s.approvedBox}>
            ✅ Approved{invoice.approved_by ? ` by ${invoice.approved_by}` : ""} on {invoice.approved_at.slice(0, 10)}
          </div>
        ) : (
          <>
            <label style={s.label}>Your name (optional)
              <input value={name} onChange={(e) => setName(e.target.value)} placeholder="e.g. Judy Downing" style={s.input} />
            </label>
            {error && <div style={s.errorBox}>{error}</div>}
            <button onClick={approve} disabled={submitting} style={s.btn}>
              {submitting ? "Approving…" : "✅ Approve this invoice"}
            </button>
          </>
        )}
      </div>
    </div>
  );
}

const s = {
  centre:      { padding: 48, fontFamily: "system-ui", textAlign: "center", color: "#666" },
  outer:       { minHeight: "100vh", background: "#F7F4EE", display: "flex", alignItems: "center", justifyContent: "center", padding: 24, fontFamily: "system-ui" },
  card:        { background: "#fff", border: "1px solid #DDD8CE", borderRadius: 12, padding: 32, width: "100%", maxWidth: 420 },
  heading:     { fontSize: 20, fontWeight: 700, color: "#2A2A2A", margin: "0 0 4px" },
  sub:         { fontSize: 13, color: "#8A8680", margin: "0 0 16px" },
  items:       { borderTop: "1px solid #DDD8CE", borderBottom: "1px solid #DDD8CE", padding: "10px 0", margin: "12px 0" },
  itemRow:     { display: "flex", justifyContent: "space-between", fontSize: 13, color: "#2A2A2A", padding: "4px 0" },
  totals:      { display: "flex", flexDirection: "column", gap: 4, marginBottom: 12 },
  totRow:      { display: "flex", justifyContent: "space-between", fontSize: 13, color: "#5A5A5A" },
  totFinal:    { fontSize: 16, fontWeight: 700, color: "#2A2A2A" },
  label:       { display: "flex", flexDirection: "column", gap: 6, fontSize: 13, fontWeight: 600, color: "#2A2A2A", margin: "16px 0" },
  input:       { padding: "10px 12px", border: "1px solid #DDD8CE", borderRadius: 6, fontSize: 14, fontWeight: 400, marginTop: 4 },
  btn:         { width: "100%", padding: 12, background: "#2C5545", color: "#fff", border: "none", borderRadius: 6, fontSize: 14, fontWeight: 600, cursor: "pointer" },
  errorBox:    { background: "#FEF2F2", border: "1px solid #FECACA", color: "#991B1B", borderRadius: 6, padding: "10px 14px", fontSize: 13, marginBottom: 12 },
  approvedBox: { background: "#F0FDF4", border: "1px solid #86EFAC", color: "#166534", borderRadius: 6, padding: "12px 14px", fontSize: 14, textAlign: "center" },
};
