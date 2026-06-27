"""
vula/integrations/finances.py — post extracted financial docs to a per-project ledger.

When a doc the AI classified as a payment/invoice (with an amount) is filed to a project,
we record money in/out so the project shows cash-in, cash-out, net and budget-vs-actual.
"""
from __future__ import annotations

import json
import logging
import re
from typing import Optional

logger = logging.getLogger(__name__)


def _client():
    from vula.commerce import service as commerce_service
    return commerce_service._client()


def _to_amount(v) -> float:
    if isinstance(v, (int, float)):
        return float(v)
    if not v:
        return 0.0
    s = re.sub(r"[^0-9.\-]", "", str(v).replace(",", ""))
    try:
        return float(s) if s not in ("", "-", ".") else 0.0
    except Exception:
        return 0.0


_OUT_KW = ("wage", "salary", "payroll", "supplier", "purchase", "expense", "subcontract",
           "subcontractor", "material", "paid to", "payment to", "creditor")
_IN_KW = ("deposit", "received", "payment received", "progress payment", "paid by",
          "income", "revenue", "from client", "invoice to")


def _direction(fields: dict, summary: str, category: str, owner_names: list) -> str:
    """Best-guess money direction from the owner's perspective."""
    f = fields or {}
    payer = str(f.get("payer") or "").lower()
    payee = str(f.get("payee") or "").lower()
    for o in (owner_names or []):
        o = (o or "").lower().strip()
        if len(o) >= 3:
            if o in payer:
                return "out"          # owner is paying → money out
            if o in payee:
                return "in"           # owner is being paid → money in
    blob = f"{summary} {category} {json.dumps(f)}".lower()
    if any(k in blob for k in _IN_KW):
        return "in"
    if any(k in blob for k in _OUT_KW):
        return "out"
    return "unknown"


def _kind(category: str, summary: str) -> str:
    c, s = (category or "").lower(), (summary or "").lower()
    if "payment" in c or "proof of payment" in s or "notification of payment" in s or "eft" in s or "paid" in s:
        return "payment"
    if "invoice" in c or "invoice" in s or "quote" in c or "bill" in c:
        return "invoice"
    return "other"


def _tok(s: str) -> set:
    return set(re.findall(r"[a-z0-9]{3,}", (s or "").lower()))


def _find_match(tenant_id: str, want_kind: str, amount: float, counterparty: str,
                reference: str, _account: str = "") -> Optional[dict]:
    """Find an unreconciled counterpart (invoice<->payment) by amount + supplier/reference."""
    try:
        rows = (_client().table("vula_project_finances").select("*")
                .eq("tenant_id", tenant_id).eq("kind", want_kind).eq("reconciled", False)
                .execute().data or [])
    except Exception:
        return None
    ctoks, ref = _tok(counterparty), (reference or "").lower().strip()
    tol = max(1.0, amount * 0.01)            # 1% / R1 tolerance
    best, best_score = None, 0
    for r in rows:
        if abs(float(r.get("amount") or 0) - amount) > tol:
            continue                         # amounts must line up
        score = 2                            # amount match is the anchor
        if _account and _account == (r.get("bank_account") or "").strip():
            score += 5                       # same bank account = definitive
        if ref and ref == (r.get("reference") or "").lower().strip():
            score += 3
        if ctoks and ctoks & _tok(r.get("counterparty") or ""):
            score += 2
        if score > best_score:
            best, best_score = r, score
    return best


def post_finance_from_doc(tenant_id: str, project: Optional[str], fields: dict,
                          doc_id: str, filename: str, summary: str = "",
                          category: str = "", owner_names: list = None) -> Optional[dict]:
    """Record a ledger row if the doc carries a positive amount. Reconciles payments
    against invoices (by amount + supplier) so allocation is matched, not guessed.
    Idempotent per doc_id."""
    amount = _to_amount((fields or {}).get("amount"))
    if amount <= 0:
        return None
    f = fields or {}
    kind = _kind(category, summary)
    counterparty = f.get("payee") or f.get("supplier") or f.get("payer") or f.get("client")
    reference = f.get("reference")
    account = str(f.get("account_number") or f.get("account") or f.get("beneficiary_account") or "").strip()
    row = {
        "tenant_id": tenant_id, "project": project, "doc_id": doc_id, "filename": filename,
        "direction": _direction(f, summary, category, owner_names),
        "amount": amount, "counterparty": counterparty, "bank_account": account or None,
        "reference": reference, "category": category, "kind": kind,
        "description": f.get("description") or f.get("line_items") or summary,
        "occurred_at": f.get("date"), "source": "email", "reconciled": False,
    }

    # Reconcile a payment against an existing invoice (or vice-versa) → inherit what it's
    # for + the project from the matched doc, rather than relying on keyword guessing.
    # Bank account number is the strongest match key.
    mate = None
    if kind in ("payment", "invoice"):
        mate = _find_match(tenant_id, "invoice" if kind == "payment" else "payment",
                           amount, counterparty, reference, account)
    if mate:
        row["reconciled"] = True
        row["matched_id"] = mate["id"]
        row["project"] = row["project"] or mate.get("project")
        row["category"] = mate.get("category") or row["category"]
        row["description"] = row["description"] or mate.get("description")

    try:
        res = _client().table("vula_project_finances").upsert(
            row, on_conflict="tenant_id,doc_id").execute()
        if mate and res.data:
            _client().table("vula_project_finances").update(
                {"reconciled": True, "matched_id": res.data[0]["id"],
                 "project": mate.get("project") or row["project"]}).eq("id", mate["id"]).execute()
        row["matched"] = bool(mate)
        return row
    except Exception as exc:
        logger.debug("finance post skipped (run migration 027?): %s", exc)
        return None


def finance_summary(tenant_id: str, project: str = None) -> dict:
    """Per-project totals (in/out/net) + budget-vs-actual, plus recent transactions."""
    try:
        q = (_client().table("vula_project_finances").select("*")
             .eq("tenant_id", tenant_id).order("created_at", desc=True).limit(500))
        if project:
            q = q.eq("project", project)
        rows = q.execute().data or []
    except Exception as exc:
        logger.debug("finance summary skipped: %s", exc)
        rows = []
    try:
        budgets = {b["project"]: float(b["budget"]) for b in
                   (_client().table("vula_project_budgets").select("project,budget")
                    .eq("tenant_id", tenant_id).execute().data or [])}
    except Exception:
        budgets = {}

    by_proj: dict = {}
    for r in rows:
        p = r.get("project") or "(unassigned)"
        agg = by_proj.setdefault(p, {"project": p, "in": 0.0, "out": 0.0, "count": 0})
        amt = float(r.get("amount") or 0)
        if r.get("direction") == "in":
            agg["in"] += amt
        elif r.get("direction") == "out":
            agg["out"] += amt
        agg["count"] += 1
    projects = []
    for p, agg in by_proj.items():
        agg["net"] = round(agg["in"] - agg["out"], 2)
        agg["budget"] = budgets.get(p, 0.0)
        agg["remaining"] = round(agg["budget"] - agg["out"], 2) if agg["budget"] else None
        agg["in"], agg["out"] = round(agg["in"], 2), round(agg["out"], 2)
        projects.append(agg)
    projects.sort(key=lambda x: x["out"], reverse=True)
    return {"projects": projects, "transactions": rows[:100],
            "total_in": round(sum(p["in"] for p in projects), 2),
            "total_out": round(sum(p["out"] for p in projects), 2)}
