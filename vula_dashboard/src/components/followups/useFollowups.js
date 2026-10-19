/**
 * useFollowups.js — emails awaiting a reply (2026-10-18, UX redesign Phase F). Shared by
 * VulaFollowups.jsx and followups/FollowupsV2.jsx; moved out unchanged.
 */
import { useCallback, useEffect, useState } from "react";
import { VULA_API } from "../../lib/authFetch";

export const daysWaiting = (t) => (t ? Math.floor((Date.now() - new Date(t)) / 86400000) : 0);

export function useFollowups(tenantId) {
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
  return { rows, setStatus };
}
