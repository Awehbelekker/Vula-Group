/**
 * VulaQSRates.jsx — the tenant's own Quantity Surveying rate library.
 *
 * DIGG's owned rates (walling, acoustic panels, finishes, fees). The assistant's
 * calculations skill computes costs from THESE rates (via lookup_rate), never from
 * market figures it would guess.
 *
 * 2026-09-28: plus "Learned from your documents" — every priced line on the tenant's filed
 * invoices, quotes and BOQs and its casual workers' day rates, rolled up per item
 * (vula/commerce/price_book.py). Own rates always win; when the documents have moved more
 * than 10% from an own rate, it's flagged with an "Update" button — nothing changes by itself.
 */
import { useState, useEffect, useCallback } from "react";
import { VULA_API } from "../lib/authFetch";

const C = { surface: "var(--surface)", border: "var(--border)", green: "var(--accent)", red: "var(--danger)", amber: "var(--warn)",
  text: "var(--text)", muted: "var(--muted)", surfaceAlt: "var(--surface-alt)" };
const inp = { padding: "8px 10px", border: `1px solid ${C.border}`, borderRadius: 6, fontSize: 13, color: C.text, background: C.surface, boxSizing: "border-box" };
const btn = { padding: "8px 14px", background: C.green, color: "var(--on-accent)", border: "none", borderRadius: 6, fontSize: 13, fontWeight: 600, cursor: "pointer" };
const link = { background: "none", border: "none", cursor: "pointer", fontSize: 12, padding: 0 };
const UNITS = ["m2", "m3", "m", "each", "no", "kg", "item", "sum", "day", "hour", "bag", "litre"];
const KINDS = [["", "All"], ["material", "Materials"], ["labour", "Labour"], ["plant", "Plant hire"], ["delivery", "Delivery"]];
const blank = { id: null, description: "", unit: "m2", rate: "", code: "", category: "", source: "" };
const zar = (n) => "R " + Number(n || 0).toLocaleString("en-ZA", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
const cents = (c) => zar((c || 0) / 100);

export default function VulaQSRates({ tenantId }) {
  const [own, setOwn] = useState([]);
  const [learned, setLearned] = useState([]);
  const [q, setQ] = useState("");
  const [kind, setKind] = useState("");
  const [form, setForm] = useState(blank);
  const [busy, setBusy] = useState(false);
  const [showAll, setShowAll] = useState(false);

  const load = useCallback(async () => {
    if (!tenantId) return;
    const params = new URLSearchParams();
    if (q) params.set("q", q);
    if (kind) params.set("kind", kind);
    try {
      const r = await fetch(`${VULA_API}/v1/qs/rates/${tenantId}?${params}`);
      const d = await r.json();
      setOwn(d.own || d.rates || []);
      setLearned(d.learned || []);
    } catch { setOwn([]); setLearned([]); }
  }, [tenantId, q, kind]);
  useEffect(() => { load(); }, [load]);

  const post = async (body) => {
    setBusy(true);
    try {
      await fetch(`${VULA_API}/v1/qs/rates/${tenantId}`, {
        method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
      });
    } finally { setBusy(false); load(); }
  };
  const save = async () => {
    if (!form.description.trim() || form.rate === "") return;
    const { id, ...rest } = form;
    await post({ ...rest, ...(id ? { id } : {}), rate: parseFloat(form.rate) });
    setForm(blank);
  };
  const edit = (r) => setForm({ id: r.id, description: r.description || "", unit: r.unit || "m2", rate: r.rate ?? "",
    code: r.code || "", category: r.category || "", source: r.source || "" });
  const del = async (id) => { await fetch(`${VULA_API}/v1/qs/rates/${tenantId}/${id}`, { method: "DELETE" }); load(); };
  const adopt = (r) => post({ description: r.description, unit: r.unit || "each", rate: r.rate,
    category: r.kind === "material" ? "" : r.kind, source: r.source });
  const updateToLearned = (r) => post({ id: r.id, description: r.description, unit: r.unit, rate: r.drift.learned_rate,
    source: r.drift.source });

  const shown = showAll ? learned : learned.slice(0, 60);

  return (
    <div style={{ maxWidth: 1040, margin: "0 auto", padding: "24px 16px" }}>
      <h1 style={{ fontFamily: "var(--font-display)", fontSize: 28, fontWeight: 700, color: C.text, margin: "0 0 4px" }}>QS Rate Library</h1>
      <p style={{ fontSize: 13, color: C.muted, margin: "0 0 18px" }}>Your own unit rates come first. Below them, rates learned from your own invoices, quotes, BOQs and labour payments. The assistant, Quick Cost, QS Pro and Takeoff use both, and never guess a market rate.</p>

      <div style={{ display: "flex", gap: 8, flexWrap: "wrap", marginBottom: 14 }}>
        <input style={{ ...inp, flex: "1 1 240px" }} placeholder="Search rates… e.g. board, tiling labour, cornice" value={q} onChange={(e) => setQ(e.target.value)} />
        <select style={inp} value={kind} onChange={(e) => setKind(e.target.value)}>{KINDS.map(([v, l]) => <option key={v} value={v}>{l}</option>)}</select>
      </div>

      {/* Add / edit a rate */}
      <div style={{ background: C.surface, border: `1px solid ${C.border}`, borderRadius: 10, padding: 14, marginBottom: 16 }}>
        <div style={{ fontSize: 12, fontWeight: 700, color: C.muted, marginBottom: 8 }}>{form.id ? "EDIT RATE" : "ADD A RATE"}</div>
        <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(110px, 1fr))", gap: 6, marginBottom: 6 }}>
          <input style={{ ...inp, gridColumn: "span 2" }} placeholder="Description" value={form.description} onChange={(e) => setForm({ ...form, description: e.target.value })} />
          <select style={inp} value={form.unit} onChange={(e) => setForm({ ...form, unit: e.target.value })}>{[...new Set([...UNITS, form.unit])].map(u => <option key={u}>{u}</option>)}</select>
          <input style={inp} type="number" placeholder="Rate (ZAR)" value={form.rate} onChange={(e) => setForm({ ...form, rate: e.target.value })} />
          <input style={inp} placeholder="Category" value={form.category} onChange={(e) => setForm({ ...form, category: e.target.value })} />
          <input style={inp} placeholder="Source e.g. DIGG 2026" value={form.source} onChange={(e) => setForm({ ...form, source: e.target.value })} />
        </div>
        <button style={{ ...btn, opacity: busy ? 0.6 : 1 }} disabled={busy} onClick={save}>{form.id ? "Save changes" : "Save rate"}</button>
        {form.id && <button style={{ ...link, color: C.muted, marginLeft: 12 }} onClick={() => setForm(blank)}>Cancel</button>}
      </div>

      {/* Own rates */}
      <div style={{ background: C.surface, border: `1px solid ${C.border}`, borderRadius: 10, overflow: "hidden", marginBottom: 18 }}>
        <div style={{ padding: "10px 14px", borderBottom: `1px solid ${C.border}`, fontSize: 13, fontWeight: 600 }}>
          Your rates <span style={{ color: C.muted }}>· {own.length}</span>
        </div>
        {own.length === 0 && <div style={{ padding: 18, fontSize: 13, color: C.muted }}>No rates of your own yet. Add one above, or tap "Add to my rates" on a learned rate below.</div>}
        {own.map((r) => (
          <div key={r.id || r.description} style={{ padding: "10px 14px", borderBottom: `1px solid ${C.surfaceAlt}`, fontSize: 13 }}>
            <div style={{ display: "flex", flexWrap: "wrap", gap: 10, alignItems: "baseline" }}>
              <span style={{ color: C.text, flex: "1 1 220px" }}>{r.description}{r.code ? <span style={{ color: C.muted }}> · {r.code}</span> : null}</span>
              <span style={{ color: C.muted, minWidth: 40 }}>{r.unit}</span>
              <span style={{ color: C.text, fontWeight: 600, minWidth: 100 }}>{zar(r.rate)}</span>
              <span style={{ color: C.muted, fontSize: 11, minWidth: 90 }}>{r.source || ""}</span>
              {r.id && <button style={{ ...link, color: C.green }} onClick={() => edit(r)}>Edit</button>}
              {r.id && <button style={{ ...link, color: C.red }} onClick={() => del(r.id)}>Delete</button>}
            </div>
            {r.drift && (
              <div style={{ marginTop: 6, fontSize: 12, color: C.amber }}>
                Your documents say {zar(r.drift.learned_rate)} ({r.drift.change_pct > 0 ? "+" : ""}{r.drift.change_pct}%) · {r.drift.source}
                {r.id && <button style={{ ...link, color: C.green, marginLeft: 10 }} disabled={busy} onClick={() => updateToLearned(r)}>Update my rate</button>}
              </div>
            )}
          </div>
        ))}
      </div>

      {/* Learned rates */}
      <div style={{ background: C.surface, border: `1px solid ${C.border}`, borderRadius: 10, overflow: "hidden" }}>
        <div style={{ padding: "10px 14px", borderBottom: `1px solid ${C.border}`, fontSize: 13, fontWeight: 600 }}>
          Learned from your documents <span style={{ color: C.muted }}>· {learned.length}</span>
          <div style={{ fontSize: 11, fontWeight: 400, color: C.muted, marginTop: 2 }}>Rate = the median of what you paid (invoices, labour). Where nothing was paid yet, the median of what was quoted or priced in a BOQ.</div>
        </div>
        {learned.length === 0 && <div style={{ padding: 18, fontSize: 13, color: C.muted }}>Nothing learned yet. Documents › "Learn from history" reads your filed invoices, quotes and BOQs into here.</div>}
        {shown.map((r) => (
          <div key={`${r.description}-${r.unit}`} style={{ padding: "10px 14px", borderBottom: `1px solid ${C.surfaceAlt}`, fontSize: 13 }}>
            <div style={{ display: "flex", flexWrap: "wrap", gap: 10, alignItems: "baseline" }}>
              <span style={{ color: C.text, flex: "1 1 220px" }}>{r.description}{r.kind !== "material" ? <span style={{ color: C.muted }}> · {r.kind}</span> : null}</span>
              <span style={{ color: C.muted, minWidth: 40 }}>{r.unit}</span>
              <span style={{ color: C.text, fontWeight: 600, minWidth: 100 }}>{cents(r.rate_cents)}</span>
              <span style={{ color: C.muted, fontSize: 11, minWidth: 140 }}>{r.low_cents !== r.high_cents ? `${cents(r.low_cents)} – ${cents(r.high_cents)}` : ""}</span>
              <button style={{ ...link, color: C.green }} disabled={busy} onClick={() => adopt(r)}>Add to my rates</button>
            </div>
            <div style={{ fontSize: 11, color: C.muted, marginTop: 3 }}>
              {r.source}{r.latest_supplier ? ` · latest ${cents(r.latest_cents)} from ${r.latest_supplier}` : ""}{r.basis === "quoted" ? " · quoted, not yet paid" : ""}{r.projects?.length ? ` · ${r.projects.join(", ")}` : ""}
            </div>
          </div>
        ))}
        {!showAll && learned.length > shown.length && (
          <button style={{ ...link, color: C.green, padding: 14 }} onClick={() => setShowAll(true)}>Show all {learned.length}</button>
        )}
      </div>
      <p style={{ textAlign: "center", fontSize: 11, color: "var(--faint)", marginTop: 22 }}>Powered by Vula</p>
    </div>
  );
}
