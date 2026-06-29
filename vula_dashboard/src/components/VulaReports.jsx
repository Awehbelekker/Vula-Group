/**
 * VulaReports.jsx — Sales Trend + Product Performance (Phase-1 reporting).
 * Reads /admin/reports (tenant-scoped, integer cents).
 */
import { useState, useEffect, useCallback } from "react";
import { T } from "../theme/tokens";
import { Card, StatCard, SectionTitle } from "./ui";

const VULA_API = import.meta.env.VITE_API_URL || "https://vula-group-production.up.railway.app";
const rand = (c) => "R" + Math.round((c || 0) / 100).toLocaleString("en-ZA");

export default function VulaReports({ tenantId }) {
  const [days, setDays] = useState(30);
  const [data, setData] = useState({ revenue_trend: [], top_products: [], total_revenue_cents: 0, total_orders: 0 });

  const load = useCallback(async () => {
    if (!tenantId) return;
    const r = await fetch(`${VULA_API}/v1/commerce/${tenantId}/admin/reports?days=${days}`);
    setData(await r.json());
  }, [tenantId, days]);
  useEffect(() => { load(); }, [load]);

  const trend = data.revenue_trend || [];
  const max = Math.max(1, ...trend.map((d) => d.revenue_cents));
  const top = data.top_products || [];
  const maxP = Math.max(1, ...top.map((p) => p.revenue_cents));
  const aov = data.total_orders ? data.total_revenue_cents / data.total_orders : 0;

  return (
    <div style={{ maxWidth: 1000, margin: "0 auto", padding: 24 }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-end", flexWrap: "wrap", gap: 10 }}>
        <SectionTitle sub="Sales trend & product performance">Reports</SectionTitle>
        <div style={{ display: "flex", gap: 6 }}>
          {[7, 30, 90].map((d) => (
            <button key={d} onClick={() => setDays(d)} style={{ padding: "6px 12px", borderRadius: T.rPill, fontSize: 12, cursor: "pointer",
              border: `1px solid ${days === d ? T.accent : T.border}`, background: days === d ? T.accent : T.surface, color: days === d ? T.onAccent : T.muted }}>{d}d</button>
          ))}
        </div>
      </div>

      <div style={{ display: "grid", gridTemplateColumns: "repeat(3,1fr)", gap: 12, margin: "16px 0 20px" }}>
        <StatCard label={`Revenue · ${days}d`} value={rand(data.total_revenue_cents)} accent />
        <StatCard label="Orders" value={data.total_orders || 0} />
        <StatCard label="Avg order value" value={rand(aov)} />
      </div>

      {/* Sales trend */}
      <Card style={{ marginBottom: 20 }}>
        <div style={{ fontSize: 13, fontWeight: 600, color: T.ink, marginBottom: 14 }}>Sales trend</div>
        <div style={{ display: "flex", alignItems: "flex-end", gap: trend.length > 45 ? 1 : 3, height: 140 }}>
          {trend.map((d) => (
            <div key={d.date} title={`${d.date}: ${rand(d.revenue_cents)} · ${d.orders} orders`}
              style={{ flex: 1, background: T.accent, opacity: 0.25 + 0.75 * (d.revenue_cents / max),
                height: `${Math.max(2, (d.revenue_cents / max) * 100)}%`, borderRadius: "3px 3px 0 0", minWidth: 2 }} />
          ))}
        </div>
        {trend.length === 0 && <div style={{ fontSize: 13, color: T.muted, padding: 12 }}>No sales in this period.</div>}
      </Card>

      {/* Product performance */}
      <Card>
        <div style={{ fontSize: 13, fontWeight: 600, color: T.ink, marginBottom: 14 }}>Top products</div>
        {top.length === 0 && <div style={{ fontSize: 13, color: T.muted }}>No product sales yet.</div>}
        {top.map((p) => (
          <div key={p.name} style={{ marginBottom: 10 }}>
            <div style={{ display: "flex", justifyContent: "space-between", fontSize: 13, marginBottom: 3 }}>
              <span style={{ color: T.text }}>{p.name} <span style={{ color: T.muted, fontSize: 11 }}>· {p.units} units</span></span>
              <span className="vula-money" style={{ color: T.ink, fontWeight: 600 }}>{rand(p.revenue_cents)}</span>
            </div>
            <div style={{ height: 6, background: T.surfaceAlt, borderRadius: 3, overflow: "hidden" }}>
              <div style={{ width: `${(p.revenue_cents / maxP) * 100}%`, height: "100%", background: T.accent, borderRadius: 3 }} />
            </div>
          </div>
        ))}
      </Card>
      <p style={{ textAlign: "center", fontSize: 11, color: "#B5B0A8", marginTop: 20 }}>Powered by Vula · paid orders only</p>
    </div>
  );
}
