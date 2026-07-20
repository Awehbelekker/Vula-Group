"""
vula/commerce/extraction_quality.py — shared arithmetic-verification heuristic for
AI-extracted financial documents (invoices, quotes, delivery notes, receipts).

Originally lived only in the Smart Scanner (vula/api/commerce.py's _scan_quality_ok), which
never trusted a vision model's self-reported confidence — it explicitly cross-checks the
extracted line-item math against the stated total instead, since a model can misread a figure
while still reporting "high" confidence. Moved here (store-admin-reconciliation follow-on,
migration 102) so the email/WhatsApp document pipeline gets the same real verification the
Smart Scanner already had, instead of a weaker one-shot read with no correctness check at all.
"""
from __future__ import annotations

from typing import Any, Dict


def scan_quality_ok(ex: Dict[str, Any]) -> bool:
    """Heuristic: did the extraction produce a usable financial read?

    Used to decide whether to escalate a weak/local read to a stronger model. Fails on the
    common failure modes:
      - parse error / self-reported low confidence
      - missing money total or no line items
      - line-item totals that don't reconcile with the stated total (a model often reads the
        structure correctly but grabs the wrong number as the total, while still reporting
        "high" confidence — so confidence alone is never trusted; the arithmetic is
        cross-checked instead).
    """
    if not ex or ex.get("raw"):
        return False
    if str(ex.get("confidence") or "").lower() == "low":
        return False
    total = ex.get("total_cents") or 0
    items = ex.get("line_items") or []
    if not total or not items:
        return False

    # Reconciliation: sum of line totals should be within ~30% of the stated total (allowing
    # for VAT, delivery, rounding). A gross mismatch means at least one money figure was
    # misread → escalate.
    line_sum = 0
    for it in items:
        lt = it.get("total_cents")
        if lt is None:  # fall back to qty × unit price
            q = it.get("quantity") or 0
            up = it.get("unit_price_cents") or 0
            lt = q * up
        line_sum += int(lt or 0)
    if line_sum > 0:
        ratio = line_sum / total
        if ratio > 1.3 or ratio < 0.7:
            return False
    return True
