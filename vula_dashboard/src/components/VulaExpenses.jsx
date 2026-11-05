/**
 * VulaExpenses.jsx — 💸 Expense claims: every business spend / out-of-pocket claim in one place.
 * Who paid, what for, VAT, which project, and reimbursement status (submitted → approved →
 * reimbursed). Receipts snapped on WhatsApp land here automatically; add manual ones too.
 * Backend: /v1/commerce/{tenant}/admin/expenses (+ /assign, /{id}/status, /reports/expenses).
 * Data and actions live in expenses/useExpenses.js (shared with expenses/ExpensesV2.jsx).
 */
import ExpensesV2 from "./expenses/ExpensesV2";

/** v2: the redesigned screen (same hook, so the same data and actions). */
export default function VulaExpenses({ tenantId, defaultPaidBy }) {
  return <ExpensesV2 tenantId={tenantId} defaultPaidBy={defaultPaidBy} />;
}

