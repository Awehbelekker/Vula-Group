/**
 * VulaStock.jsx — the Stock tab: count, receive and adjust stock by scanning barcodes with the
 * phone camera.
 *
 *   📋 Stock-take  → start a count, scan every item (each scan adds one, or type a number),
 *                    review counted vs system with the difference in rands, owner/manager applies
 *   📦 Receive     → pick a purchase order (or none), scan what arrived, book it into stock
 *   ✏️ Quick adjust → scan one item, see its stock and history, set or add to it
 *
 * Camera: the browser's BarcodeDetector (Android Chrome) or, where it's missing (iPhone
 * Safari), @zxing/browser loaded on first use. A barcode nothing carries can be linked to a
 * product on the spot, so a shop builds its barcode list just by scanning.
 *
 * Stock-take scans are queued in this browser and sent in batches, so a count keeps going with
 * no signal in the storeroom. Each scan carries its own id and the server ignores one it has
 * seen, so a batch re-sent after a dropped reply never double-counts (migration 183).
 * Every change is recorded as a stock movement (migration 182).
 */
import StockV2 from './stock/StockV2'

export { toQty } from './stock/useStock'

export default function VulaStock({ ...props }) {
  return <StockV2 {...props} />
}

