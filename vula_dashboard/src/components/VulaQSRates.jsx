/**
 * VulaQSRates.jsx — the tenant's own Quantity Surveying rate library.
 *
 * DIGG's owned rates (walling, acoustic panels, finishes, fees). The assistant's
 * calculations skill computes costs from THESE rates (via lookup_rate), never from
 * market figures it would guess.
 *
 * 2026-09-28: plus "Learned from your documents" — every priced line on the tenant's filed
 * invoices, quotes and BOQs and its casual workers' day rates, rolled up per item
 * (vula/commerce/price_book.py). Own rates always win; when the documents have moved more
 * than 10% from an own rate, it's flagged with an "Update" button — nothing changes by itself.
 */
import QSRatesV2 from "./construction/QSRatesV2";

export default function VulaQSRates({ ...props }) {
  return <QSRatesV2 {...props} />;
}
