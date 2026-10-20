/**
 * useRepHome.js — the sales rep's landing page data (shared by VulaRepHome and RepHomeV2): their
 * own open reminders and this week's call sheet. Moved verbatim out of VulaRepHome.jsx.
 */
import { useState, useEffect, useCallback } from "react";
import { VULA_API } from "../../lib/authFetch";

export function useRepHome(tenantId, repPhone) {
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
  return { reminders, callSheet, list, overdue, upcoming, load };
}
