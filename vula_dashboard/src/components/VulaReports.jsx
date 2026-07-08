import { useState, useEffect, useCallback } from "react";

const VULA_API = import.meta.env.VITE_API_URL || "https://vula-group-production.up.railway.app";

const C = {
  bg: "#F7F4EE", surface: "#FFFFFF", border: "#DDD8CE",
  green: "var(--accent)", text: "#2A2A2A", muted: "#8A8680",
  surfaceAlt: "#F0EDE5", greenLight: "#EAF2EF", amber: "#C4861A",
};

function fmt(cents) {
  return `R ${(cents / 100).toFixed(2).replace(/\B(?=(\d{3})+(?!\d))/g, ",")}`;
}

function StatCard({ label, value, sub }) {
  return (
    <div style={{
      background: C.surface, border: `1px solid ${C.border}`,
      borderRadius: 10, padding: "16px 20px",
    }}>
      <div style={{ fontSize: 11, fontWeight: 600, color: C.muted, textTransform: "uppercase", marginBottom: 6 }}>{label}</div>
      <div style={{ fontSize: 24, fontWeight: 700, color: C.text }}>{value}</div>
      {sub && <div style={{ fontSize: 12, color: C.muted, marginTop: 3 }}>{sub}</div>}
    </div>
  );
}

function BarChart({ data }) {
  const max = Math.max(...data.map(d => d.revenue_cents), 1);
  return (
    <div style={{ display: "flex", alignItems: "flex-end", gap: 3, height: 120, padding: "0 4px" }}>
      {data.map((d, i) => {
        const pct = (d.revenue_cents / max) * 100;
        const label = d.date?.slice(5); // MM-DD
        return (
          <div key={i} style={{ flex: 1, display: "flex", flexDirection: "column", alignItems: "center", gap: 3 }}>
            <div
              title={`${label}: ${fmt(d.revenue_cents)} · ${d.orders} orders`}
              style={{
                width: "100%", background: d.revenue_cents > 0 ? C.green : C.border,
                height: `${Math.max(pct, 2)}%`, borderRadius: "3px 3px 0 0",
                opacity: 0.85, cursor: "default", transition: "opacity 0.15s",
              }}
            />
            {data.length <= 14 && (
              <div style={{ fontSize: 9, color: C.muted, writingMode: "vertical-lr", transform: "rotate(180deg)" }}>
                {label}
              </div>
            )}
          </div>
        );
      })}
    </div>
  );
}

