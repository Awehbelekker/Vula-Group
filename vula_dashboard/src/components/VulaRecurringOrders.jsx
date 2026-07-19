/**
 * VulaRecurringOrders.jsx — customer product subscriptions ("fish every Friday").
 * Standing orders that auto-create a real order each cycle. Backend: /v1/subscriptions/{tenant} (migration 051).
 * Distinct from Vula's own SaaS plan billing (VulaSubscriptions).
 */
import { useState, useEffect, useCallback } from "react";

const VULA_API = import.meta.env.VITE_API_URL || "https://vula-group-production.up.railway.app";
const C = { surface: "#FFFFFF", border: "#DDD8CE", green: "var(--accent)", red: "#A23B2D", text: "#2A2A2A", muted: "#8A8680", alt: "#F0EDE5" };
const CAD = { weekly: "Weekly", biweekly: "Every 2 weeks", monthly: "Monthly" };
const STATUS = { active: { l: "Active", c: "#22c55e" }, paused: { l: "Paused", c: "#f59e0b" }, cancelled: { l: "Cancelled", c: "#ef4444" } };
const R = (c) => `R${((c || 0) / 100).toFixed(2)}`;
const itemsTotal = (items) => (items || []).reduce((s, i) => s + Math.round((i.quantity || 0) * (i.unit_price_cents || 0)), 0);

export default function VulaRecurringOrders({ tenantId }) {
  const [subs, setSubs] = useState([]);
  const [products, setProducts] = useState([]);
  const [loading, setLoading] = useState(true);
  const [showNew, setShowNew] = useState(false);
  const [editingId, setEditingId] = useState(null);
  const [msg, setMsg] = useState("");

  const load = useCallback(async () => {
    setLoading(true);
    const [s, p] = await Promise.all([
      fetch(`${VULA_API}/v1/subscriptions/${tenantId}`).then(r => r.json()).catch(() => ({})),
      fetch(`${VULA_API}/v1/commerce/${tenantId}/admin/products`).then(r => r.json()).catch(() => ({})),
    ]);
    setSubs(s.subscriptions || []);
    setProducts(p.products || []);
    setLoading(false);
  }, [tenantId]);
  useEffect(() => { load(); }, [load]);

  const flash = (t) => { setMsg(t); setTimeout(() => setMsg(""), 3000); };

  const setStatus = async (id, status) => {
    await fetch(`${VULA_API}/v1/subscriptions/${tenantId}/${id}/status`, {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ status }),
    });
    load();
  };

  const runDue = async () => {
    const r = await fetch(`${VULA_API}/v1/subscriptions/${tenantId}/jobs/run`, { method: "POST" })
      .then(r => r.json()).catch(() => ({}));
    flash(`Created ${r.created ?? 0} due order(s)`);
    load();
  };

  return (
    <div style={{ fontFamily: "system-ui", color: C.text }}>
      <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 6, flexWrap: "wrap" }}>
        <h4 style={{ fontSize: 15, fontWeight: 600, margin: 0 }}>🔁 Recurring orders</h4>
        <div style={{ marginLeft: "auto", display: "flex", gap: 8 }}>
          <button style={btn} onClick={runDue} title="Create any orders due today now">Run due now</button>
          <button style={{ ...btn, ...btnOn }} onClick={() => setShowNew(v => !v)}>{showNew ? "Close" : "+ New"}</button>
        </div>
        {msg && <span style={{ color: C.green, fontSize: 13, width: "100%" }}>{msg}</span>}
      </div>
      <p style={{ color: C.muted, fontSize: 13, marginTop: 0 }}>
        Standing orders auto-create a real order each cycle and message the customer. They can reply STOP to pause.
      </p>

      {showNew && <NewSub tenantId={tenantId} products={products} onDone={() => { setShowNew(false); load(); flash("Subscription created ✓"); }} flash={flash} />}

      {loading ? <div style={{ color: C.muted, fontSize: 13 }}>Loading…</div>
        : subs.length === 0 ? <div style={{ color: C.muted, fontSize: 13 }}>No recurring orders yet.</div>
          : subs.map(s => {
            const st = STATUS[s.status] || { l: s.status, c: C.muted };
            const total = itemsTotal(s.items) + (s.delivery_cents || 0);
            if (editingId === s.id) {
              return (
                <NewSub key={s.id} tenantId={tenantId} products={products} sub={s} flash={flash}
                  onDone={() => { setEditingId(null); load(); flash("Subscription updated ✓"); }}
                  onCancel={() => setEditingId(null)} />
              );
            }
            return (
              <div key={s.id} style={card}>
                <div style={{ flex: 1 }}>
                  <div style={{ fontWeight: 600 }}>
                    {s.customer_name || s.customer_phone || "Customer"} · {CAD[s.cadence] || s.cadence}
                  </div>
                  <div style={{ fontSize: 13, color: C.muted }}>
                    {(s.items || []).map(i => `${i.quantity}× ${i.product_name}`).join(", ") || "—"}
                  </div>
                  <div style={{ fontSize: 12, color: C.muted, marginTop: 2 }}>
                    {R(total)} · next {s.next_run}{s.last_run ? ` · last ${String(s.last_run).slice(0, 10)}` : ""}
                  </div>
                </div>
                <span style={{ ...pill, color: st.c, borderColor: st.c }}>{st.l}</span>
                {s.status !== "cancelled" && (
                  <div style={{ display: "flex", gap: 4, marginLeft: 8 }}>
                    <button style={miniBtn} onClick={() => setEditingId(s.id)}>Edit</button>
                    {s.status === "active"
                      ? <button style={miniBtn} onClick={() => setStatus(s.id, "paused")}>Pause</button>
                      : <button style={miniBtn} onClick={() => setStatus(s.id, "active")}>Resume</button>}
                    <button style={{ ...miniBtn, color: C.red }} onClick={() => setStatus(s.id, "cancelled")}>Cancel</button>
                  </div>
                )}
              </div>
            );
          })}
    </div>
  );
}

