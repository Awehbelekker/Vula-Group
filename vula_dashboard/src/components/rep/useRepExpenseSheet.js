/**
 * useRepExpenseSheet.js — the rep's monthly expense-claim sheet (shared by VulaRepExpenseSheet
 * and RepExpenseSheetV2): schedule + budget config and this month's spend, in integer cents
 * from the server. Moved verbatim out of VulaRepExpenseSheet.jsx.
 */
import { useState, useEffect, useCallback } from "react";
import { VULA_API } from "../../lib/authFetch";

export function useRepExpenseSheet(tenantId, repPhone) {
  const [config, setConfig] = useState({});
  const [mtdCents, setMtdCents] = useState(0);
  const [loaded, setLoaded] = useState(false);
  const [msg, setMsg] = useState("");

  const load = useCallback(async () => {
    if (!tenantId || !repPhone) return;
    const r = await fetch(`${VULA_API}/v1/commerce/${tenantId}/admin/expense-sheet?rep_phone=${encodeURIComponent(repPhone)}`);
    const d = await r.json().catch(() => ({}));
    setConfig(d.config || {});
    setMtdCents(d.mtd_spend_cents || 0);
    setLoaded(true);
  }, [tenantId, repPhone]);
  useEffect(() => { load(); }, [load]);

  const saveConfig = async (patch) => {
    setMsg("");
    const r = await fetch(`${VULA_API}/v1/commerce/${tenantId}/admin/expense-sheet/configure`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ rep_phone: repPhone, ...patch }),
    });
    const d = await r.json().catch(() => ({}));
    if (d.saved) load(); else setMsg(d.detail || "Could not save.");
  };

  const budgetCents = config.expense_budget_cents || 0;
  const pct = budgetCents ? Math.min(100, Math.round((mtdCents / budgetCents) * 100)) : 0;
  return { config, mtdCents, loaded, msg, saveConfig, budgetCents, pct };
}
