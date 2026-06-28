/**
 * VulaQSRates.jsx — the tenant's own Quantity Surveying rate library.
 *
 * DIGG's owned rates (walling, acoustic panels, finishes, fees). The assistant's
 * calculations skill computes costs from THESE rates (via lookup_rate), never from
 * market figures it would guess.
 */
import { useState, useEffect, useCallback } from "react";

const VULA_API = import.meta.env.VITE_API_URL || "https://vula-group-production.up.railway.app";
const C = { surface: "#FFFFFF", border: "#DDD8CE", green: "var(--accent)", red: "#C0392B",
  text: "#2A2A2A", muted: "#8A8680", surfaceAlt: "#F0EDE5" };
const inp = { padding: "8px 10px", border: `1px solid ${C.border}`, borderRadius: 6, fontSize: 13, color: C.text, background: C.surface, boxSizing: "border-box" };
const btn = { padding: "8px 14px", background: C.green, color: "#fff", border: "none", borderRadius: 6, fontSize: 13, fontWeight: 600, cursor: "pointer" };
const UNITS = ["m2", "m3", "m", "no", "kg", "item", "sum"];
const blank = { description: "", unit: "m2", rate: "", code: "", category: "", source: "" };

export default function VulaQSRates({ tenantId }) {
  const [rates, setRates] = useState([]);
  const [q, setQ] = useState("");
  const [form, setForm] = useState(blank);
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    if (!tenantId) return;
    const r = await fetch(`${VULA_API}/v1/qs/rates/${tenantId}${q ? `?q=${encodeURIComponent(q)}` : ""}`);
    const d = await r.json();
    setRates(d.rates || []);
  }, [tenantId, q]);
  useEffect(() => { load(); }, [load]);

  const save = async () => {
    if (!form.description.trim() || form.rate === "") return;
    setBusy(true);
    await fetch(`${VULA_API}/v1/qs/rates/${tenantId}`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ ...form, rate: parseFloat(form.rate) }),
    });
    setForm(blank); setBusy(false); load();
  };
  const del = async (id) => { await fetch(`${VULA_API}/v1/qs/rates/${tenantId}/${id}`, { method: "DELETE" }); load(); };
  const zar = (n) => "R " + Number(n).toLocaleString("en-ZA", { minimumFractionDigits: 2, maximumFractionDigits: 2 });

  return (
    <div style={{ maxWidth: 980, margin: "0 auto", padding: 24 }}>
      <h1 style={{ fontFamily: "'Cormorant Garamond', serif", fontSize: 28, fontWeight: 700, color: C.text, margin: "0 0 4px" }}>QS Rate Library</h1>
      <p style={{ fontSize: 13, color: C.muted, margin: "0 0 18px" }}>Your own unit rates. The assistant uses these to compute costs — it won't guess market rates.</p>

      <input style={{ ...inp, width: "100%", marginBottom: 14 }} placeholder="Search rates…" value={q} onChange={(e) => setQ(e.target.value)} />

      {/* Add a rate */}
      <div style={{ background: C.surface, border: `1px solid ${C.border}`, borderRadius: 10, padding: 14, marginBottom: 16 }}>
        <div style={{ fontSize: 12, fontWeight: 700, color: C.muted, marginBottom: 8 }}>ADD / UPDATE A RATE</div>
        <div style={{ display: "grid", gridTemplateColumns: "2fr 80px 110px 1fr 1fr", gap: 6, marginBottom: 6 }}>
          <input style={inp} placeholder="Description" value={form.description} onChange={(e) => setForm({ ...form, description: e.target.value })} />
          <select style={inp} value={form.unit} onChange={(e) => setForm({ ...form, unit: e.target.value })}>{UNITS.map(u => <option key={u}>{u}</option>)}</select>
          <input style={inp} type="number" placeholder="Rate (ZAR)" value={form.rate} onChange={(e) => setForm({ ...form, rate: e.target.value })} />
          <input style={inp} placeholder="Category" value={form.category} onChange={(e) => setForm({ ...form, category: e.target.value })} />
          <input style={inp} placeholder="Source e.g. DIGG 2024" value={form.source} onChange={(e) => setForm({ ...form, source: e.target.value })} />
        </div>
        <button style={{ ...btn, opacity: busy ? 0.6 : 1 }} disabled={busy} onClick={save}>Save rate</button>
      </div>

      {/* Rates table */}
      <div style={{ background: C.surface, border: `1px solid ${C.border}`, borderRadius: 10, overflow: "hidden" }}>
        <div style={{ padding: "10px 14px", borderBottom: `1px solid ${C.border}`, fontSize: 13, fontWeight: 600 }}>
          Rates <span style={{ color: C.muted }}>· {rates.length}</span>
        </div>
        {rates.length === 0 && <div style={{ padding: 18, fontSize: 13, color: C.muted }}>No rates yet. Add your own above (e.g. breeze-block walling, acoustic panels, fees).</div>}
        {rates.map((r) => (
          <div key={r.id || r.description} style={{ padding: "10px 14px", borderBottom: `1px solid ${C.surfaceAlt}`, display: "grid", gridTemplateColumns: "2fr 60px 120px 1fr 100px 60px", gap: 8, alignItems: "center", fontSize: 13 }}>
            <span style={{ color: C.text }}>{r.description}{r.code ? <span style={{ color: C.muted }}> · {r.code}</span> : null}</span>
            <span style={{ color: C.muted }}>{r.unit}</span>
            <span style={{ color: C.text, fontWeight: 600 }}>{zar(r.rate)}</span>
            <span style={{ color: C.muted }}>{r.category || "—"}</span>
            <span style={{ color: C.muted, fontSize: 11 }}>{r.source || ""}</span>
            {r.id && <button onClick={() => del(r.id)} style={{ background: "none", border: "none", color: C.red, cursor: "pointer", fontSize: 12 }}>Delete</button>}
          </div>
        ))}
      </div>
      <p style={{ textAlign: "center", fontSize: 11, color: "#B5B0A8", marginTop: 22 }}>Powered by Vula</p>
    </div>
  );
}