export default function VulaReports({ tenantId }) {
  const [days, setDays] = useState(30);
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

  const load = useCallback(async (d) => {
    if (!tenantId) return;
    setLoading(true); setError("");
    try {
      const res = await fetch(`${VULA_API}/v1/commerce/${tenantId}/admin/reports?days=${d}`);
      if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
      setData(await res.json());
    } catch (e) { setError(e.message); }
    finally { setLoading(false); }
  }, [tenantId]);

  useEffect(() => { load(days); }, [load, days]);

  const avgCents = data?.total_orders > 0 ? Math.round(data.total_revenue_cents / data.total_orders) : 0;

  return (
    <div style={{ padding: "4px 0" }}>
      {/* Period selector */}
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 20 }}>
        <h2 style={{ margin: 0, fontSize: 20, fontWeight: 700, color: C.text }}>Sales Reports</h2>
        <div style={{ display: "flex", gap: 6 }}>
          {[7, 14, 30, 90].map(n => (
            <button key={n} onClick={() => setDays(n)} style={{
              padding: "6px 14px", fontSize: 12, fontWeight: 600, borderRadius: 6,
              border: `1px solid ${days === n ? C.green : C.border}`,
              background: days === n ? C.greenLight : C.surface,
              color: days === n ? C.green : C.muted, cursor: "pointer",
            }}>{n}d</button>
          ))}
        </div>
      </div>

      {error && <div style={{ padding: 12, background: "#FDEDEC", borderRadius: 8, color: "#C0392B", fontSize: 13, marginBottom: 20 }}>{error}</div>}

      {loading && <div style={{ padding: 60, textAlign: "center", color: C.muted }}>Loading…</div>}

      {!loading && data && (
        <>
          {/* Summary stats */}
          <div style={{ display: "grid", gridTemplateColumns: "repeat(3, 1fr)", gap: 12, marginBottom: 24 }}>
            <StatCard label="Total Revenue" value={fmt(data.total_revenue_cents)} sub={`last ${days} days`} />
            <StatCard label="Total Orders" value={data.total_orders} sub="paid / confirmed / dispatched" />
            <StatCard label="Avg Order Value" value={fmt(avgCents)} sub="per order" />
          </div>

          {/* Revenue trend */}
          <div style={{
            background: C.surface, border: `1px solid ${C.border}`,
            borderRadius: 10, padding: 20, marginBottom: 24,
          }}>
            <div style={{ fontSize: 13, fontWeight: 700, color: C.text, marginBottom: 16 }}>
              Revenue Trend — last {days} days
            </div>
            {data.revenue_trend && data.revenue_trend.length > 0 ? (
              <BarChart data={data.revenue_trend} />
            ) : (
              <div style={{ padding: 40, textAlign: "center", color: C.muted, fontSize: 13 }}>
                No revenue data for this period.
              </div>
            )}
            <div style={{ marginTop: 12, display: "flex", gap: 20, fontSize: 11, color: C.muted }}>
              <span>Hover bars for daily detail</span>
              {data.revenue_trend?.length > 14 && <span>Showing {data.revenue_trend.length} days</span>}
            </div>
          </div>

          {/* Top products */}
          <div style={{
            background: C.surface, border: `1px solid ${C.border}`,
            borderRadius: 10, padding: 20,
          }}>
            <div style={{ fontSize: 13, fontWeight: 700, color: C.text, marginBottom: 16 }}>
              Top Products by Revenue
            </div>
            {data.top_products && data.top_products.length > 0 ? (
              <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 13 }}>
                <thead>
                  <tr style={{ borderBottom: `1px solid ${C.border}` }}>
                    <th style={{ textAlign: "left", padding: "6px 8px", color: C.muted, fontWeight: 600, fontSize: 11, textTransform: "uppercase" }}>Product</th>
                    <th style={{ textAlign: "right", padding: "6px 8px", color: C.muted, fontWeight: 600, fontSize: 11, textTransform: "uppercase" }}>Units</th>
                    <th style={{ textAlign: "right", padding: "6px 8px", color: C.muted, fontWeight: 600, fontSize: 11, textTransform: "uppercase" }}>Revenue</th>
                    <th style={{ padding: "6px 8px", width: 120 }}></th>
                  </tr>
                </thead>
                <tbody>
                  {data.top_products.map((p, i) => {
                    const maxRev = data.top_products[0]?.revenue_cents || 1;
                    const pct = Math.round((p.revenue_cents / maxRev) * 100);
                    return (
                      <tr key={i} style={{ borderBottom: `1px solid ${C.border}` }}>
                        <td style={{ padding: "10px 8px", fontWeight: 600, color: C.text }}>{p.name}</td>
                        <td style={{ padding: "10px 8px", textAlign: "right", color: C.muted }}>{p.units}</td>
                        <td style={{ padding: "10px 8px", textAlign: "right", fontWeight: 700, color: C.text }}>{fmt(p.revenue_cents)}</td>
                        <td style={{ padding: "10px 8px" }}>
                          <div style={{ height: 6, background: C.border, borderRadius: 3, overflow: "hidden" }}>
                            <div style={{ height: "100%", width: `${pct}%`, background: C.green, borderRadius: 3 }} />
                          </div>
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            ) : (
              <div style={{ padding: 40, textAlign: "center", color: C.muted, fontSize: 13 }}>
                No product data for this period.
              </div>
            )}
          </div>
        </>
      )}
    </div>
  );
}
