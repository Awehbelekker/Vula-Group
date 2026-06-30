/**
 * VulaAISpend.jsx — master AI/LLM cost (COGS) visibility.
 * Reads /v1/tenants/ai-spend (from vula_ai_usage). Total spend, daily trend, by tenant, by model.
 */
import { useState, useEffect, useCallback } from "react";

const VULA_API = import.meta.env.VITE_API_URL || "https://vula-group-production.up.railway.app";
const C = { surface: "#FFFFFF", border: "#DDD8CE", accent: "var(--accent)", ink: "#2A2A2A", muted: "#8A8680", alt: "#F0EDE5" };
const usd = (n) => `$${(n || 0).toFixed(n < 1 ? 4 : 2)}`;

export default function VulaAISpend() {
  const [days, setDays] = useState(14);
  const [d, setD] = useState(null);

  const load = useCallback(async () => {
    const r = await fetch(`${VULA_API}/v1/tenants/ai-spend?days=${days}`);
    setD(await r.json());
  }, [days]);
  useEffect(() => { load(); }, [load]);

  if (!d) return <div style={{ padding: 24, color: C.muted }}>Loading…</div>;
  const maxDay = Math.max(0.0001, ...(d.daily || []).map((x) => x.usd));
  const card = { background: C.surface, border: `1px solid ${C.border}`, borderRadius: 10, padding: 16 };
  const Stat = ({ label, value, sub }) => (
    <div style={{ ...card, flex: 1, minWidth: 150 }}>
      <div style={{ fontSize: 11, color: C.muted, textTransform: "uppercase" }}>{label}</div>
      <div style={{ fontSize: 24, fontWeight: 700, color: C.ink, fontFamily: "'Source Code Pro', monospace" }}>{value}</div>
      {sub && <div style={{ fontSize: 11, color: C.muted }}>{sub}</div>}
    </div>
  );

  const Table = ({ title, rows, label }) => (
    <div style={{ ...card, flex: 1, minWidth: 280 }}>
      <div style={{ fontWeight: 700, color: C.ink, marginBottom: 8 }}>{title}</div>
      {(rows || []).length === 0 && <div style={{ fontSize: 12, color: C.muted }}>No usage yet.</div>}
      {(rows || []).slice(0, 10).map((r) => (
        <div key={r.key} style={{ display: "flex", justifyContent: "space-between", padding: "5px 0", borderTop: `1px solid ${C.alt}`, fontSize: 12.5 }}>
          <span style={{ color: r.key === "_unattributed" ? "#C4861A" : C.ink }}>{label(r.key)}</span>
          <span style={{ color: C.muted }}>
            <b style={{ color: C.ink, fontFamily: "'Source Code Pro', monospace" }}>{usd(r.usd)}</b> · {r.calls} calls · {(r.tokens / 1000).toFixed(1)}k tok
          </span>
        </div>
      ))}
    </div>
  );

  return (
    <div style={{ maxWidth: 1000, margin: "0 auto", padding: 24 }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 16 }}>
        <div>
          <h1 style={{ fontFamily: "'Cormorant Garamond', serif", fontSize: 28, fontWeight: 700, color: C.ink, margin: 0 }}>AI Spend</h1>
          <p style={{ fontSize: 13, color: C.muted, margin: "2px 0 0" }}>LLM cost across all tenants (estimated COGS). “_unattributed” = background/test calls.</p>
        </div>
        <select value={days} onChange={(e) => setDays(Number(e.target.value))} style={{ padding: "8px 10px", border: `1px solid ${C.border}`, borderRadius: 8, fontSize: 13 }}>
          {[7, 14, 30, 90].map((n) => <option key={n} value={n}>Last {n} days</option>)}
        </select>
      </div>

      {d.error && <div style={{ ...card, color: "#A23B2D", marginBottom: 12 }}>{d.error}</div>}

      <div style={{ display: "flex", gap: 12, flexWrap: "wrap", marginBottom: 14 }}>
        <Stat label={`Total (${days}d)`} value={usd(d.total_usd)} sub={`${d.total_calls} calls`} />
        <Stat label="Avg / day" value={usd((d.total_usd || 0) / Math.max(1, days))} />
        <Stat label="Avg / call" value={usd(d.total_calls ? d.total_usd / d.total_calls : 0)} />
      </div>

      {/* Daily trend */}
      <div style={{ ...card, marginBottom: 14 }}>
        <div style={{ fontWeight: 700, color: C.ink, marginBottom: 10 }}>Daily spend</div>
        <div style={{ display: "flex", alignItems: "flex-end", gap: 4, height: 120 }}>
          {(d.daily || []).map((x) => (
            <div key={x.day} title={`${x.day}: ${usd(x.usd)}`} style={{ flex: 1, display: "flex", flexDirection: "column", justifyContent: "flex-end", alignItems: "center" }}>
              <div style={{ width: "100%", height: `${Math.max(2, (x.usd / maxDay) * 100)}%`, background: "var(--accent)", borderRadius: "3px 3px 0 0", minHeight: 2 }} />
              <span style={{ fontSize: 8, color: C.muted, marginTop: 2 }}>{x.day.slice(5)}</span>
            </div>
          ))}
          {(d.daily || []).length === 0 && <span style={{ color: C.muted, fontSize: 12 }}>No usage in this period yet.</span>}
        </div>
      </div>

      <div style={{ display: "flex", gap: 12, flexWrap: "wrap" }}>
        <Table title="By tenant" rows={d.by_tenant} label={(k) => k} />
        <Table title="By model" rows={d.by_model} label={(k) => k} />
      </div>
      <p style={{ textAlign: "center", fontSize: 11, color: "#B5B0A8", marginTop: 18 }}>Estimated from token counts · set VULA_DEV_MODE=true while testing to cut cost</p>
    </div>
  );
}
