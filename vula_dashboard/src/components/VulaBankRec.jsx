/**
 * VulaBankRec.jsx — bank reconciliation from the emailed statement (move off Xero, no API).
 * Upload/auto-ingest a Capitec statement → transactions auto-match to invoices (paid) + expenses.
 * Backend: /v1/commerce/{tenant}/admin/bank/* (migration 057).
 */
import BankV2 from "./bank/BankV2";

export default function VulaBankRec({ tenantId }) {
  return <BankV2 tenantId={tenantId} />;
}

