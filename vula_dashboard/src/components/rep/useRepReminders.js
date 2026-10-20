/**
 * useRepReminders.js — a sales rep's own reminders (shared by VulaRepReminders and
 * RepRemindersV2). Moved verbatim out of VulaRepReminders.jsx.
 */
import { useState, useEffect, useCallback } from "react";
import { VULA_API } from "../../lib/authFetch";

export function useRepReminders(tenantId, repPhone) {
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
  return { reminders, status, setStatus, complete, isOverdue };
}
