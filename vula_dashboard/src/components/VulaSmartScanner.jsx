/**
 * VulaSmartScanner.jsx — AI Smart Scanner
 *
 * The flagship workflow-simplifier: photograph ANY business document and
 * AI extracts structured data, then one tap turns it into an action.
 *
 *   📷 Snap a supplier receipt  → auto-creates an EXPENSE
 *   📦 Snap a delivery note     → lines matched to products on the server, reviewed, then
 *                                 booked into STOCK (recorded as 'receive' movements)
 *   💲 Snap a supplier price list → proposes COST PRICE updates for matched products
 *   🧾 Snap an invoice           → pre-fills an INVOICE
 *   📝 Snap a handwritten order  → pre-fills an ORDER
 *
 * Flow: capture/upload photo → POST /admin/scan → review extracted JSON →
 * confirm → POST to the relevant endpoint (expense / product stock / invoice).
 * Data and actions: scanner/useSmartScanner.js (shared with the redesign's ScannerV2, shown when
 * the caller passes v2).
 */

import ScannerV2 from './scanner/ScannerV2'

export default function VulaSmartScanner(props) {
  return <ScannerV2 {...props} />;
}

