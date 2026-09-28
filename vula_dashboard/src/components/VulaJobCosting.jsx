/**
 * VulaJobCosting.jsx — is each project making its fee? From the bank.
 *
 * 2026-09-28 (DIGG): per project, money received vs cost by trade, the cost-plus fee it should
 * earn (fee % editable per project, business default 10%), its share of the business's running
 * costs, and profit. Plus a "what should I charge" box (/admin/projects/price-advice) that
 * starts from what the business actually paid. Figures come from vula/commerce/job_costing.py.
 */
import { useState, useEffect, useCallback } from "react";
import { VULA_API } from "../lib/authFetch";

const C = { surface: "#FFFFFF", border: "#DDD8CE", green: "var(--accent)", red: "#A23B2D", amber: "#B7791F",
  text: "#2A2A2A", muted: "#8A8680", alt: "#F0EDE5" };
const R = (c) => "R" + ((Number(c) || 0) / 100).toLocaleString("en-ZA", { maximumFractionDigits: 0 });
const STATUS = { "loss": C.red, "below target": C.amber, "on track": "#2E7D32" };
const inp = { padding: "7px 9px", border: `1px solid ${C.border}`, borderRadius: 6, fontSize: 13, boxSizing: "border-box" };

function Stat({ label, value, color, sub }) {
  return (
    <div style={{ background: C.alt, borderRadius: 8, padding: "8px 10px", minWidth: 0 }}>
      <div style={{ fontSize: 10, textTransform: "uppercase", color: C.muted }}>{label}</div>
      <div style={{ fontSize: 15, fontWeight: 700, color: color || C.text }}>{value}</div>
      {sub && <div style={{ fontSize: 10, color: C.muted }}>{sub}</div>}
    </div>
  );
}

