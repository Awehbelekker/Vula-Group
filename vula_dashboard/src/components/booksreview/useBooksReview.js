/**
 * useBooksReview.js — the data behind Money › Books review (18 Nov 2026; vula/api/books_audit.py,
 * vula/commerce/books_review.py). Plain fetch (the installed auth patch adds the login). Owners and
 * managers only: a 403 means this login can't review the books, which the screen says plainly.
 * Every figure is the server's — this file never adds, rounds or works out money.
 */
import { useCallback, useEffect, useState } from "react";
import { VULA_API } from "../../lib/authFetch";

const base = (tenantId) => `${VULA_API}/v1/commerce/${tenantId}/admin`;

async function call(url, opts) {
  const r = await fetch(url, opts).catch(() => null);
  if (!r) return { ok: false, status: 0, data: {} };
  const data = await r.json().catch(() => ({}));
  return { ok: r.ok, status: r.status, data };
}

const send = (method, url, body) => call(url, { method, headers: { "Content-Type": "application/json" },
  body: JSON.stringify(body || {}) });
const post = (url, body) => send("POST", url, body);

/** "2 300.00", "R2,300", "2300,5" → 230000 / 230050 cents, read as written (no arithmetic on floats);
 * null when it isn't an amount. */
export function randToCents(text) {
  const t = String(text ?? "").replace(/[R\s\u00a0]/gi, "");
  if (!t) return null;
  const m = t.match(/^(-?)(\d{1,3}(?:,\d{3})+|\d+)(?:[.,](\d{1,2}))?$/);
  if (!m) return null;
  const whole = m[2].replace(/,/g, "");
  const cents = Number(whole) * 100 + Number((m[3] || "0").padEnd(2, "0"));
  return m[1] ? -cents : cents;
}

/** The server's reason, as it wrote it, or a plain fallback. */
export const reason = (res, fallback) =>
  (typeof res?.data?.detail === "string" && res.data.detail) || fallback;

export function useBooksReview(tenantId) {
  const [review, setReview] = useState(null);
  const [accounts, setAccounts] = useState([]);
  const [checks, setChecks] = useState([]);
  const [state, setState] = useState("loading");     // loading | ready | off | failed

  const reload = useCallback(async () => {
    if (!tenantId) return;
    setState((s) => (s === "ready" ? s : "loading"));
    const res = await call(`${base(tenantId)}/books-audit/review`);
    if (res.status === 403) { setState("off"); return; }
    if (!res.ok) { setState("failed"); return; }
    setReview(res.data);
    setState("ready");
    const [acc, chk] = await Promise.all([call(`${base(tenantId)}/accounts`),
      call(`${base(tenantId)}/books-audit/statements`)]);
    if (acc.ok) setAccounts(acc.data.accounts || []);
    if (chk.ok) setChecks(chk.data.checks || []);
  }, [tenantId]);

  useEffect(() => { reload(); }, [reload]);

  const a = (p) => `${base(tenantId)}/books-audit${p}`;
  const runCheck = () => post(a("/run"));
  const restore = (ids, why) => post(a("/lines/restore"), { ids, reason: why });
  const setAside = (ids, why) => post(a("/lines/set-aside"), { ids, reason: why });
  const transfer = (ids) => post(a("/lines/transfer"), { ids });
  const link = (txnId, target) => post(a(`/lines/${txnId}/link`), target);
  const payBill = (txnId, billIds, accountCode) => post(a(`/lines/${txnId}/pay-bill`),
    { bill_ids: billIds, account_code: accountCode });
  const payInvoices = (txnId, invoiceIds) => post(a(`/lines/${txnId}/pay-invoices`), { invoice_ids: invoiceIds });
  const correctDate = (kind, id, date, why) => post(a(`/documents/${kind}/${id}/date`), { date, reason: why });
  const agree = (auditId, note) => post(a(`/${auditId}/agree`), { note });
  const categorise = (ids, accountCode) => post(`${base(tenantId)}/bank/transactions/bulk-categorize`,
    { txn_ids: ids, account_code: accountCode });
  const paidBack = (expenseId) => post(`${base(tenantId)}/expenses/${expenseId}/status`, { status: "reimbursed" });
  const recordDraft = (invoiceId) => send("PATCH", `${base(tenantId)}/invoices/${invoiceId}`, { status: "sent" });
  const cancelDraft = (invoiceId, why) => post(`${base(tenantId)}/invoices/${invoiceId}/cancel`, { reason: why });
  const check = (id) => call(a(`/statements/${id}`));
  // the documents behind the money (19 Nov 2026, vula/commerce/doc_audit.py)
  const docFigures = (id, changes, why) => post(a(`/docs/${id}/figures`), { changes, reason: why });
  const docConfirm = (id, note) => post(a(`/docs/${id}/confirm`), { note });
  const docBook = (id) => post(a(`/docs/${id}/book`));
  const docNotABill = (id, why) => post(a(`/docs/${id}/not-a-bill`), { reason: why });
  const docCertificate = (id) => post(a(`/docs/${id}/certificate`));
  // proofs of payment the business sent, linked to the supplier's bill(s) (20 Nov 2026, pop_links.py)
  const popLink = (id, billIds) => post(a(`/pops/${id}/link`), { bill_ids: billIds });
  const popDismiss = (id, why) => post(a(`/pops/${id}/dismiss`), { reason: why });
  const docAddToInvoices = (id, txnId) => post(a(`/docs/${id}/add-to-invoices`), { txn_id: txnId || null });
  const docLinkInvoice = (id, invoiceId) => post(a(`/docs/${id}/link-invoice`), { of_id: invoiceId });
  const docDuplicate = (id, ofId) => post(a(`/docs/${id}/duplicate`), { of_id: ofId });
  const docVatBasis = (id, basis) => post(a(`/docs/${id}/vat-basis`), { basis });
  const billCancelDuplicate = (id, ofId, why) => post(a(`/bills/${id}/cancel-duplicate`), { of_id: ofId, reason: why });
  const billMatchDocument = (id, why) => post(a(`/bills/${id}/match-document`), { reason: why });
  const mergeSuppliers = (keepId, ids) => post(`${base(tenantId)}/suppliers/merge`, { keep_id: keepId, duplicate_ids: ids });
  const apply = (id, accept) => post(a(`/statements/${id}/apply`), { accept });
  /** Every waiting statement checked again and its safe changes applied (25 Nov 2026); confirm=false = what it would do. */
  const applyAll = (confirm) => post(a("/statements/apply-all"), { confirm });

  /** Upload one official statement PDF — compared only; nothing changes until it's applied. */
  async function upload(file) {
    const fd = new FormData();
    fd.append("file", file);
    return call(a("/statements"), { method: "POST", body: fd });
  }

  return { review, accounts, checks, state, reload, runCheck, restore, setAside, transfer, link, correctDate,
    agree, categorise, payBill, payInvoices, paidBack, recordDraft, cancelDraft, upload, check, apply, applyAll, docFigures, docConfirm, docBook,
    docNotABill, docCertificate, popLink, popDismiss, docAddToInvoices, docLinkInvoice, docDuplicate, docVatBasis, billCancelDuplicate, billMatchDocument, mergeSuppliers };
}
