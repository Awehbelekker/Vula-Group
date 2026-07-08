/**
 * VulaClientOnboarding.jsx — merchant-facing panel: introduce the WhatsApp line to imported clients.
 * Staci sends the approved intro/opt-in template in small batches (protects the number's quality
 * rating), previews to her own phone first, and watches the opt-in funnel fill.
 * (Distinct from VulaOnboarding.jsx, which is the Vula platform trial-signup wizard.)
 */
import { useState, useEffect } from "react";

const VULA_API = import.meta.env.VITE_API_URL || "https://vula-group-production.up.railway.app";
const C = { surface: "#FFFFFF", border: "#DDD8CE", text: "#2A2A2A", muted: "#8A8680" };

export default function VulaClientOnboarding({ tenantId }) {
  const [stats, setStats] = useState(null);
  const [test, setTest] = useState("");
  const [batch, setBatch] = useState(25);
  const [msg, setMsg] = useState("");
  const [busy, setBusy] = useState(false);

  const load = () =>
    fetch(`${VULA_API}/v1/commerce/${tenantId}/admin/onboarding/status`)
      .then((r) => r.json()).then((d) => setStats(d.stats || {})).catch(() => setStats({}));
  useEffect(() => { load(); }, [tenantId]);

  const send = async (payload, label) => {
    setBusy(true); setMsg(`${label}…`);
    try {
      const d = await fetch(`${VULA_API}/v1/commerce/${tenantId}/admin/onboarding/send-batch`, {
        method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload),
      }).then((r) => r.json());
      if (d.error) setMsg(`⚠️ ${d.error}`);
      else setMsg(`✓ Sent ${d.sent}${d.failed ? `, ${d.failed} failed` : ""}${d.remaining != null ? ` · ${d.remaining} left` : ""}`);
      load();
    } catch { setMsg("⚠️ Request failed"); }
    setBusy(false);
  };

  if (!stats) return null;
  const field = { padding: "8px 10px", border: `1px solid ${C.border}`, borderRadius: 8, fontSize: 13 };
  const btn = (bg) => ({ padding: "9px 14px", border: "none", borderRadius: 8, fontSize: 13, fontWeight: 600,
    color: "#fff", background: bg, cursor: busy ? "default" : "pointer", opacity: busy ? 0.6 : 1 });
  const Stat = ({ label, val, color }) => (
    <div style={{ flex: "1 1 90px", background: "#FAF9F6", border: `1px solid ${C.border}`, borderRadius: 8, padding: "10px 12px" }}>
      <div style={{ fontSize: 22, fontWeight: 700, color: color || C.text }}>{val ?? 0}</div>
      <div style={{ fontSize: 11, color: C.muted, textTransform: "uppercase" }}>{label}</div>
    </div>
  );

  return (
    <div style={{ background: C.surface, border: `1px solid ${C.border}`, borderRadius: 10, padding: 16, marginBottom: 16 }}>
      <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
        <span style={{ fontWeight: 700, color: C.text }}>Client onboarding</span>
        {msg && <span style={{ fontSize: 12, color: msg.startsWith("⚠️") ? "#B23B3B" : "#2C7A4B" }}>{msg}</span>}
      </div>
      <p style={{ fontSize: 12, color: C.muted, margin: "2px 0 12px" }}>
        Introduce the WhatsApp line to your existing clients in small batches. They reply to opt in and
        share their delivery details — only opted-in clients get future specials.
      </p>

      <div style={{ display: "flex", gap: 8, flexWrap: "wrap", marginBottom: 14 }}>
        <Stat label="Total" val={stats.total} />
        <Stat label="Not sent" val={stats.invited} color="#8A8680" />
        <Stat label="Awaiting reply" val={stats.awaiting_reply} color="#B8860B" />
        <Stat label="In progress" val={stats.in_progress} color="#B8860B" />
        <Stat label="Opted in" val={stats.opted_in} color="#2C7A4B" />
        <Stat label="Complete" val={stats.complete} color="#2C7A4B" />
        <Stat label="Opted out" val={stats.opted_out} color="#B23B3B" />
      </div>

      <div style={{ display: "flex", gap: 8, flexWrap: "wrap", alignItems: "flex-end" }}>
        <label style={{ display: "flex", flexDirection: "column", gap: 4 }}>
          <span style={{ fontSize: 11, color: C.muted, textTransform: "uppercase" }}>Preview to my number</span>
          <input value={test} onChange={(e) => setTest(e.target.value)} placeholder="27821234567" style={field} />
        </label>
        <button disabled={busy || !test} style={btn("#6B7280")}
          onClick={() => send({ test_phone: test }, "Sending test")}>Send test</button>

        <label style={{ display: "flex", flexDirection: "column", gap: 4, marginLeft: 12 }}>
          <span style={{ fontSize: 11, color: C.muted, textTransform: "uppercase" }}>Batch size</span>
          <input type="number" min={1} max={200} value={batch} onChange={(e) => setBatch(e.target.value)} style={{ ...field, width: 90 }} />
        </label>
        <button disabled={busy || !stats.invited} style={btn("var(--accent)")}
          onClick={() => send({ batch_size: Number(batch) }, "Sending batch")}>
          Send next {batch} →
        </button>
      </div>
      <p style={{ fontSize: 11, color: C.muted, marginTop: 10 }}>
        Tip: start small (10–25) on day one so WhatsApp sees healthy engagement, then increase.
      </p>
    </div>
  );
}
