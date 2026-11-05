/**
 * VulaAccounting.jsx — 📒 Books: P&L by category, VAT summary (if registered), and a
 * general-ledger export her accountant can file from. Built from the reconciled bank ledger.
 * Backend: /v1/commerce/{tenant}/admin/reports/* (migration 058).
 */
import BooksV2 from "./books/BooksV2";

export default function VulaAccounting({ tenantId }) {
  return <BooksV2 tenantId={tenantId} />;
}

