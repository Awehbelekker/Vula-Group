/**
 * useBooks.js — Accounts (the books): P&L by category, the VAT summary, and the ledger export
 * (2026-10-19, UX redesign Phase F). Shared by VulaAccounting.jsx and books/BooksV2.jsx; moved out
 * unchanged — same /admin/reports/* endpoints (migration 058). Every figure is integer cents
 * computed on the server; the screen only formats them.
 */
import { useCallback, useEffect, useState } from "react";
import { VULA_API } from "../../lib/authFetch";
import { toast } from "../ui/index.jsx";

export function useBooks(tenantId) {
  const [since, setSince] = useState("");
  const [until, setUntil] = useState("");
  const [pnl, setPnl] = useState(null);
  const [vat, setVat] = useState(null);

  const qs = () => { const p = new URLSearchParams(); if (since) p.set("since", since); if (until) p.set("until", until); return p.toString(); };

  const load = useCallback(async () => {
    const q = new URLSearchParams(); if (since) q.set("since", since); if (until) q.set("until", until);
    const [p, v] = await Promise.all([
      fetch(`${VULA_API}/v1/commerce/${tenantId}/admin/reports/pnl?${q}`).then(r => r.json()).catch(() => null),
      fetch(`${VULA_API}/v1/commerce/${tenantId}/admin/reports/vat?${q}`).then(r => r.json()).catch(() => null),
    ]);
    setPnl(p); setVat(v);
  }, [tenantId, since, until]);
  useEffect(() => { load(); }, [load]);

  // fetched (not a plain link) so the signed-in token goes with it — the route is member-only
  const exportLedger = async () => {
    try {
      const r = await fetch(`${VULA_API}/v1/commerce/${tenantId}/admin/reports/ledger.csv?${qs()}`);
      if (!r.ok) throw new Error(String(r.status));
      const url = URL.createObjectURL(await r.blob());
      const a = document.createElement("a");
      a.href = url; a.download = `ledger_${since}_${until}.csv`; a.click();
      setTimeout(() => URL.revokeObjectURL(url), 1000);
    } catch { toast("Couldn't export the ledger — please try again.", "danger"); }
  };

  return { since, setSince, until, setUntil, pnl, vat, exportLedger };
}
