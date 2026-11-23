/**
 * useRepReminders.js — a sales rep's own reminders (shared by VulaRepReminders and
 * RepRemindersV2). Moved verbatim out of VulaRepReminders.jsx.
 */
import { useState, useEffect, useCallback } from "react";
import { tryApi } from "../../lib/api";
import { toast } from "../ui/index.jsx";

export function useRepReminders(tenantId, repPhone) {
  const [reminders, setReminders] = useState([]);
  const [status, setStatus] = useState("open");

  const load = useCallback(async () => {
    if (!tenantId) return;
    const params = new URLSearchParams({ created_by: repPhone || "", status });
    // 23 Nov 2026 audit: a failed load showed "You're all caught up"
    const { ok, data: d, error } = await tryApi(`/v1/commerce/${tenantId}/admin/reminders?${params}`);
    if (!ok) { toast(`Your reminders couldn't be loaded: ${error}`, "danger"); return; }
    setReminders(d.reminders || []);
  }, [tenantId, repPhone, status]);
  useEffect(() => { load(); }, [load]);

  const complete = async (id) => {
    const { ok, error } = await tryApi(`/v1/commerce/${tenantId}/admin/reminders/${id}`, { method: "PATCH" });
    if (!ok) toast(`That reminder wasn't marked done: ${error}`, "danger");
    load();
  };

  const isOverdue = (r) => r.due_at && new Date(r.due_at) < new Date() && r.status === "open";
  return { reminders, status, setStatus, complete, isOverdue };
}
