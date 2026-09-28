"""
vula/integrations/doc_labels.py — labels a person would put on a document, read from its content.

2026-09-28 (Ian, DIGG): "I've added docs — these are expenses over and above the projected BOQ
and need to be built as a label in the file." The same day a "variation claim for additional
works not included in the original drawings — HPC Fit-Out" (R34,335) and "additional costs
incurred due to errors or delays caused by Storeplay" (R5,800) were filed as plain quotes named
"Quote 20260928-1337.pdf", with nothing saying they were over the BOQ.

labels_for() is deterministic (no model): the extraction's own summary, filename and line items
are checked for the wording such documents use. The labels are stored in fields["labels"],
shown on the document, used in its filename, and counted per project by job costing.
"""
from __future__ import annotations

import re
from typing import Any, Dict, List

VARIATION = "Variation — over BOQ"
BACK_CHARGE = "Back-charge"

_VARIATION_RE = re.compile(
    r"\bvariations?\b|\bv\.?o\.?\s*#?\d+|variation (order|claim)|additional works?|extra works?|"
    r"extras? over|over and above|not (included|allowed|priced) (in|for) the (original|contract|"
    r"boq|bill|drawings|scope|quote)|out of (the )?scope|scope change|change order|"
    r"additional costs?|additional (tiler|labou?r|day)|remedial works?|re-?work", re.IGNORECASE)
_BACK_CHARGE_RE = re.compile(
    r"back[- ]?charge|(errors?|delays?|damage|defects?)\s+(or\s+\w+\s+)?caused by|"
    r"recover(able)? from|at the (sub)?contractor'?s? cost|contra[- ]?charge", re.IGNORECASE)
_MONEY_CATS = {"Invoice", "Quote / Estimate", "Bill of Quantities (BOQ)", "Proof of Payment",
               "Fee Proposal / Schedule", "Contract / Agreement", "Report", "General Document"}


def labels_for(category: str, summary: str = "", fields: Dict[str, Any] = None,
               filename: str = "") -> List[str]:
    fields = fields or {}
    if category not in _MONEY_CATS:
        return list(fields.get("labels") or [])
    lines = " ".join(str((li or {}).get("description") or "") for li in (fields.get("line_items") or [])
                     if isinstance(li, dict))
    text = " ".join([summary or "", filename or "", lines, str(fields.get("notes") or ""),
                     str(fields.get("title") or "")])
    out = list(fields.get("labels") or [])
    if _VARIATION_RE.search(text) and VARIATION not in out:
        out.append(VARIATION)
    if _BACK_CHARGE_RE.search(text) and BACK_CHARGE not in out:
        out.append(BACK_CHARGE)
    return out


def is_variation(fields: Dict[str, Any]) -> bool:
    return VARIATION in (fields or {}).get("labels", [])