function NewSub({ tenantId, products, onDone, flash, sub, onCancel }) {
  const editing = !!sub;
  const [f, setF] = useState(editing
    ? { customer_name: sub.customer_name || "", customer_phone: sub.customer_phone || "", cadence: sub.cadence || "weekly", payment_method: sub.payment_method || "cod" }
    : { customer_name: "", customer_phone: "", cadence: "weekly", payment_method: "cod" });
  const [lines, setLines] = useState(editing && sub.items?.length
    ? sub.items.map(i => ({ product_id: i.product_id, quantity: i.quantity }))
    : [{ product_id: "", quantity: 1 }]);
  const [saving, setSaving] = useState(false);

  const setLine = (i, patch) => setLines(ls => ls.map((l, j) => j === i ? { ...l, ...patch } : l));
  const addLine = () => setLines(ls => [...ls, { product_id: "", quantity: 1 }]);
  const removeLine = (i) => setLines(ls => ls.filter((_, j) => j !== i));

  const save = async () => {
    const items = lines.filter(l => l.product_id).map(l => {
      const p = products.find(p => p.id === l.product_id);
      return { product_id: l.product_id, product_name: p?.name || "", quantity: +l.quantity || 1, unit_price_cents: p?.price_cents || 0 };
    });
    if (!items.length) return flash("Add at least one product");
    if (!editing && !f.customer_phone.trim()) return flash("Customer phone is required");
    setSaving(true);
    const url = editing ? `${VULA_API}/v1/subscriptions/${tenantId}/${sub.id}` : `${VULA_API}/v1/subscriptions/${tenantId}`;
    const r = await fetch(url, {
      method: editing ? "PATCH" : "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify(editing ? { items, cadence: f.cadence, payment_method: f.payment_method, customer_name: f.customer_name } : { ...f, items }),
    }).then(r => r.json()).catch(() => ({ error: "network" }));
    setSaving(false);
    if (r.error) return flash(r.error);
    onDone();
  };

  return (
    <div style={{ ...card, flexDirection: "column", alignItems: "stretch", gap: 10, background: C.alt }}>
      <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
        <input placeholder="Customer name" value={f.customer_name} onChange={e => setF({ ...f, customer_name: e.target.value })} style={input} />
        {!editing && <input placeholder="Phone (27…)" value={f.customer_phone} onChange={e => setF({ ...f, customer_phone: e.target.value })} style={input} />}
        <select value={f.cadence} onChange={e => setF({ ...f, cadence: e.target.value })} style={input}>
          {Object.entries(CAD).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
        </select>
        <select value={f.payment_method} onChange={e => setF({ ...f, payment_method: e.target.value })} style={input}>
          <option value="cod">Pay on delivery</option>
          <option value="eft">EFT</option>
          <option value="online">Card / online</option>
        </select>
      </div>
      {lines.map((l, i) => (
        <div key={i} style={{ display: "flex", gap: 8 }}>
          <select value={l.product_id} onChange={e => setLine(i, { product_id: e.target.value })} style={{ ...input, flex: 1 }}>
            <option value="">Product…</option>
            {products.map(p => <option key={p.id} value={p.id}>{p.name} ({R(p.price_cents)})</option>)}
          </select>
          <input type="number" min="0.5" step="0.5" value={l.quantity} onChange={e => setLine(i, { quantity: e.target.value })} style={{ ...input, width: 80 }} />
          {lines.length > 1 && <button style={miniBtn} onClick={() => removeLine(i)}>✕</button>}
        </div>
      ))}
      <div>
        <button style={miniBtn} onClick={addLine}>+ item</button>
      </div>
      <div style={{ display: "flex", gap: 8 }}>
        <button style={{ ...btn, ...btnOn, alignSelf: "flex-start" }} disabled={saving} onClick={save}>
          {saving ? "Saving…" : editing ? "Save changes" : "Create subscription"}
        </button>
        {editing && <button style={btn} onClick={onCancel}>Cancel</button>}
      </div>
    </div>
  );
}

const btn = { padding: "7px 14px", border: `1px solid ${C.border}`, borderRadius: 6, background: C.surface, color: C.text, fontSize: 13, cursor: "pointer" };
const btnOn = { background: C.green, color: "#fff", borderColor: C.green };
const miniBtn = { padding: "4px 10px", border: `1px solid ${C.border}`, borderRadius: 5, background: C.surface, color: C.text, fontSize: 12, cursor: "pointer" };
const input = { padding: "7px 10px", border: `1px solid ${C.border}`, borderRadius: 6, fontSize: 13, background: C.surface, color: C.text };
const card = { display: "flex", alignItems: "center", gap: 10, padding: "12px 14px", border: `1px solid ${C.border}`, borderRadius: 8, background: C.surface, marginBottom: 8 };
const pill = { fontSize: 11, padding: "2px 8px", borderRadius: 10, border: "1px solid", fontWeight: 600 };
