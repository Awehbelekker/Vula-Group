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


def _find_match(tenant_id: str, want_kind, amount: float, counterparty: str,
                reference: str, _account: str = "", project: Optional[str] = None) -> Optional[dict]:
    """Find an unreconciled counterpart (invoice<->payment, or expense<->payment — e.g. a
    Nolo procurement completion waiting for the POP that pays it) by amount + supplier/
    reference/project. `want_kind` may be a single kind or a list of candidate kinds."""
    want_kinds = [want_kind] if isinstance(want_kind, str) else list(want_kind)
    try:
        rows = (_client().table("vula_project_finances").select("*")
                .eq("tenant_id", tenant_id).in_("kind", want_kinds).eq("reconciled", False)
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
        if project and r.get("project") and _norm_label(project) == _norm_label(r.get("project")):
            score += 2                       # same project — helps when amounts collide
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

    # Reconcile a payment against an existing invoice/expense (or vice-versa) → inherit
    # what it's for + the project from the matched doc, rather than relying on keyword
    # guessing. Bank account number is the strongest match key. "expense" here covers a
    # procurement/stock-ordering ClickUp task Nolo already logged a cost for — the POP
    # that eventually pays it should link back to that entry, not sit as a second,
    # unlinked line.
    mate = None
    if kind == "payment":
        mate = _find_match(tenant_id, ["invoice", "expense"], amount, counterparty,
                           reference, account, project)
    elif kind == "invoice":
        mate = _find_match(tenant_id, "payment", amount, counterparty, reference, account, project)
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
        _notify_payment(tenant_id, row)
        return row
    except Exception as exc:
        logger.debug("finance post skipped (run migration 027?): %s", exc)
        return None


def _notify_payment(tenant_id: str, row: dict) -> None:
    """Best-effort WhatsApp nudge to team members subscribed to payment_received."""
    try:
        import asyncio
        from vula.integrations.notify import notify_team
        arrow = "▼ out" if row.get("direction") == "out" else ("▲ in" if row.get("direction") == "in" else "•")
        msg = (f"💵 Payment filed {arrow} R{row['amount']:,.0f} — "
               f"{row.get('counterparty') or row.get('filename')}"
               f"{(' · ' + row['project']) if row.get('project') else ''}"
               f"{(' (' + row['category'] + ')') if row.get('category') else ''}")
        loop = asyncio.get_event_loop()
        if loop.is_running():
            loop.create_task(notify_team(tenant_id, "payment_received", msg))
        else:
            loop.run_until_complete(notify_team(tenant_id, "payment_received", msg))
    except Exception as exc:
        logger.debug("payment notify skipped: %s", exc)


def _dedupe_reconciled(rows: list) -> list:
    """A reconciled pair (e.g. a Nolo procurement "expense" entry + the POP that later
    paid it, or an invoice + the payment that settled it) is stored as two linked rows so
    each keeps its own source/date/doc — but summing both would double-count the real
    money. Keep exactly one side per pair (first one seen), plus every other row."""
    seen_pairs = set()
    out = []
    for r in rows:
        if r.get("reconciled") and r.get("matched_id"):
            pair_key = tuple(sorted([str(r.get("id")), str(r["matched_id"])]))
            if pair_key in seen_pairs:
                continue
            seen_pairs.add(pair_key)
        out.append(r)
    return out


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
    for r in _dedupe_reconciled(rows):
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


def _norm_label(s: str) -> str:
    """Loose project-label match so 'HPC_Bokaap', 'HPC Bokaap', 'hpc-bokaap' all reconcile."""
    return re.sub(r"[^a-z0-9]+", "", (s or "").lower())


def project_financials(tenant_id: str, project: str) -> dict:
    """Unified money picture for ONE project, pulling every source into one view:
      • contract/budget  — vula_project_budgets (settable; stands in for the BoQ value)
      • invoiced / paid  — commerce_invoices linked to the project (by status)
      • expenses         — commerce_expenses linked to the project
      • cash in / out    — vula_project_finances ledger (scanned/emailed payments)
    Labels are matched loosely so 'HPC_Bokaap' and 'HPC Bokaap' reconcile.
    """
    db = _client()
    key = _norm_label(project)

    def _match(rows, field="project"):
        return [r for r in rows if _norm_label(r.get(field)) == key]

    # Ledger (payments in/out) — reuse finance_summary's rows, loosely matched.
    ledger_rows = []
    try:
        ledger_rows = (db.table("vula_project_finances").select("*")
                       .eq("tenant_id", tenant_id).limit(2000).execute().data or [])
    except Exception:
        pass
    ledger = _match(ledger_rows)
    dedup_ledger = _dedupe_reconciled(ledger)
    paid_in = sum(float(r.get("amount") or 0) for r in dedup_ledger if r.get("direction") == "in")
    paid_out = sum(float(r.get("amount") or 0) for r in dedup_ledger if r.get("direction") == "out")

    # Invoices linked to the project (guarded — column may not exist pre-migration 055).
    invoiced = invoiced_paid = 0.0
    inv_count = 0
    try:
        invs = (db.table("commerce_invoices")
                .select("total_cents,status,project,doc_type").eq("tenant_id", tenant_id)
                .limit(3000).execute().data or [])
        invs = [i for i in _match(invs) if (i.get("doc_type") or "invoice") == "invoice"]
        inv_count = len(invs)
        invoiced = sum(int(i.get("total_cents") or 0) for i in invs) / 100.0
        invoiced_paid = sum(int(i.get("total_cents") or 0) for i in invs if i.get("status") == "paid") / 100.0
    except Exception as exc:
        logger.debug("project invoices skipped (run migration 055?): %s", exc)

    # Expenses linked to the project.
    expenses = 0.0
    try:
        exps = (db.table("commerce_expenses").select("amount_cents,project")
                .eq("tenant_id", tenant_id).limit(3000).execute().data or [])
        expenses = sum(int(e.get("amount_cents") or 0) for e in _match(exps)) / 100.0
    except Exception as exc:
        logger.debug("project expenses skipped (run migration 055?): %s", exc)

    # Casual labour paid on this project (bank debits categorised as casual_labour, tagged to it).
    labour = 0.0
    try:
        lab = (db.table("commerce_bank_transactions").select("amount_cents,project,account_code")
               .eq("tenant_id", tenant_id).eq("account_code", "casual_labour").limit(5000).execute().data or [])
        labour = sum(int(r.get("amount_cents") or 0) for r in _match(lab)) / 100.0
    except Exception as exc:
        logger.debug("project labour skipped (run migration 059?): %s", exc)

    # Contract value: a persisted BoQ (migration 056) wins; else the manual budget.
    contract = 0.0
    try:
        boq = (db.table("vula_project_boq").select("project,total_cents")
               .eq("tenant_id", tenant_id).limit(500).execute().data or [])
        b = _match(boq)
        if b:
            contract = int(b[0].get("total_cents") or 0) / 100.0
    except Exception as exc:
        logger.debug("project boq skipped (run migration 056?): %s", exc)
    if not contract:
        try:
            brows = (db.table("vula_project_budgets").select("project,budget")
                     .eq("tenant_id", tenant_id).limit(500).execute().data or [])
            b = _match(brows)
            contract = float(b[0]["budget"]) if b else 0.0
        except Exception:
            pass

    spent = round(paid_out + expenses + labour, 2)  # cash paid out + logged expenses + casual labour
    return {
        "project": project,
        "contract": round(contract, 2),            # contract value / budget (BoQ stand-in)
        "invoiced": round(invoiced, 2),            # total raised to the client
        "invoiced_paid": round(invoiced_paid, 2),  # invoices marked paid
        "invoice_count": inv_count,
        "paid_in": round(paid_in, 2),              # cash received (ledger)
        "spent": spent,                            # cash out + expenses + labour
        "labour_cents": int(round(labour * 100)),  # casual labour paid on this project
        "net": round(paid_in - spent, 2),
        "remaining": round(contract - spent, 2) if contract else None,
        "outstanding": round(max(invoiced - invoiced_paid, 0), 2),  # billed but not yet paid
        "transactions": sorted(ledger, key=lambda r: r.get("created_at") or "", reverse=True)[:100],
    }
