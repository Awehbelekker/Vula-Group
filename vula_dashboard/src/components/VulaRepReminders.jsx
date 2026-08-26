/**
 * VulaRepReminders.jsx — a sales rep's own reminders (vula_reminders, created_by-scoped).
 * First dashboard surface for vula_reminders at all — previously WhatsApp-only.
 */
import { useState, useEffect, useCallback } from "react";

const VULA_API = import.meta.env.VITE_API_URL || "https://vula-group-production.up.railway.app";
const C = { surface: "#FFFFFF", border: "#DDD8CE", green: "var(--accent)", text: "#2A2A2A", muted: "#8A8680" };

export default function VulaRepReminders({ tenantId, repPhone }) {
  const [reminders, setReminders] = useState([]);
  const [status, setStatus] = useState("open");

  const load = useCallback(async () => {
    if (!tenantId) return;
    const params = new URLSearchParams({ created_by: repPhone || "", status });
    const r = await fetch(`${VULA_API}/v1/commerce/${tenantId}/admin/reminders?${params}`);
    const d = await r.json().catch(() => ({}));
    setReminders(d.reminders || []);
  }, [tenantId, repPhone, status]);
  useEffect(() => { load(); }, [load]);

  const complete = async (id) => {
    await fetch(`${VULA_API}/v1/commerce/${tenantId}/admin/reminders/${id}`, { method: "PATCH" });
    load();
  };

  const isOverdue = (r) => r.due_at && new Date(r.due_at) < new Date() && r.status === "open";

  return (
    <div style={{ maxWidth: 700, margin: "0 auto", padding: 20 }}>
      <h2 style={{ fontSize: 20, fontWeight: 700, color: C.text, margin: "0 0 4px" }}>My Reminders</h2>
      <p style={{ fontSize: 13, color: C.muted, margin: "0 0 16px" }}>
        Set via WhatsApp ("remind me to follow up with X Friday") — logged meetings also create
        one automatically per action item.
      </p>

      <div style={{ display: "flex", gap: 6, marginBottom: 14 }}>
        {["open", "done", "all"].map((s) => (
          <button key={s} onClick={() => setStatus(s)}
            style={{ padding: "5px 12px", borderRadius: 14, fontSize: 12, cursor: "pointer",
              border: `1px solid ${status === s ? C.green : C.border}`,
              background: status === s ? C.green : C.surface, color: status === s ? "#fff" : C.muted }}>
            {s === "open" ? "Open" : s === "done" ? "Done" : "All"}
          </button>
        ))}
      </div>

      {reminders.length === 0 && <div style={{ padding: 16, fontSize: 13, color: C.muted, background: C.surface, border: `1px solid ${C.border}`, borderRadius: 10 }}>Nothing here.</div>}
      {reminders.map((r) => (
        <div key={r.id} style={{ display: "flex", alignItems: "center", gap: 10, background: C.surface,
          border: `1px solid ${isOverdue(r) ? "#E6A23C" : C.border}`, borderRadius: 10, padding: "10px 14px", marginBottom: 8 }}>
          <div style={{ flex: 1 }}>
            <div style={{ fontSize: 13.5, color: C.text }}>{r.text}</div>
            {r.due_at && <div style={{ fontSize: 11.5, color: isOverdue(r) ? "#A23B2D" : C.muted }}>
              Due {new Date(r.due_at).toLocaleDateString()}{isOverdue(r) ? " — overdue" : ""}
            </div>}
          </div>
          {r.status === "open" && (
            <button onClick={() => complete(r.id)} style={{ fontSize: 11.5, color: C.muted, background: "none",
              border: `1px solid ${C.border}`, borderRadius: 6, padding: "4px 10px", cursor: "pointer" }}>✓ Done</button>
          )}
        </div>
      ))}
    </div>
  );
}
