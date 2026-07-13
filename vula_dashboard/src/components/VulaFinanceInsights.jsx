/**
 * VulaFinanceInsights.jsx — commerce P&L snapshot: revenue, expenses, gross profit,
 * VAT collected, receivables, top customers — plus a plain-language "Ask Vula" read.
 * Backend: /v1/commerce/{tenant}/admin/finances/insights
 */
import { useState, useEffect, useCallback } from "react";

const VULA_API = import.meta.env.VITE_API_URL || "https://vula-group-production.up.railway.app";
const C = { surface: "#FFFFFF", border: "#DDD8CE", green: "var(--accent)", red: "#A23B2D", text: "#2A2A2A", muted: "#8A8680", alt: "#F0EDE5" };
const R = (c) => `R${((c || 0) / 100).toLocaleString("en-ZA", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
const PERIODS = [{ d: 7, l: "7 days" }, { d: 30, l: "30 days" }, { d: 90, l: "90 days" }];

export default function VulaFinanceInsights({ tenantId }) {
  const [days, setDays] = useState(30);
  const [d, setD] = useState(null);
  const [loading, setLoading] = useState(true);
  const [summary, setSummary] = useState("");
  const [asking, setAsking] = useState(false);

  const load = useCallback(async () => {
    setLoading(true); setSummary("");
    const r = await fetch(`${VULA_API}/v1/commerce/${tenantId}/admin/finances/insights?days=${days}`)
      .then(r => r.json()).catch(() => null);
    setD(r); setLoading(false);
  }, [tenantId, days]);

  useEffect(() => { load(); }, [load]);

  const askVula = async () => {
    setAsking(true);
    const r = await fetch(`${VULA_API}/v1/commerce/${tenantId}/admin/finances/insights?days=${days}&explain=true`)
      .then(r => r.json()).catch(() => null);
    setSummary(r?.summary || "Couldn't generate a summary right now.");
    setAsking(false);
  };

  if (loading) return <div style={{ color: C.muted, fontSize: 13, padding: 12 }}>Crunching the numbers…</div>;
  if (!d || d.error) return <div style={{ color: C.muted, fontSize: 13, padding: 12 }}>No financial data yet.</div>;

  const profit = d.gross_profit_cents;
  const profitColor = profit >= 0 ? C.green : C.red;

  return (
    <div style={{ fontFamily: "system-ui", color: C.text }}>
      <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 14, flexWrap: "wrap" }}>
        <h4 style={{ fontSize: 15, fontWeight: 600, margin: 0 }}>💵 Financial insights</h4>
        <div style={{ marginLeft: "auto", display: "flex", gap: 4 }}>
          {PERIODS.map(p => (
            <button key={p.d} onClick={() => setDays(p.d)}
              style={{ ...chip, ...(days === p.d ? chipOn : {}) }}>{p.l}</button>
          ))}
        </div>
      </div>

      <div style={grid}>
        <Stat label="Revenue" value={R(d.revenue_cents)} color={C.green}
              sub={`${d.orders_count} paid orders`} />
        <Stat label="Expenses" value={R(d.expenses_cents)} color={C.red} />
        <Stat label={`Gross profit · ${d.margin_pct}%`} value={R(profit)} color={profitColor} />
        <Stat label="VAT collected" value={R(d.vat.collected_cents)} color={C.text} sub="on paid invoices" />
        <Stat label="Owed to you" value={R(d.receivables.outstanding_cents)} color={C.text}
              sub={`${d.receivables.count} unpaid invoices`} />
        <Stat label="Overdue" value={R(d.receivables.overdue_cents)}
              color={d.receivables.overdue_cents > 0 ? C.red : C.muted} sub="chase these" />
      </div>

      <div style={{ ...card, marginTop: 14, background: C.alt }}>
        <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
          <strong style={{ fontSize: 13 }}>Ask Vula</strong>
          <button style={{ ...chip, ...chipOn, marginLeft: "auto" }} onClick={askVula} disabled={asking}>
            {asking ? "Thinking…" : "✨ Explain my numbers"}
          </button>
        </div>
        {summary && <p style={{ fontSize: 14, lineHeight: 1.55, margin: "10px 0 0", whiteSpace: "pre-wrap" }}>{summary}</p>}
      </div>

      {(d.top_customers?.length > 0 || d.top_expense_categories?.length > 0) && (
        <div style={{ display: "flex", gap: 16, marginTop: 14, flexWrap: "wrap" }}>
          {d.top_customers?.length > 0 && (
            <div style={{ ...card, flex: 1, minWidth: 240 }}>
              <div style={lbl}>Top customers</div>
              {d.top_customers.map((c, i) => (
                <Row key={i} a={c.name} b={R(c.revenue_cents)} />
              ))}
            </div>
          )}
          {d.top_expense_categories?.length > 0 && (
            <div style={{ ...card, flex: 1, minWidth: 240 }}>
              <div style={lbl}>Top expense categories</div>
              {d.top_expense_categories.map((c, i) => (
                <Row key={i} a={c.category} b={R(c.amount_cents)} />
              ))}
            </div>
          )}
        </div>
      )}
      <div style={{ fontSize: 11, color: C.muted, marginTop: 10 }}>{d.vat.note}</div>
    </div>
  );
}

const Stat = ({ label, value, color, sub }) => (
  <div style={card}>
    <div style={lbl}>{label}</div>
    <div style={{ fontSize: 22, fontWeight: 700, color }}>{value}</div>
    {sub && <div style={{ fontSize: 11, color: C.muted, marginTop: 2 }}>{sub}</div>}
  </div>
);
const Row = ({ a, b }) => (
  <div style={{ display: "flex", justifyContent: "space-between", fontSize: 13, padding: "4px 0", borderBottom: `1px solid ${C.border}` }}>
    <span style={{ color: C.text, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap", maxWidth: "65%" }}>{a}</span>
    <span style={{ color: C.muted, fontWeight: 600 }}>{b}</span>
  </div>
);

const grid = { display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(150px, 1fr))", gap: 12 };
const card = { background: C.surface, border: `1px solid ${C.border}`, borderRadius: 10, padding: 14 };
const lbl = { fontSize: 12, color: C.muted, marginBottom: 6 };
const chip = { padding: "5px 12px", border: `1px solid ${C.border}`, borderRadius: 16, background: C.surface, color: C.text, fontSize: 12, cursor: "pointer" };
const chipOn = { background: C.green, color: "#fff", borderColor: C.green };
