/**
 * VulaCSMetrics.jsx — the "AI economics" card. Surfaces cost-per-conversation vs the industry
 * benchmark (Cue's ~R20/AI-resolution) plus the % handled on local infrastructure. This is the
 * flat-pricing sales story made concrete: local-first inference = near-zero marginal cost.
 */
import { useState, useEffect } from "react";

const VULA_API = import.meta.env.VITE_API_URL || "https://vula-group-production.up.railway.app";

export default function VulaCSMetrics({ tenantId }) {
  const [m, setM] = useState(null);
  useEffect(() => {
    fetch(`${VULA_API}/v1/commerce/${tenantId}/admin/cs-metrics`)
      .then((r) => r.json()).then(setM).catch(() => setM(null));
  }, [tenantId]);

  if (!m || m.conversations == null) return null;
  const cpc = Number(m.cost_per_conversation_zar || 0);
  const bench = Number(m.benchmark_cue_per_resolution_zar || 20);
  const cheaper = cpc > 0 ? Math.round(bench / cpc) : null;

  const wrap = { background: "linear-gradient(135deg,#0E3B2E,#14503D)", color: "#EAF3EF",
    border: "1px solid #14503D", borderRadius: 12, padding: 18, margin: "18px 0" };
  const tiles = { display: "flex", gap: 12, flexWrap: "wrap", marginTop: 12 };
  const tile = { flex: "1 1 130px", background: "rgba(255,255,255,0.06)", borderRadius: 10, padding: "12px 14px" };
  const big = { fontSize: 26, fontWeight: 800, lineHeight: 1.1 };
  const cap = { fontSize: 11, opacity: 0.75, textTransform: "uppercase", letterSpacing: "0.04em", marginTop: 4 };

  return (
    <div style={wrap}>
      <div style={{ display: "flex", alignItems: "baseline", gap: 10, flexWrap: "wrap" }}>
        <span style={{ fontWeight: 700, fontSize: 15 }}>Your AI economics</span>
        <span style={{ fontSize: 12, opacity: 0.7 }}>last {m.period_days} days · {m.conversations} conversations</span>
      </div>

      <div style={tiles}>
        <div style={tile}>
          <div style={{ ...big, color: "#7FE3B0" }}>R{cpc.toFixed(2)}</div>
          <div style={cap}>Cost / conversation</div>
        </div>
        <div style={tile}>
          <div style={{ ...big, opacity: 0.85 }}>R{bench.toFixed(0)}</div>
          <div style={cap}>Typical CS platform*</div>
        </div>
        {m.pct_handled_local != null && (
          <div style={tile}>
            <div style={{ ...big, color: "#7FE3B0" }}>{m.pct_handled_local}%</div>
            <div style={cap}>Handled on your GPU</div>
          </div>
        )}
        <div style={tile}>
          <div style={big}>R{Number(m.ai_cost_zar || 0).toFixed(2)}</div>
          <div style={cap}>Total AI cost (30d)</div>
        </div>
      </div>

      {cheaper && cheaper > 1 && (
        <p style={{ marginTop: 12, fontSize: 13, opacity: 0.92 }}>
          ≈ <strong>{cheaper}× cheaper</strong> per conversation than the industry benchmark — because your AI
          runs on your own infrastructure, so there's no per-message cloud bill to pass on.
        </p>
      )}
      <p style={{ marginTop: 6, fontSize: 10, opacity: 0.5 }}>*Benchmark: ~R20 per AI resolution (Cue public pricing, £0.89).</p>
    </div>
  );
}
