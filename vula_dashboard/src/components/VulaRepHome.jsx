/**
 * VulaRepHome.jsx — the sales rep's landing page: due reminders, this week's call sheet status,
 * a nudge into the other My Work tabs. Replaces the tenant-wide owner overview for a restricted
 * sales_rep dashboard login (see VulaMerchantAdmin.jsx's overview branch).
 */
import VulaRepToday from './VulaRepToday'
import { useState, useEffect, useCallback } from "react";
import { VULA_API } from "../lib/authFetch";

const C = { surface: "var(--surface)", border: "var(--border)", green: "var(--accent)", text: "var(--text)", muted: "var(--muted)" };

export default function VulaRepHome({ tenantId, repPhone, onNavigate }) {
  const [reminders, setReminders] = useState(null);   // null = still loading
  const [callSheet, setCallSheet] = useState(null);

  const load = useCallback(async () => {
    if (!tenantId || !repPhone) return;
    const rParams = new URLSearchParams({ created_by: repPhone, status: "open" });
    fetch(`${VULA_API}/v1/commerce/${tenantId}/admin/reminders?${rParams}`)
      .then((r) => r.json()).then((d) => setReminders(d.reminders || [])).catch(() => setReminders([]));
    fetch(`${VULA_API}/v1/commerce/${tenantId}/admin/call-sheet?rep_phone=${encodeURIComponent(repPhone)}`)
      .then((r) => r.json()).then((d) => setCallSheet(d)).catch(() => {});
  }, [tenantId, repPhone]);
  useEffect(() => { load(); }, [load]);

  const list = reminders || [];
  const overdue = list.filter((r) => r.due_at && new Date(r.due_at) < new Date());
  const upcoming = list.filter((r) => !r.due_at || new Date(r.due_at) >= new Date());

  // A real button when it goes somewhere (keyboard + screen reader reachable), a plain card otherwise.
  const Card = ({ title, children, onClick }) => {
    const style = { display: "block", width: "100%", textAlign: "left", font: "inherit", color: "inherit",
      background: C.surface, border: `1px solid ${C.border}`, borderRadius: "var(--r-card)",
      padding: 16, marginBottom: 14, cursor: onClick ? "pointer" : "default" };
    const body = <><div style={{ fontWeight: 700, color: C.text, marginBottom: 8 }}>{title}</div>{children}</>;
    return onClick ? <button type="button" onClick={onClick} style={style}>{body}</button> : <div style={style}>{body}</div>;
  };

  return (
    <div style={{ maxWidth: 700, margin: "0 auto", padding: 20 }}>
      <h2 className="vula-display" style={{ fontSize: 22, fontWeight: 600, color: "var(--ink)", margin: "0 0 4px" }}>Today</h2>
      <p style={{ fontSize: 13, color: C.muted, margin: "0 0 18px" }}>Your own contacts, call sheet, reminders, and bookings — nothing tenant-wide.</p>

      <VulaRepToday tenantId={tenantId} repPhone={repPhone} />

      <Card title={reminders === null ? "⏰ Reminders" : `⏰ Reminders (${list.length} open)`} onClick={() => onNavigate?.("my-work", "rep-reminders")}>
        {overdue.length > 0 && <div style={{ fontSize: 13, color: "var(--danger)", marginBottom: 4 }}>{overdue.length} overdue</div>}
        {reminders === null
          ? <div style={{ fontSize: 13, color: C.muted }}>Loading…</div>
          : list.length === 0
          ? <div style={{ fontSize: 13, color: C.muted }}>Nothing due — nice.</div>
          : list.slice(0, 3).map((r) => (
              <div key={r.id} style={{ fontSize: 13, color: C.text, padding: "3px 0" }}>• {r.text}</div>
            ))}
      </Card>

      <Card title="📋 Call Sheet" onClick={() => onNavigate?.("my-work", "rep-callsheet")}>
        {callSheet
          ? <>
              <div style={{ fontSize: 13, color: C.text }}>{(callSheet.entries || []).length} entr{(callSheet.entries || []).length === 1 ? "y" : "ies"} logged so far</div>
              {callSheet.config?.call_sheet_recipient_email
                ? <div style={{ fontSize: 12, color: C.muted }}>Goes to {callSheet.config.call_sheet_recipient_email}</div>
                : <div style={{ fontSize: 12, color: "var(--danger)" }}>No recipient configured yet</div>}
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
