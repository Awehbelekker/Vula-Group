/**
 * useBooks.js — Accounts (the books): P&L by category, the VAT summary, and the ledger export
 * (2026-10-19, UX redesign Phase F). Shared by VulaAccounting.jsx and books/BooksV2.jsx; moved out
 * unchanged — same /admin/reports/* endpoints (migration 058). Every figure is integer cents
 * computed on the server; the screen only formats them.
 *
 * 18 Nov 2026: a business on the one book (books_v2) gets its reports from the journal, which
 * says so (`source: "journal"`); it can then be read on the accrual or the cash view (`basis`),
 * remembered per viewer. VAT always follows the business's own VAT basis (the server says which).
 */
import { useCallback, useEffect, useState } from "react";
import { VULA_API } from "../../lib/authFetch";
import { tryApi } from "../../lib/api";
import { toast } from "../ui/index.jsx";

const BASIS_KEY = "vula.books.basis";
const readBasis = () => { try { return localStorage.getItem(BASIS_KEY) === "cash" ? "cash" : "accrual"; } catch { return "accrual"; } };

export function useBooks(tenantId) {
  const [since, setSince] = useState("");
  const [until, setUntil] = useState("");
  const [pnl, setPnl] = useState(null);
  const [vat, setVat] = useState(null);
  const [error, setError] = useState("");
  const [basis, setBasisState] = useState(readBasis);
  const setBasis = (b) => { setBasisState(b); try { localStorage.setItem(BASIS_KEY, b); } catch { /* private window */ } };
  const fromBooks = pnl?.source === "journal";

  const qs = (withBasis = fromBooks) => {
    const p = new URLSearchParams(); if (since) p.set("since", since); if (until) p.set("until", until);
    if (withBasis) p.set("basis", basis);
    return p.toString();
  };

  const load = useCallback(async () => {
    const q = new URLSearchParams(); if (since) q.set("since", since); if (until) q.set("until", until);
    const pq = new URLSearchParams(q); pq.set("basis", basis);     // ignored by a business not on the one book
    const [p, v] = await Promise.all([
      tryApi(`/v1/commerce/${tenantId}/admin/reports/pnl?${pq}`),
      tryApi(`/v1/commerce/${tenantId}/admin/reports/vat?${q}`),
    ]);
    // A refused or failed report is left out (no R0.00 tiles) and said once, not shown as zero.
    setPnl(p.ok ? p.data : null); setVat(v.ok ? v.data : null);
    const failed = !p.ok ? p : !v.ok ? v : null;
    setError(failed ? failed.error : "");
    if (failed) toast(`Couldn't load your books: ${failed.error}`, "danger");
  }, [tenantId, since, until, basis]);
  useEffect(() => { load(); }, [load]);

  // fetched (not a plain link) so the signed-in token goes with it — the route is member-only
  const exportLedger = async () => {
    try {
      const r = await fetch(`${VULA_API}/v1/commerce/${tenantId}/admin/reports/ledger.csv?${qs()}`);
      if (!r.ok) throw new Error(String(r.status));
      const url = URL.createObjectURL(await r.blob());
      const a = document.createElement("a");
      a.href = url; a.download = `ledger_${since}_${until}${fromBooks ? `_${basis}` : ""}.csv`; a.click();
      setTimeout(() => URL.revokeObjectURL(url), 1000);
    } catch { toast("Couldn't export the ledger — please try again.", "danger"); }
  };

  return { since, setSince, until, setUntil, pnl, vat, exportLedger, basis, setBasis, fromBooks, error };
}
