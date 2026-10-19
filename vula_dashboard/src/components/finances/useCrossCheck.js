/**
 * useCrossCheck.js — documents ↔ books ↔ bank, and VAT in vs out (2026-10-19, UX redesign
 * Phase F). Shared by VulaCrossCheck.jsx and finances/CrossCheckV2.jsx; moved out unchanged —
 * same /admin/crosscheck, /reports/vat-scenario, /crosscheck/book-unbooked and bank match calls
 * (vula/commerce/cross_check.py).
 */
import { useCallback, useEffect, useState } from "react";
import { VULA_API } from "../../lib/authFetch";

export function useCrossCheck(tenantId) {
  const [rep, setRep] = useState(null);
  const [vat, setVat] = useState(null);
  const [open, setOpen] = useState("");
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState("");
  const base = `${VULA_API}/v1/commerce/${tenantId}/admin`;

  const [loaded, setLoaded] = useState(false);
  const load = useCallback(async () => {
    if (!tenantId) return;
    const [a, b] = await Promise.all([
      fetch(`${base}/crosscheck`).then(r => (r.ok ? r.json() : null)).catch(() => null),
      fetch(`${base}/reports/vat-scenario`).then(r => (r.ok ? r.json() : null)).catch(() => null),
    ]);
    setRep(a); setVat(b); setLoaded(true);
  }, [tenantId, base]);
  useEffect(() => { load(); }, [load]);

  const toggle = (k) => setOpen(open === k ? "" : k);
  const bookAll = async () => {
    setBusy(true);
    const r = await fetch(`${base}/crosscheck/book-unbooked`, { method: "POST" }).then(r => r.json()).catch(() => ({}));
    setBusy(false); setMsg(r.detail || `Booked ${r.booked ?? 0}; ${r.skipped ?? 0} couldn't be.`); load();
  };
  const confirm = async (txnId, invoiceId) => {
    await fetch(`${base}/bank/transactions/${txnId}/match`, { method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ action: "match", invoice_id: invoiceId }) }).catch(() => {});
    load();
  };

  return { rep, vat, open, toggle, busy, msg, loaded, load, bookAll, confirm };
}
