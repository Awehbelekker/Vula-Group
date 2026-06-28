/**
 * VulaFollowups.jsx — emails awaiting a reply. Vula flags inbound questions/requests/
 * scheduling and nudges on WhatsApp daily until they're handled.
 */
import { useState, useEffect, useCallback } from "react";

const VULA_API = import.meta.env.VITE_API_URL || "https://vula-group-production.up.railway.app";
const C = { surface: "#FFFFFF", border: "#DDD8CE", green: "var(--accent)", text: "#2A2A2A", muted: "#8A8680", alt: "#F0EDE5" };
const REASON = { schedule: { label: "📅 Schedule", color: "#2B5797" }, request: { label: "✋ Request", color: "#C4861A" }, question: { label: "❓ Question", color: "#6B5B95" } };

export default function VulaFollowups({ tenantId }) {
  const [rows, setRows] = useState([]);
  const load = useCallback(async () => {
    if (!tenantId) return;
    const r = await fetch(`${VULA_API}/v1/email/followups/${tenantId}?status=open`);
    const d = await r.json();
    setRows(d.followups || []);
  }, [tenantId]);
  useEffect(() => { load(); }, [load]);

  const setStatus = async (id, status) => {
    await fetch(`${VULA_API}/v1/email/followups/${tenantId}/${id}`, {
      method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ status }),
    });
    load();
  };
  const days = (t) => t ? Math.floor((Date.now() - new Date(t)) / 86400000) : 0;

  return (
    <div style={{ maxWidth: 920, margin: "0 auto", padding: 24 }}>
      <h1 style={{ fontFamily: "'Cormorant Garamond', serif", fontSize: 28, fontWeight: 700, color: C.text, margin: "0 0 2px" }}>Follow-ups</h1>
      <p style={{ fontSize: 13, color: C.muted, margin: "0 0 18px" }}>Emails Vula thinks are waiting on you — you'll get a WhatsApp nudge daily until they're cleared.</p>

      <div style={{ background: C.surface, border: `1px solid ${C.border}`, borderRadius: 10, overflow: "hidden" }}>
        {rows.length === 0 && <div style={{ padding: 18, fontSize: 13, color: C.muted }}>Nothing waiting — you're all caught up. 🎉</div>}
        {rows.map((r) => {
          const d = days(r.received_at);
          const re = REASON[r.reason] || { label: r.reason || "", color: C.muted };
          return (
            <div key={r.id} style={{ padding: "12px 16px", borderTop: `1px solid ${C.alt}`, display: "flex", gap: 12, alignItems: "flex-start" }}>
              <div style={{ flex: 1 }}>
                <div style={{ display: "flex", gap: 8, alignItems: "center", marginBottom: 3 }}>
                  <span style={{ fontWeight: 600, color: C.text, fontSize: 13.5 }}>{r.sender_name || r.sender}</span>
                  <span style={{ fontSize: 10.5, color: re.color, fontWeight: 600 }}>{re.label}</span>
                  {d >= 2 && <span style={{ fontSize: 10.5, color: "#A23B2D", fontWeight: 600 }}>{d}d waiting</span>}
                </div>
                <div style={{ fontSize: 13, color: C.text }}>{r.subject || "(no subject)"}</div>
                <div style={{ fontSize: 12, color: C.muted, marginTop: 2 }}>{(r.preview || "").slice(0, 110)}</div>
              </div>
              <div style={{ display: "flex", flexDirection: "column", gap: 5 }}>
                <button onClick={() => setStatus(r.id, "done")} style={{ padding: "5px 10px", background: C.green, color: "#fff", border: "none", borderRadius: 6, fontSize: 11.5, cursor: "pointer" }}>Done</button>
                <button onClick={() => setStatus(r.id, "snoozed")} style={{ padding: "5px 10px", background: C.surface, color: C.muted, border: `1px solid ${C.border}`, borderRadius: 6, fontSize: 11.5, cursor: "pointer" }}>Dismiss</button>
              </div>
            </div>
          );
        })}
      </div>
      <p style={{ textAlign: "center", fontSize: 11, color: "#B5B0A8", marginTop: 20 }}>Powered by Vula · checked every sync</p>
    </div>
  );
}
