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

import re
from typing import Any, Dict, List


def _money_candidates(cents: int) -> set[str]:
    """Digit-strings a `cents` amount could show up as in document text — with and without the
    decimal part, since a source may print "R44 000" or "R44 000.00"."""
    cents = abs(int(cents))
    whole, frac = divmod(cents, 100)
    return {f"{whole}{frac:02d}", f"{whole}"}


def _iter_money_cents(ex: Dict[str, Any]):
    """(field-label, cents) for every money figure in an extraction — the top-level *_cents
    keys plus each line item's unit price and total."""
    for k, v in (ex or {}).items():
        if k.endswith("_cents") and isinstance(v, (int, float)) and v:
            yield k, int(v)
    for i, it in enumerate(ex.get("line_items") or []):
        if not isinstance(it, dict):
            continue
        for k in ("unit_price_cents", "total_cents"):
            v = it.get(k)
            if isinstance(v, (int, float)) and v:
                yield f"line_items[{i}].{k}", int(v)


def ungrounded_figures(ex: Dict[str, Any], source_text: str,
                       min_cents: int = 1000) -> List[Dict[str, Any]]:
    """Every money figure in `ex` (>= R10 by default) whose digits do NOT appear anywhere in
    `source_text`. An LLM that misreads or invents a figure usually produces one that is
    internally consistent (the line items it also invented sum to it), so scan_quality_ok's
    arithmetic check passes — but the number is still not on the page. This is the verbatim
    backstop: the total that gets booked must be legible in the document it came from.

    Returns [] when there's no source text to check against (deterministic parses, images with
    no text layer) — nothing to verify, nothing to falsely flag. Same fail-open stance as
    core.skills.base.unverified_prices.
    """
    digits = re.sub(r"\D", "", source_text or "")
    if len(digits) < 3:
        return []
    bad: List[Dict[str, Any]] = []
    for label, cents in _iter_money_cents(ex):
        if abs(cents) < min_cents:
            continue
        if not any(c in digits for c in _money_candidates(cents)):
            bad.append({"field": label, "cents": cents, "rand": round(cents / 100, 2)})
    return bad


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