export default function VulaJobCosting({ tenantId }) {
  const [data, setData] = useState(null);
  const [open, setOpen] = useState(null);
  const [feeEdit, setFeeEdit] = useState({});
  const [ask, setAsk] = useState({ item: "", quantity: "", unit: "" });
  const [advice, setAdvice] = useState(null);

  const [hasProjects, setHasProjects] = useState(null);
  const base = `${VULA_API}/v1/commerce/${tenantId}/admin/projects`;
  useEffect(() => {
    if (!tenantId) return;
    fetch(`${VULA_API}/v1/tenants/${tenantId}`).then(r => r.json())
      .then(d => setHasProjects((d.modules || d.tenant?.modules || []).includes("projects")))
      .catch(() => setHasProjects(false));
  }, [tenantId]);
  const load = useCallback(async () => {
    if (!tenantId) return;
    try { setData(await (await fetch(`${base}/costing`)).json()); } catch { setData(null); }
  }, [tenantId, base]);
  useEffect(() => { load(); }, [load]);

  const saveFee = async (project) => {
    const v = parseFloat(feeEdit[project]);
    if (Number.isNaN(v)) return;
    await fetch(`${base}/terms`, { method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ project, fee_pct: v }) });
    setFeeEdit({ ...feeEdit, [project]: undefined });
    load();
  };
  const getAdvice = async () => {
    if (!ask.item.trim()) return;
    const q = new URLSearchParams({ item: ask.item });
    if (ask.quantity) q.set("quantity", ask.quantity);
    if (ask.unit) q.set("unit", ask.unit);
    try { setAdvice(await (await fetch(`${base}/price-advice?${q}`)).json()); } catch { setAdvice(null); }
  };

  if (!data || !hasProjects) return null;   // job costing is for project businesses (DIGG)
  const projects = data.projects || [];

  return (
    <div style={{ background: C.surface, border: `1px solid ${C.border}`, borderRadius: 10, padding: 16, marginBottom: 20 }}>
      <h3 style={{ margin: "0 0 4px", fontSize: 15, fontWeight: 700, color: C.text }}>📊 Job costing — is each project making its fee?</h3>
      <p style={{ fontSize: 12, color: C.muted, margin: "0 0 12px" }}>
        From the bank: money received vs cost on each project, the cost-plus fee it should earn (default {data.fee_default_pct}%), and its share of running costs. Received counts what's in the bank so far.
      </p>

      {projects.length === 0 && (
        <div style={{ fontSize: 13, color: C.muted, marginBottom: 12 }}>
          No bank lines are allocated to projects yet. In Bank, import a categorised statement sheet or set a project on each line.
        </div>)}

      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(150px, 1fr))", gap: 8, marginBottom: 14 }}>
        <Stat label="Fees earned" value={R(data.fees_earned_cents)} sub={`target ${R(data.fees_target_cents)}`}
              color={data.fees_earned_cents < data.fees_target_cents ? C.red : undefined} />
        <Stat label="Running costs" value={R(data.overheads_cents)}
              sub={data.overhead_rate_pct != null ? `${data.overhead_rate_pct}% of project spend` : ""} />
        <Stat label="Business result" value={R(data.business_result_cents)} sub="fees earned − running costs"
              color={data.business_result_cents < 0 ? C.red : undefined} />
        {data.unallocated_project_spend_cents > 0 &&
          <Stat label="Not on a project yet" value={R(data.unallocated_project_spend_cents)}
                sub={`${data.unallocated_project_lines} materials/labour lines — allocate in Bank`} color={C.amber} />}
      </div>

      {projects.map((p) => (
        <div key={p.project} style={{ border: `1px solid ${C.border}`, borderRadius: 8, padding: 12, marginBottom: 10 }}>
          <div style={{ display: "flex", flexWrap: "wrap", gap: 8, alignItems: "baseline", marginBottom: 8 }}>
            <b style={{ fontSize: 14, color: C.text, flex: "1 1 160px" }}>{p.project}</b>
            <span style={{ fontSize: 12, fontWeight: 700, color: STATUS[p.status] }}>{p.status}</span>
            <span style={{ fontSize: 11, color: C.muted }}>{p.first} – {p.last}</span>
          </div>
          <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(120px, 1fr))", gap: 6 }}>
            <Stat label="Received" value={R(p.received_cents)} />
            <Stat label="Cost" value={R(p.cost_cents)} />
            <Stat label={`Should bring in (+${p.fee_pct}%)`} value={R(p.target_received_cents)} />
            <Stat label="Fee earned" value={R(p.fee_earned_cents)} sub={`of ${R(p.fee_target_cents)}`}
                  color={p.fee_earned_cents < p.fee_target_cents ? C.red : undefined} />
            <Stat label="Overhead share" value={R(p.overhead_share_cents)} />
            <Stat label="Profit" value={R(p.profit_cents)} color={p.profit_cents < 0 ? C.red : "#2E7D32"} />
          </div>
          <div style={{ display: "flex", flexWrap: "wrap", gap: 10, alignItems: "center", marginTop: 8, fontSize: 12 }}>
            <button onClick={() => setOpen(open === p.project ? null : p.project)}
                    style={{ background: "none", border: "none", color: C.green, cursor: "pointer", padding: 0, fontSize: 12 }}>
              {open === p.project ? "Hide" : "Show"} cost by trade
            </button>
            <span style={{ color: C.muted }}>Fee %</span>
            <input style={{ ...inp, width: 70 }} type="number" value={feeEdit[p.project] ?? p.fee_pct}
                   onChange={(e) => setFeeEdit({ ...feeEdit, [p.project]: e.target.value })} />
            {feeEdit[p.project] !== undefined &&
              <button onClick={() => saveFee(p.project)} style={{ ...inp, cursor: "pointer", background: C.green, color: "#fff", border: "none" }}>Save</button>}
            {p.unallocated_trade_cents > 0 && <span style={{ color: C.amber }}>{R(p.unallocated_trade_cents)} not yet allocated to a trade</span>}
            {p.variations && <span style={{ color: C.amber }} title="Documents labelled 'Variation — over BOQ'">
              Variations over BOQ: {p.variations.documents} · claimed {R(p.variations.claimed_cents)} · extra costs {R(p.variations.extra_cost_cents)}</span>}
          </div>
          {open === p.project && (
            <div style={{ marginTop: 8 }}>
              {p.trades.map((t) => (
                <div key={t.trade} style={{ display: "flex", justifyContent: "space-between", fontSize: 12.5, padding: "4px 0", borderBottom: `1px solid ${C.alt}` }}>
                  <span style={{ color: C.text }}>{t.trade}</span>
                  <span style={{ color: C.muted }}>{R(t.cents)} · {p.cost_cents ? Math.round(100 * t.cents / p.cost_cents) : 0}%</span>
                </div>))}
            </div>)}
        </div>
      ))}

      <div style={{ borderTop: `1px solid ${C.border}`, marginTop: 14, paddingTop: 12 }}>
        <div style={{ fontSize: 13, fontWeight: 700, color: C.text, marginBottom: 6 }}>What should I charge?</div>
        <div style={{ display: "flex", flexWrap: "wrap", gap: 6 }}>
          <input style={{ ...inp, flex: "2 1 180px" }} placeholder="Item or work, e.g. ceiling board, tiling labour" value={ask.item} onChange={(e) => setAsk({ ...ask, item: e.target.value })} />
          <input style={{ ...inp, flex: "1 1 80px" }} type="number" placeholder="Qty" value={ask.quantity} onChange={(e) => setAsk({ ...ask, quantity: e.target.value })} />
          <input style={{ ...inp, flex: "1 1 70px" }} placeholder="Unit (m2…)" value={ask.unit} onChange={(e) => setAsk({ ...ask, unit: e.target.value })} />
          <button onClick={getAdvice} style={{ ...inp, cursor: "pointer", background: C.green, color: "#fff", border: "none" }}>Price it</button>
        </div>
        {advice && <div style={{ fontSize: 12.5, color: C.text, marginTop: 8, lineHeight: 1.5 }}>{advice.text || advice.message}</div>}
      </div>
    </div>
  );
}
