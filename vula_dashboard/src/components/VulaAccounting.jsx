/**
 * VulaAccounting.jsx — 📒 Books: P&L by category, VAT summary (if registered), and a
 * general-ledger export her accountant can file from. Built from the reconciled bank ledger.
 * Backend: /v1/commerce/{tenant}/admin/reports/* (migration 058).
 */
import { useState, useEffect, useCallback } from "react";

const VULA_API = import.meta.env.VITE_API_URL || "https://vula-group-production.up.railway.app";
const C = { surface: "#FFFFFF", border: "#DDD8CE", green: "var(--accent)", red: "#A23B2D", text: "#2A2A2A", muted: "#8A8680", alt: "#F0EDE5" };
const R = (c) => `R${((c || 0) / 100).toLocaleString("en-ZA", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;

export default function VulaAccounting({ tenantId }) {
  const [since, setSince] = useState("");
  const [until, setUntil] = useState("");
  const [pnl, setPnl] = useState(null);
  const [vat, setVat] = useState(null);

  const qs = () => { const p = new URLSearchParams(); if (since) p.set("since", since); if (until) p.set("until", until); return p.toString(); };

  const load = useCallback(async () => {
    const q = new URLSearchParams(); if (since) q.set("since", since); if (until) q.set("until", until);
    const [p, v] = await Promise.all([
      fetch(`${VULA_API}/v1/commerce/${tenantId}/admin/reports/pnl?${q}`).then(r => r.json()).catch(() => null),
      fetch(`${VULA_API}/v1/commerce/${tenantId}/admin/reports/vat?${q}`).then(r => r.json()).catch(() => null),
    ]);
    setPnl(p); setVat(v);
  }, [tenantId, since, until]);
  useEffect(() => { load(); }, [load]);

  return (
    <div style={{ fontFamily: "system-ui", color: C.text, maxWidth: 720 }}>
      <div style={{ display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap", marginBottom: 4 }}>
        <h4 style={{ fontSize: 15, fontWeight: 600, margin: 0 }}>📒 Books</h4>
        <span style={{ fontSize: 12, color: C.muted }}>from</span>
        <input type="date" value={since} onChange={e => setSince(e.target.value)} style={input} />
        <span style={{ fontSize: 12, color: C.muted }}>to</span>
        <input type="date" value={until} onChange={e => setUntil(e.target.value)} style={input} />
        <a href={`${VULA_API}/v1/commerce/${tenantId}/admin/reports/ledger.csv?${qs()}`} target="_blank" rel="noreferrer"
          style={{ ...btn, ...btnOn, marginLeft: "auto", textDecoration: "none" }}>⬇ Export for accountant (CSV)</a>
      </div>
      <p style={{ color: C.muted, fontSize: 13, marginTop: 0 }}>Your books, built from the reconciled bank statement. Export the ledger for your accountant to file.</p>

      {/* P&L */}
      {pnl && (
        <div style={{ ...card, marginBottom: 14 }}>
          <div style={{ display: "flex", gap: 12, marginBottom: 10, flexWrap: "wrap" }}>
            <Big label="Income" value={R(pnl.total_income_cents)} color={C.green} />
            <Big label="Expenses" value={R(pnl.total_expense_cents)} color={C.red} />
            <Big label="Net profit" value={R(pnl.net_profit_cents)} color={pnl.net_profit_cents >= 0 ? C.green : C.red} />
          </div>
          <Section title="Income" rows={pnl.income} />
          <Section title="Expenses" rows={pnl.expenses} />
          {(!pnl.income?.length && !pnl.expenses?.length) &&
            <div style={{ fontSize: 12.5, color: C.muted }}>Nothing categorised yet — reconcile a statement in the Bank tab first.</div>}
        </div>
      )}

      {/* VAT */}
      <div style={card}>
        <div style={{ fontWeight: 600, fontSize: 13, marginBottom: 8 }}>VAT (SARS)</div>
        {!vat ? <span style={{ color: C.muted, fontSize: 12 }}>—</span>
          : vat.vat_registered === false
            ? <div style={{ fontSize: 12.5, color: C.muted }}>Not VAT-registered — no VAT return needed. (Turn on VAT registration in Settings if this changes.)</div>
            : (
              <div style={{ display: "flex", gap: 12, flexWrap: "wrap" }}>
                <Big label="Output VAT (sales)" value={R(vat.output_vat_cents)} />
                <Big label="Input VAT (purchases)" value={R(vat.input_vat_cents)} />
                <Big label={vat.net_vat_cents >= 0 ? "VAT payable to SARS" : "VAT refund due"} value={R(Math.abs(vat.net_vat_cents))}
                  color={vat.net_vat_cents >= 0 ? C.red : C.green} />
              </div>
            )}
        {vat?.note && <div style={{ fontSize: 11, color: C.muted, marginTop: 8 }}>{vat.note}</div>}
      </div>
    </div>
  );
}

const Big = ({ label, value, color }) => (
  <div style={{ flex: "1 1 120px" }}>
    <div style={{ fontSize: 10, textTransform: "uppercase", color: C.muted, marginBottom: 2 }}>{label}</div>
    <div style={{ fontSize: 18, fontWeight: 700, color: color || C.text }}>{value}</div>
  </div>
);

const Section = ({ title, rows }) => (!rows?.length ? null : (
  <div style={{ marginTop: 8 }}>
    <div style={{ fontSize: 11, textTransform: "uppercase", color: C.muted, marginBottom: 4 }}>{title}</div>
    {rows.map((r, i) => (
      <div key={i} style={{ display: "flex", justifyContent: "space-between", fontSize: 13, padding: "3px 0", borderBottom: `1px solid ${C.alt}` }}>
        <span>{r.account}</span><span style={{ fontWeight: 600 }}>{R(r.amount_cents)}</span>
      </div>
    ))}
  </div>
));

const card = { background: C.surface, border: `1px solid ${C.border}`, borderRadius: 10, padding: 14 };
const btn = { padding: "7px 12px", border: `1px solid ${C.border}`, borderRadius: 6, background: C.surface, color: C.text, fontSize: 12, cursor: "pointer" };
const btnOn = { background: C.green, color: "#fff", borderColor: C.green };
const input = { padding: "5px 8px", border: `1px solid ${C.border}`, borderRadius: 6, fontSize: 12, background: C.surface, color: C.text };
