/**
 * VulaImport.jsx — 📥 historical onboarding: paste/upload old WhatsApp chats or emails, Vula
 * extracts the past orders, you review + edit, then commit them into the system.
 * Backend: /v1/commerce/{tenant}/admin/import/extract + /commit.
 */
import { useState } from "react";

const VULA_API = import.meta.env.VITE_API_URL || "https://vula-group-production.up.railway.app";
const C = { surface: "#FFFFFF", border: "#DDD8CE", green: "var(--accent)", red: "#A23B2D", text: "#2A2A2A", muted: "#8A8680", alt: "#F0EDE5" };
const R = (n) => `R${(Number(n) || 0).toLocaleString("en-ZA", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;

export default function VulaImport({ tenantId }) {
  const [mode, setMode] = useState("orders");          // 'orders' | 'invoices'
  const [text, setText] = useState("");
  const [orders, setOrders] = useState(null);
  const [invoices, setInvoices] = useState(null);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState("");

  const flash = (t) => { setMsg(t); setTimeout(() => setMsg(""), 5000); };

  const onFile = async (file) => {
    if (!file) return;
    const t = await file.text();
    setText(t.slice(0, 200000));
    flash(`Loaded ${file.name}`);
  };

  const extract = async () => {
    if (!text.trim()) return flash("Paste or upload something first.");
    setBusy(true); setOrders(null); setInvoices(null); flash("Reading your history…");
    if (mode === "invoices") {
      const r = await fetch(`${VULA_API}/v1/commerce/${tenantId}/admin/import/invoices/extract`, {
        method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ text }),
      }).then(r => r.json()).catch(() => ({ invoices: [] }));
      setBusy(false); setInvoices(r.invoices || []);
      return flash(`Found ${(r.invoices || []).length} invoice(s) — review, then commit.`);
    }
    const r = await fetch(`${VULA_API}/v1/commerce/${tenantId}/admin/import/extract`, {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ text }),
    }).then(r => r.json()).catch(() => ({ orders: [] }));
    setBusy(false);
    setOrders(r.orders || []);
    flash(`Found ${(r.orders || []).length} order(s) — review below, then commit.`);
  };

  const commitInvoices = async () => {
    if (!invoices?.length) return;
    setBusy(true); flash("Registering invoices…");
    const r = await fetch(`${VULA_API}/v1/commerce/${tenantId}/admin/import/invoices/commit`, {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ invoices }),
    }).then(r => r.json()).catch(() => ({ error: "network" }));
    setBusy(false);
    if (r.error) return flash(r.error);
    flash(`✓ ${r.created} invoice(s) registered${r.skipped ? `, ${r.skipped} skipped` : ""} — bank payments will now match them.`);
    setInvoices(null); setText("");
  };

  const upd = (i, patch) => setOrders(os => os.map((o, j) => j === i ? { ...o, ...patch } : o));
  const drop = (i) => setOrders(os => os.filter((_, j) => j !== i));

  const commit = async () => {
    if (!orders?.length) return;
    setBusy(true); flash("Adding to Vula…");
    const r = await fetch(`${VULA_API}/v1/commerce/${tenantId}/admin/import/commit`, {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ orders }),
    }).then(r => r.json()).catch(() => ({ error: "network" }));
    setBusy(false);
    if (r.error) return flash(r.error);
    flash(`✓ ${r.created} order(s) added${r.skipped ? `, ${r.skipped} skipped (duplicates)` : ""}.`);
    setOrders(null); setText("");
  };

  return (
    <div style={{ fontFamily: "system-ui", color: C.text, maxWidth: 820 }}>
      <h4 style={{ fontSize: 15, fontWeight: 600, margin: "2px 0 2px" }}>📥 Import your history</h4>
      <div style={{ display: "flex", gap: 6, margin: "6px 0" }}>
        {[["orders", "💬 Orders (chats/emails)"], ["invoices", "🧾 Old invoices (Xero export)"]].map(([v, l]) => (
          <button key={v} onClick={() => { setMode(v); setOrders(null); setInvoices(null); }}
            style={{ ...btn, ...(mode === v ? btnOn : {}) }}>{l}</button>
        ))}
      </div>
      <p style={{ color: C.muted, fontSize: 13, marginTop: 0 }}>
        {mode === "invoices"
          ? "Paste or upload your old invoice list (Xero: Business → Invoices → Export, or any pasted table). Vula registers your past invoice numbers so bank payments like “INVOICE 003983” match up — unpaid ones stay open, paid ones go straight to history."
          : "Paste an old WhatsApp chat or emails (or upload a WhatsApp chat export). Vula pulls out the past orders — you review and confirm before anything is added. Nothing is created until you commit."}
      </p>

      {!orders && !invoices && (
        <div style={card}>
          <textarea value={text} onChange={e => setText(e.target.value)} placeholder="Paste WhatsApp chat / emails here…"
            style={{ width: "100%", minHeight: 180, boxSizing: "border-box", padding: 10, border: `1px solid ${C.border}`, borderRadius: 8, fontFamily: "monospace", fontSize: 12.5, resize: "vertical" }} />
          <div style={{ display: "flex", alignItems: "center", gap: 10, marginTop: 8, flexWrap: "wrap" }}>
            <label style={{ ...btn, cursor: "pointer" }}>⬆ Upload {mode === "invoices" ? ".csv export" : ".txt export"}
              <input type="file" accept=".txt,.csv,text/plain,text/csv" style={{ display: "none" }} onChange={e => onFile(e.target.files[0])} />
            </label>
            <button style={{ ...btn, ...btnOn }} disabled={busy} onClick={extract}>{busy ? "Reading…" : mode === "invoices" ? "Extract invoices" : "Extract orders"}</button>
            {msg && <span style={{ fontSize: 12, color: C.green }}>{msg}</span>}
          </div>
        </div>
      )}

      {orders && (
        <>
          <div style={{ display: "flex", alignItems: "center", gap: 10, margin: "4px 0 10px", flexWrap: "wrap" }}>
            <strong style={{ fontSize: 13 }}>{orders.length} order(s) found</strong>
            <button style={btn} onClick={() => setOrders(null)}>← Back to text</button>
            <button style={{ ...btn, ...btnOn, marginLeft: "auto" }} disabled={busy || !orders.length} onClick={commit}>
              {busy ? "Adding…" : `Commit ${orders.length} order(s)`}
            </button>
            {msg && <span style={{ fontSize: 12, color: C.green, width: "100%" }}>{msg}</span>}
          </div>
          {orders.length === 0 && <div style={{ color: C.muted, fontSize: 13 }}>No orders detected in that text.</div>}
          {orders.map((o, i) => (
            <div key={i} style={{ ...card, marginBottom: 8 }}>
              <div style={{ display: "flex", gap: 8, flexWrap: "wrap", alignItems: "center" }}>
                <input value={o.customer_name || ""} onChange={e => upd(i, { customer_name: e.target.value })} placeholder="Customer" style={{ ...input, flex: 1, minWidth: 120 }} />
                <input value={o.customer_phone || ""} onChange={e => upd(i, { customer_phone: e.target.value })} placeholder="Phone" style={{ ...input, width: 120 }} />
                <input type="date" value={o.date || ""} onChange={e => upd(i, { date: e.target.value })} style={input} />
                <label style={chk}><input type="checkbox" checked={!!o.paid} onChange={e => upd(i, { paid: e.target.checked })} /> paid</label>
                <label style={chk}><input type="checkbox" checked={!!o.delivered} onChange={e => upd(i, { delivered: e.target.checked })} /> delivered</label>
                <button style={{ ...miniBtn, color: C.red, marginLeft: "auto" }} onClick={() => drop(i)}>Remove</button>
              </div>
              <div style={{ fontSize: 12, color: C.muted, marginTop: 6 }}>
                {(o.items || []).map((it, k) => `${it.quantity || 1}× ${it.name}${it.unit_price_rands ? ` @ ${R(it.unit_price_rands)}` : ""}`).join(", ") || "—"}
                {o.total_rands != null && <strong style={{ color: C.text }}> · {R(o.total_rands)}</strong>}
                {o.delivery_address && ` · ${o.delivery_address}`}
              </div>
            </div>
          ))}
        </>
      )}

      {invoices && (
        <>
          <div style={{ display: "flex", alignItems: "center", gap: 10, margin: "4px 0 10px", flexWrap: "wrap" }}>
            <strong style={{ fontSize: 13 }}>{invoices.length} invoice(s) found</strong>
            <button style={btn} onClick={() => setInvoices(null)}>← Back</button>
            <button style={{ ...btn, ...btnOn, marginLeft: "auto" }} disabled={busy || !invoices.length} onClick={commitInvoices}>
              {busy ? "Registering…" : `Register ${invoices.length} invoice(s)`}
            </button>
            {msg && <span style={{ fontSize: 12, color: C.green, width: "100%" }}>{msg}</span>}
          </div>
          {invoices.length === 0 && <div style={{ color: C.muted, fontSize: 13 }}>No invoices detected in that text.</div>}
          <div style={{ ...card, padding: 0, overflowX: "auto" }}>
            <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 12.5 }}>
              <thead><tr style={{ textAlign: "left", color: C.muted, background: C.alt }}>
                {["Invoice #", "Customer", "Date", "Total", "Status", ""].map(h => <th key={h} style={{ padding: "8px 10px", fontWeight: 600 }}>{h}</th>)}
              </tr></thead>
              <tbody>
                {invoices.map((v, i) => (
                  <tr key={i} style={{ borderTop: `1px solid ${C.border}` }}>
                    <td style={{ padding: "7px 10px", fontWeight: 600 }}>{v.invoice_number}</td>
                    <td style={{ padding: "7px 10px" }}>{v.customer_name || "—"}</td>
                    <td style={{ padding: "7px 10px" }}>{v.date || "—"}</td>
                    <td style={{ padding: "7px 10px" }}>{R(v.total_rands)}</td>
                    <td style={{ padding: "7px 10px", color: v.status === "paid" ? C.green : C.red, fontWeight: 600 }}>{v.status || "unpaid"}</td>
                    <td style={{ padding: "7px 10px" }}><button style={miniBtn} onClick={() => setInvoices(vs => vs.filter((_, j) => j !== i))}>Remove</button></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}
    </div>
  );
}

const card = { background: C.surface, border: `1px solid ${C.border}`, borderRadius: 10, padding: 14 };
const btn = { padding: "8px 14px", border: `1px solid ${C.border}`, borderRadius: 6, background: C.surface, color: C.text, fontSize: 13, cursor: "pointer" };
const btnOn = { background: C.green, color: "#fff", borderColor: C.green };
const miniBtn = { padding: "4px 10px", border: `1px solid ${C.border}`, borderRadius: 5, background: C.surface, color: C.text, fontSize: 12, cursor: "pointer" };
const input = { padding: "6px 9px", border: `1px solid ${C.border}`, borderRadius: 6, fontSize: 12.5, background: C.surface, color: C.text, fontFamily: "system-ui" };
const chk = { fontSize: 12, color: C.muted, display: "flex", alignItems: "center", gap: 3 };
