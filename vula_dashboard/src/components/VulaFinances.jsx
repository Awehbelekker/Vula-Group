/**
 * VulaFinances.jsx — money in/out per project, budget-vs-actual, built from filed
 * invoices/payments. Payments reconciled to invoices (matched, not guessed).
 */
import { useState, useEffect, useCallback } from "react";

const VULA_API = import.meta.env.VITE_API_URL || "https://vula-group-production.up.railway.app";
const C = { surface: "#FFFFFF", border: "#DDD8CE", green: "#2C5545", red: "#A23B2D", text: "#2A2A2A", muted: "#8A8680", alt: "#F0EDE5" };
const rand = (n) => "R" + (Number(n) || 0).toLocaleString("en-ZA", { maximumFractionDigits: 0 });

export default function VulaFinances({ tenantId }) {
  const [data, setData] = useState({ projects: [], transactions: [], total_in: 0, total_out: 0 });
  const [editing, setEditing] = useState(null);
  const [budget, setBudget] = useState("");

  const load = useCallback(async () => {
    if (!tenantId) return;
    const r = await fetch(`${VULA_API}/v1/projects/${tenantId}/finances`);
    setData(await r.json());
  }, [tenantId]);
  useEffect(() => { load(); }, [load]);

  const saveBudget = async (project) => {
    await fetch(`${VULA_API}/v1/projects/${tenantId}/budget`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ project, budget: Number(budget) || 0 }),
    });
    setEditing(null); setBudget(""); load();
  };

  return (
    <div style={{ maxWidth: 1000, margin: "0 auto", padding: 24 }}>
      <h1 style={{ fontFamily: "'Cormorant Garamond', serif", fontSize: 28, fontWeight: 700, color: C.text, margin: "0 0 2px" }}>Finances</h1>
      <p style={{ fontSize: 13, color: C.muted, margin: "0 0 18px" }}>Money in/out per project — built from invoices & payments Vula files. Payments are matched to invoices, not guessed.</p>

      <div style={{ display: "flex", gap: 12, marginBottom: 18 }}>
        <div style={{ flex: 1, background: C.surface, border: `1px solid ${C.border}`, borderRadius: 10, padding: 16 }}>
          <div style={{ fontSize: 12, color: C.muted }}>Money in</div>
          <div style={{ fontSize: 24, fontWeight: 700, color: C.green }}>{rand(data.total_in)}</div>
        </div>
        <div style={{ flex: 1, background: C.surface, border: `1px solid ${C.border}`, borderRadius: 10, padding: 16 }}>
          <div style={{ fontSize: 12, color: C.muted }}>Money out</div>
          <div style={{ fontSize: 24, fontWeight: 700, color: C.red }}>{rand(data.total_out)}</div>
        </div>
        <div style={{ flex: 1, background: C.surface, border: `1px solid ${C.border}`, borderRadius: 10, padding: 16 }}>
          <div style={{ fontSize: 12, color: C.muted }}>Net</div>
          <div style={{ fontSize: 24, fontWeight: 700, color: C.text }}>{rand(data.total_in - data.total_out)}</div>
        </div>
      </div>

      <div style={{ background: C.surface, border: `1px solid ${C.border}`, borderRadius: 10, overflow: "hidden", marginBottom: 20 }}>
        <div style={{ padding: "10px 16px", display: "grid", gridTemplateColumns: "1.6fr 1fr 1fr 1fr 1.2fr", gap: 8, fontSize: 11, color: C.muted, textTransform: "uppercase", background: C.alt }}>
          <span>Project</span><span>In</span><span>Out</span><span>Budget</span><span>Remaining</span>
        </div>
        {data.projects.length === 0 && <div style={{ padding: 16, fontSize: 13, color: C.muted }}>No financial documents filed yet.</div>}
        {data.projects.map((p) => (
          <div key={p.project} style={{ padding: "11px 16px", borderTop: `1px solid ${C.alt}`, display: "grid", gridTemplateColumns: "1.6fr 1fr 1fr 1fr 1.2fr", gap: 8, alignItems: "center", fontSize: 13 }}>
            <span style={{ fontWeight: 600, color: C.text }}>{p.project} <span style={{ color: C.muted, fontWeight: 400, fontSize: 11 }}>· {p.count}</span></span>
            <span style={{ color: C.green }}>{rand(p.in)}</span>
            <span style={{ color: C.red }}>{rand(p.out)}</span>
            <span>
              {editing === p.project
                ? <input autoFocus value={budget} onChange={(e) => setBudget(e.target.value)} onBlur={() => saveBudget(p.project)} onKeyDown={(e) => e.key === "Enter" && saveBudget(p.project)} placeholder="0" style={{ width: 80, padding: "3px 6px", border: `1px solid ${C.border}`, borderRadius: 5, fontSize: 12 }} />
                : <span onClick={() => { setEditing(p.project); setBudget(p.budget || ""); }} style={{ cursor: "pointer", color: p.budget ? C.text : C.muted, borderBottom: `1px dotted ${C.muted}` }}>{p.budget ? rand(p.budget) : "set"}</span>}
            </span>
            <span style={{ color: p.remaining != null && p.remaining < 0 ? C.red : C.text }}>{p.remaining != null ? rand(p.remaining) : "—"}</span>
          </div>
        ))}
      </div>

      <h3 style={{ fontSize: 14, color: C.text, margin: "0 0 8px" }}>Recent transactions</h3>
      <div style={{ background: C.surface, border: `1px solid ${C.border}`, borderRadius: 10, overflow: "hidden" }}>
        {data.transactions.slice(0, 25).map((t) => (
          <div key={t.id} style={{ padding: "9px 16px", borderTop: `1px solid ${C.alt}`, display: "grid", gridTemplateColumns: "70px 1.4fr 1fr 90px 70px", gap: 8, alignItems: "center", fontSize: 12.5 }}>
            <span style={{ color: t.direction === "in" ? C.green : t.direction === "out" ? C.red : C.muted, fontWeight: 600 }}>{t.direction === "in" ? "▲ in" : t.direction === "out" ? "▼ out" : "•"}</span>
            <span style={{ color: C.text }}>{t.counterparty || t.filename} <span style={{ color: C.muted }}>{t.description ? `· ${String(t.description).slice(0, 40)}` : ""}</span></span>
            <span style={{ color: C.muted }}>{t.project || "—"}</span>
            <span style={{ fontWeight: 600, color: C.text }}>{rand(t.amount)}</span>
            <span style={{ fontSize: 10, color: t.reconciled ? C.green : C.muted }}>{t.reconciled ? "✓ matched" : t.kind}</span>
          </div>
        ))}
      </div>
      <p style={{ textAlign: "center", fontSize: 11, color: "#B5B0A8", marginTop: 20 }}>Powered by Vula · figures from filed invoices & payments</p>
    </div>
  );
}
