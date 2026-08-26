/**
 * VulaRepHome.jsx — the sales rep's landing page: due reminders, this week's call sheet status,
 * a nudge into the other My Work tabs. Replaces the tenant-wide owner overview for a restricted
 * sales_rep dashboard login (see VulaMerchantAdmin.jsx's overview branch).
 */
import { useState, useEffect, useCallback } from "react";

const VULA_API = import.meta.env.VITE_API_URL || "https://vula-group-production.up.railway.app";
const C = { surface: "#FFFFFF", border: "#DDD8CE", green: "var(--accent)", text: "#2A2A2A", muted: "#8A8680" };

export default function VulaRepHome({ tenantId, repPhone, onNavigate }) {
  const [reminders, setReminders] = useState([]);
  const [callSheet, setCallSheet] = useState(null);

  const load = useCallback(async () => {
    if (!tenantId || !repPhone) return;
    const rParams = new URLSearchParams({ created_by: repPhone, status: "open" });
    fetch(`${VULA_API}/v1/commerce/${tenantId}/admin/reminders?${rParams}`)
      .then((r) => r.json()).then((d) => setReminders(d.reminders || [])).catch(() => {});
    fetch(`${VULA_API}/v1/commerce/${tenantId}/admin/call-sheet?rep_phone=${encodeURIComponent(repPhone)}`)
      .then((r) => r.json()).then((d) => setCallSheet(d)).catch(() => {});
  }, [tenantId, repPhone]);
  useEffect(() => { load(); }, [load]);

  const overdue = reminders.filter((r) => r.due_at && new Date(r.due_at) < new Date());
  const upcoming = reminders.filter((r) => !r.due_at || new Date(r.due_at) >= new Date());

  const Card = ({ title, children, onClick }) => (
    <div onClick={onClick} style={{ background: C.surface, border: `1px solid ${C.border}`, borderRadius: 10,
      padding: 16, marginBottom: 14, cursor: onClick ? "pointer" : "default" }}>
      <div style={{ fontWeight: 700, color: C.text, marginBottom: 8 }}>{title}</div>
      {children}
    </div>
  );

  return (
    <div style={{ maxWidth: 700, margin: "0 auto", padding: 20 }}>
      <h1 style={{ fontSize: 24, fontWeight: 700, color: C.text, margin: "0 0 4px" }}>My Work</h1>
      <p style={{ fontSize: 13, color: C.muted, margin: "0 0 18px" }}>Your own contacts, call sheet, reminders, and bookings — nothing tenant-wide.</p>

      <Card title={`⏰ Reminders (${reminders.length} open)`} onClick={() => onNavigate?.("my-work", "rep-reminders")}>
        {overdue.length > 0 && <div style={{ fontSize: 13, color: "#A23B2D", marginBottom: 4 }}>{overdue.length} overdue</div>}
        {reminders.length === 0
          ? <div style={{ fontSize: 13, color: C.muted }}>Nothing due — nice.</div>
          : reminders.slice(0, 3).map((r) => (
              <div key={r.id} style={{ fontSize: 13, color: C.text, padding: "3px 0" }}>• {r.text}</div>
            ))}
      </Card>

      <Card title="📋 Call Sheet" onClick={() => onNavigate?.("my-work", "rep-callsheet")}>
        {callSheet
          ? <>
              <div style={{ fontSize: 13, color: C.text }}>{(callSheet.entries || []).length} entr{(callSheet.entries || []).length === 1 ? "y" : "ies"} logged so far</div>
              {callSheet.config?.call_sheet_recipient_email
                ? <div style={{ fontSize: 12, color: C.muted }}>Goes to {callSheet.config.call_sheet_recipient_email}</div>
                : <div style={{ fontSize: 12, color: "#A23B2D" }}>No recipient configured yet</div>}
            </>
          : <div style={{ fontSize: 13, color: C.muted }}>Loading…</div>}
      </Card>

      <Card title="📅 Bookings" onClick={() => onNavigate?.("my-work", "rep-bookings")}>
        <div style={{ fontSize: 13, color: C.muted }}>View today's and this week's bookings →</div>
      </Card>

      <div style={{ display: "flex", gap: 8, flexWrap: "wrap", marginTop: 4 }}>
        {[["rep-contacts", "📇 Contacts"], ["rep-documents", "📂 Documents"], ["rep-expenses", "💸 Expenses"], ["rep-crm", "🔗 Dynamics 365"]].map(([id, label]) => (
          <button key={id} onClick={() => onNavigate?.("my-work", id)}
            style={{ padding: "8px 14px", background: C.surface, border: `1px solid ${C.border}`, borderRadius: 8,
              fontSize: 13, color: C.text, cursor: "pointer" }}>{label}</button>
        ))}
      </div>
    </div>
  );
}
