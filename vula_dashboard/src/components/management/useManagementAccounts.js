/**
 * useManagementAccounts.js — the data behind Money › Management accounts (23 Nov 2026;
 * vula_mind/vula/commerce/management_pack.py). Plain fetch (the installed auth patch adds the login).
 * Every figure is the server's — this file never adds, rounds or works out money.
 */
import { useCallback, useEffect, useState } from "react";
import { VULA_API } from "../../lib/authFetch";

const MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August", "September",
  "October", "November", "December"];

export const reportUrl = (tenantId, kind, month) =>
  `${VULA_API}/v1/commerce/${tenantId}/admin/reports/management${kind ? `.${kind}` : ""}` +
  (month ? `?month=${encodeURIComponent(month)}` : "");

/** "2026-10" → "October 2026". */
export const monthLabel = (ym) => {
  const [y, m] = String(ym || "").split("-").map(Number);
  return y && m ? `${MONTHS[m - 1]} ${y}` : String(ym || "");
};

/** Every month from the business's first trading month to this one, newest first. */
export function monthsFrom(first, today = new Date()) {
  const out = [];
  let [y, m] = String(first || "").split("-").map(Number);
  if (!y || !m) return out;
  const endY = today.getFullYear(), endM = today.getMonth() + 1;
  while (y < endY || (y === endY && m <= endM)) {
    out.push(`${y}-${String(m).padStart(2, "0")}`);
    [y, m] = m === 12 ? [y + 1, 1] : [y, m + 1];
    if (out.length > 240) break;
  }
  return out.reverse();
}

export function useManagementAccounts(tenantId) {
  const [month, setMonth] = useState("");          // "" = the last full month (the server's default)
  const [data, setData] = useState(null);
  const [state, setState] = useState("loading");   // loading | ready | off | empty | failed
  const [why, setWhy] = useState("");

  const load = useCallback(async () => {
    if (!tenantId) return;
    setState("loading");
    const r = await fetch(reportUrl(tenantId, "", month)).catch(() => null);
    if (!r) { setState("failed"); return; }
    const d = await r.json().catch(() => ({}));
    if (r.status === 403) { setState("off"); return; }
    if (r.status === 400) { setWhy(typeof d.detail === "string" ? d.detail : ""); setState("empty"); return; }
    if (!r.ok) { setState("failed"); return; }
    setData(d);
    setState("ready");
  }, [tenantId, month]);

  useEffect(() => { load(); }, [load]);

  return { month: month || data?.period?.month || "", setMonth, data, state, why, reload: load };
}
