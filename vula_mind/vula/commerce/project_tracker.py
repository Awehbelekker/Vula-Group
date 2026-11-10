"""
vula/commerce/project_tracker.py — one project's money, for the owner and, by link, its client.

2026-10-27 (Ian, DIGG): Belladonna's final account — budget against actual, what Rudi paid
suppliers directly against what he paid DIGG, labour against materials, and the balance due —
was built by hand from the bank, the filed invoices and Judy's sheet over a dozen rounds of
corrections. Everything it needed is already in Vula; this puts it in one place, from the same
sources the rest of Vula uses (no second set of books):

  budget            vula_project_boq: the bill's total and its sections (the signed baseline
                    when one is locked — project_programme.set_baseline)
  variations        documents labelled as variations (job_costing._variations): extra work
                    claimed from the client, extra charged by suppliers
  cost by trade     the business's bank lines allocated to the project (job_costing.costing),
                    plus statement-proven claims someone paid from their own pocket
  paid directly     what the client paid suppliers themselves: direct-supplier invoices
                    (job_costing.direct_costs), slips answered "the client paid"
                    (commerce_expenses.paid_with = 'client'), and payments recorded as the
                    client's own (vula_project_direct_payments, migration 229 — with what the
                    client has still to pay) — in the project's spend, never in the business's
                    cost, fee or profit
  fee               the project's terms (vula_project_terms: cost-plus, fee %) on the business's cost
  received          money in on the project (job_costing)
  invoiced / paid   the project's own invoices (commerce_invoices)

Shared by link (vula_project_shares, migrations 226–227), in the view chosen for that project:
  price      the contract, what was invoiced and paid, what is still owed — the business's own
             costs, fee and margin stay private
  open_book  cost-plus: the business's cost per trade, what the client paid suppliers directly,
             the fee, what the client paid the business, and the balance
  qs         a quantity surveyor's view: contract, certified and anticipated final per bill,
             variations, valuations and retention, invoices, the programme, and the document
             register — the business's own costs, fee and margin stay private, as in `price`

The link's token is random (secrets.token_urlsafe) and only its sha256 is stored; every link
expires (180 days) and can be revoked. All money is integer cents; nothing is estimated.
"""
from __future__ import annotations

import hashlib
import html
import logging
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

log = logging.getLogger(__name__)

VIEWS = ("price", "open_book", "qs")
VIEW_LABEL = {"price": "Price only", "open_book": "Open book (cost-plus)", "qs": "QS view"}
# A valuation's per-bill figures, kept on vula_project_boq sections when the BOQ is a payment
# certificate (vula/ingestion/boq_sheet.py: certified / anticipated / variation columns).
_SECTION_EXTRA = ("certified_cents", "anticipated_cents", "variation_cents")
# The business's own reconciled project bill (boq_sheet: Actual / Paid-to-date columns) — what each
# section really cost and how much of it is paid, whoever paid it. Owner and open-book views only:
# a price-only or QS link never shows what the business's suppliers cost.
_SECTION_COST = ("actual_cents", "paid_cents")
LINK_DAYS = 180


def _client():
    from vula.commerce import service
    return service._client()


def _key(name: Optional[str]) -> str:
    from vula.commerce.service import project_key
    return project_key(name)


def _rows(make) -> List[Dict[str, Any]]:
    from vula.commerce.ledger import _all_pages
    try:
        return _all_pages(make)
    except Exception as exc:
        log.debug("project tracker read skipped: %s", exc)
        return []


def _boq(tenant_id: str, key: str) -> Optional[Dict[str, Any]]:
    rows = _rows(lambda: _client().table("vula_project_boq")
                 .select("project,title,total_cents,sections,baseline_locked,baseline_set_at")
                 .eq("tenant_id", tenant_id))
    return next((r for r in rows if _key(r.get("project")) == key), None)


def _client_paid(tenant_id: str, key: str) -> Dict[str, Any]:
    """Slips the client paid the supplier for directly ("the client paid" — whatsapp.py)."""
    rows = _rows(lambda: _client().table("commerce_expenses")
                 .select("date,supplier,description,amount_cents,project")
                 .eq("tenant_id", tenant_id).eq("paid_with", "client"))
    mine = [r for r in rows if _key(r.get("project")) == key]
    return {"cents": sum(int(r.get("amount_cents") or 0) for r in mine), "lines": len(mine)}


_DIRECT = "vula_project_direct_payments"


def direct_payments(tenant_id: str, key: str) -> List[Dict[str, Any]]:
    """The client's own payments to suppliers and trades on the project (paid and still to pay)."""
    rows = _rows(lambda: _client().table(_DIRECT)
                 .select("id,project,supplier,description,amount_cents,paid_on,status,created_at")
                 .eq("tenant_id", tenant_id).order("paid_on"))
    return [{"id": r["id"], "supplier": r.get("supplier"), "description": r.get("description"),
             "amount_cents": int(r.get("amount_cents") or 0), "paid_on": r.get("paid_on"), "status": r.get("status")}
            for r in rows if _key(r.get("project")) == key and r.get("status") in ("paid", "to_pay")]


def add_direct_payment(tenant_id: str, project: str, supplier: str, amount_cents: int, *,
                       paid_on: Optional[str] = None, status: str = "paid", description: Optional[str] = None,
                       by: Optional[str] = None) -> Dict[str, Any]:
    """Record a payment the client made (or still has to make) to a supplier directly, read back."""
    supplier = " ".join(str(supplier or "").split())[:120]
    if not supplier:
        raise ValueError("Say who the client paid.")
    try:
        amount_cents = int(amount_cents)
    except (TypeError, ValueError):
        raise ValueError("The amount isn't a number.")
    if amount_cents <= 0:
        raise ValueError("The amount must be more than R0.")
    if status not in ("paid", "to_pay"):
        raise ValueError("status must be 'paid' or 'to_pay'")
    from vula.commerce.service import canonical_project
    name = canonical_project(tenant_id, project) or project
    db = _client()
    res = db.table(_DIRECT).insert({
        "tenant_id": tenant_id, "project": name, "supplier": supplier,
        "description": (" ".join(str(description or "").split())[:200] or None), "amount_cents": amount_cents,
        "paid_on": (str(paid_on)[:10] if paid_on else None), "status": status, "created_by": by}).execute()
    new_id = ((getattr(res, "data", None) or [{}])[0] or {}).get("id")
    back = [r for r in direct_payments(tenant_id, _key(name))
            if (new_id and r["id"] == new_id) or (not new_id and r["supplier"] == supplier and r["amount_cents"] == amount_cents)]
    if not back:
        raise RuntimeError("the payment was not saved")
    return {**back[-1], "project": name}


def remove_direct_payment(tenant_id: str, payment_id: str) -> bool:
    """Take a wrong entry out (kept, marked 'removed'), read back."""
    db = _client()
    db.table(_DIRECT).update({"status": "removed", "updated_at": datetime.now(timezone.utc).isoformat()}) \
        .eq("tenant_id", tenant_id).eq("id", payment_id).execute()
    rows = (db.table(_DIRECT).select("status").eq("tenant_id", tenant_id).eq("id", payment_id)
            .limit(1).execute().data or [])
    return bool(rows and rows[0].get("status") == "removed")


def _invoices(tenant_id: str, key: str) -> List[Dict[str, Any]]:
    rows = _rows(lambda: _client().table("commerce_invoices")
                 .select("invoice_number,issue_date,due_date,total_cents,total_paid_cents,status,"
                         "doc_type,direction,project")
                 .eq("tenant_id", tenant_id).eq("doc_type", "invoice"))
    out = []
    for r in rows:
        if _key(r.get("project")) != key or (r.get("direction") or "outbound") != "outbound" \
                or r.get("status") in ("cancelled", "draft"):
            continue
        total = int(r.get("total_cents") or 0)
        paid = total if r.get("status") == "paid" else min(total, int(r.get("total_paid_cents") or 0))
        out.append({"number": r.get("invoice_number"), "date": r.get("issue_date"), "due": r.get("due_date"),
                    "total_cents": total, "paid_cents": paid, "owed_cents": total - paid,
                    "status": r.get("status")})
    return sorted(out, key=lambda i: str(i.get("date") or ""))


def _draft_invoices_cents(tenant_id: str, key: str) -> int:
    """The project's invoices to the client still in draft (not yet sent), in cents."""
    rows = _rows(lambda: _client().table("commerce_invoices")
                 .select("total_cents,status,doc_type,direction,project")
                 .eq("tenant_id", tenant_id).eq("doc_type", "invoice").eq("status", "draft"))
    return sum(int(r.get("total_cents") or 0) for r in rows
               if _key(r.get("project")) == key and (r.get("direction") or "outbound") == "outbound")


def _r_text(cents: int) -> str:
    return ("-" if cents < 0 else "") + f"R{abs(cents) / 100:,.2f}"


def _warnings(final: Optional[Dict[str, Any]], basis: Optional[Dict[str, Any]], fee_source: Optional[str],
              fee_pct: Optional[float], estimate_balance: Optional[int], invoices: List[Dict[str, Any]],
              drafts_cents: int, received: int) -> List[str]:
    """What the owner should check before the client relies on the link (10 Nov 2026: Belladonna's
    link told the client the business owed him R57,917.27 while its invoice asked him for R32,839.40)."""
    out: List[str] = []
    if basis and basis["pricing"] == "per_bill" and not final:
        out.append("Priced as per the bill: the client's open-book link shows the contract and your invoices. "
                   "Store the final account when the job is done so the link shows the balance as agreed.")
    if not final and not basis:
        out.append("No fee is agreed for this project, so the client's open-book link shows no balance. "
                   "Set the fee (a percentage or a fixed amount) or store the final account.")
    elif not final and fee_source == "standard" and basis["pricing"] != "per_bill":
        what = f"{fee_pct:g}%" if fee_pct is not None else f"{_r_text(basis['fee_cents'])} fixed"
        out.append(f"This project uses your standard fee ({what}). If a different fee was agreed with this "
                   "client, set it for the project.")
    balance = final["balance_cents"] if final else estimate_balance
    if balance is None:
        return out
    if balance < 0:
        out.append(f"The client's link shows you owing the client {_r_text(-balance)}. Check that every cost is "
                   "booked to the project and that the fee is right before they rely on it.")
    owed = sum(i["owed_cents"] for i in invoices) + drafts_cents
    if balance > 0 and abs(balance - owed) > 100:
        out.append(f"The client's link shows {_r_text(balance)} due, but your invoices to the client add up to "
                   f"{_r_text(owed)} unpaid (drafts included). Bring them in line before the client compares them.")
    return out


def _claims(tenant_id: str, project: str) -> Dict[str, Any]:
    """The project's progress claims (vula_project_claims): how many, and the latest's figures."""
    try:
        from vula.integrations.progress_claims import list_claims
        rows = list_claims(tenant_id, project)
    except Exception as exc:
        log.debug("claims read skipped: %s", exc)
        rows = []
    if not rows:
        return {"count": 0, "latest": None}
    last = rows[-1]
    keys = ("cumulative_value_cents", "certified_to_date_cents", "retention_cents", "this_payment_cents")
    return {"count": len(rows), "latest": {"number": last.get("claim_number"), "status": last.get("status"),
                                           "date": last.get("claim_date"),
                                           **{k: int(last.get(k) or 0) for k in keys}}}


def _programme(tenant_id: str, project: str, today: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """Time gone on the programme and tasks done (project_programme's field tasks)."""
    from datetime import date
    try:
        from vula.commerce.project_programme import programme_tasks
        tasks = programme_tasks(tenant_id, project)
    except Exception as exc:
        log.debug("programme read skipped: %s", exc)
        return None
    dates = sorted(str(x)[:10] for t in tasks for x in (t.get("start_date"), t.get("due_date")) if x)
    if not tasks or not dates:
        return None
    first, last = date.fromisoformat(dates[0]), date.fromisoformat(dates[-1])
    day = date.fromisoformat(today) if today else datetime.now(timezone.utc).date()
    span = max((last - first).days + 1, 1)
    done = sum(1 for t in tasks if t.get("status") == "complete")
    return {"tasks": len(tasks), "done": done, "done_pct": round(100 * done / len(tasks)),
            "time_pct": max(0, min(100, round(100 * ((day - first).days + 1) / span))),
            "start": first.isoformat(), "finish": last.isoformat()}


def _documents(tenant_id: str, key: str, limit: int = 60) -> List[Dict[str, Any]]:
    """The project's document register: what was filed, as what, when (names only — no files)."""
    rows = _rows(lambda: _client().table("vula_filed_documents").select("filename,category,created_at,project,status")
                 .eq("tenant_id", tenant_id).order("created_at", desc=True))
    mine = [r for r in rows if _key(r.get("project")) == key and r.get("status") != "deleted"]
    return [{"name": r.get("filename"), "category": r.get("category"), "filed": str(r.get("created_at") or "")[:10]}
            for r in mine[:limit]]


_FINAL = "vula_project_final_accounts"


def final_account(tenant_id: str, key: str) -> Optional[Dict[str, Any]]:
    """The project's agreed final account (migration 254), or None. Charged + adjustments, in cents."""
    rows = _rows(lambda: _client().table(_FINAL)
                 .select("project,as_of,charged_cents,adjustments,note").eq("tenant_id", tenant_id))
    r = next((x for x in rows if _key(x.get("project")) == key), None)
    if not r:
        return None
    adj = [{"label": str(a.get("label") or "Adjustment"), "cents": int(a.get("cents") or 0)}
           for a in (r.get("adjustments") or []) if isinstance(a, dict)]
    charged = int(r.get("charged_cents") or 0)
    return {"as_of": str(r.get("as_of") or "")[:10], "charged_cents": charged, "adjustments": adj,
            "total_cents": charged + sum(a["cents"] for a in adj)}


def set_final_account(tenant_id: str, project: str, as_of: str, charged_cents: int,
                      adjustments: Optional[List[Dict[str, Any]]] = None, note: Optional[str] = None,
                      by: Optional[str] = None) -> Dict[str, Any]:
    """Store (or replace) the project's final account, read back."""
    from vula.commerce.service import canonical_project
    if int(charged_cents) < 0:
        raise ValueError("The final account can't be less than R0.")
    name = canonical_project(tenant_id, project) or project
    adj = [{"label": str(a["label"])[:200], "cents": int(a["cents"])} for a in adjustments or []]
    _client().table(_FINAL).upsert({
        "tenant_id": tenant_id, "project": name, "as_of": str(as_of)[:10], "charged_cents": int(charged_cents),
        "adjustments": adj, "note": note, "created_by": by,
        "updated_at": datetime.now(timezone.utc).isoformat()}, on_conflict="tenant_id,project").execute()
    back = final_account(tenant_id, _key(name))
    if not back or back["charged_cents"] != int(charged_cents) or back["adjustments"] != adj:
        raise RuntimeError("the final account was not saved")
    return {**back, "project": name}


def _fee_pct(tenant_id: str, project: str, costed: Optional[Dict[str, Any]]) -> float:
    if costed and costed.get("fee_pct") is not None:
        return float(costed["fee_pct"])
    from vula.commerce import job_costing
    t = job_costing.terms(tenant_id)
    return next((v for p, v in t.items() if p != "*" and _key(p) == _key(project)), t["*"])


def position(tenant_id: str, project: str) -> Dict[str, Any]:
    """The project's whole money picture (the owner's view). Every figure in cents."""
    from vula.commerce import job_costing
    key = _key(project)
    costing = job_costing.costing(tenant_id)
    costed = next((p for p in costing.get("projects") or [] if _key(p["project"]) == key), None)
    boq = _boq(tenant_id, key) or {}
    name = (costed or {}).get("project") or boq.get("project") or project
    business_cost = int((costed or {}).get("cost_cents") or 0)
    received = int((costed or {}).get("received_cents") or 0)
    variations = (costed or {}).get("variations") or {}
    direct = int((job_costing.direct_costs(tenant_id).get(key) or {}).get("cents") or 0)
    by_client = _client_paid(tenant_id, key)
    recorded = direct_payments(tenant_id, key)
    recorded_paid = sum(r["amount_cents"] for r in recorded if r["status"] == "paid")
    to_pay_directly = sum(r["amount_cents"] for r in recorded if r["status"] == "to_pay")
    invoices = _invoices(tenant_id, key)
    # the fee as agreed for this project, else the business's standard fee — never an assumed one
    agreed = job_costing.agreed_terms(tenant_id)
    own = next((v for p, v in agreed.items() if p != "*" and _key(p) == key), None)
    basis = own or agreed.get("*")
    fee_source = "project" if own else ("standard" if basis else None)
    fee_pct = basis["fee_pct"] if basis and basis["pricing"] == "cost_plus" else None
    if basis and basis["pricing"] == "cost_plus":
        fee: Optional[int] = int(round(business_cost * fee_pct / 100))
    elif basis and basis["pricing"] == "fixed_fee":
        fee = int(basis["fee_cents"])
    else:
        fee = None          # per the bill (the fee is in the bill's rates) or not agreed
    final = final_account(tenant_id, key)
    if final:
        # the agreed final account is the account: what the client paid comes from the bank, live
        final = {**final, "received_cents": received, "balance_cents": final["total_cents"] - received}
    budget = int(boq.get("total_cents") or 0)
    claimed = int(variations.get("claimed_cents") or 0)
    contract = budget + claimed
    invoiced = sum(i["total_cents"] for i in invoices)
    paid = sum(i["paid_cents"] for i in invoices)
    paid_directly = direct + by_client["cents"] + recorded_paid
    spent = business_cost + paid_directly
    sections = [{"section": s.get("section"), "budget_cents": int(s.get("budget_cents") or 0),
                 **{k: int(s[k]) for k in _SECTION_EXTRA + _SECTION_COST if s.get(k) is not None}}
                for s in boq.get("sections") or [] if s.get("section")]
    sheet = None
    if any("actual_cents" in s for s in sections):
        actual = sum(s.get("actual_cents", 0) for s in sections)
        sheet_paid = sum(s.get("paid_cents", 0) for s in sections)
        sheet = {"actual_cents": actual, "paid_cents": sheet_paid, "to_pay_cents": actual - sheet_paid,
                 "over_budget_cents": actual - budget if budget else None,
                 "over_sections": [s["section"] for s in sections if s.get("actual_cents", 0) > s["budget_cents"]],
                 # what the books show against what the reconciled bill says it cost
                 "not_in_books_cents": actual - spent}
    return {
        "project": name,
        "budget_cents": budget, "budget_title": boq.get("title"),
        "baseline_locked": bool(boq.get("baseline_locked")),
        "sections": sections,
        "sheet": sheet,
        "variations_claimed_cents": claimed,
        "variations_extra_cost_cents": int(variations.get("extra_cost_cents") or 0),
        "contract_cents": contract,
        "trades": [{"trade": t["trade"], "cents": int(t["cents"])} for t in (costed or {}).get("trades") or []],
        "business_cost_cents": business_cost,
        "paid_directly_cents": paid_directly,
        "paid_directly_suppliers_cents": direct, "paid_directly_slips_cents": by_client["cents"],
        "paid_directly_recorded_cents": recorded_paid, "direct_payments": recorded,
        "to_pay_directly_cents": to_pay_directly,
        "spent_cents": spent,
        "budget_left_cents": budget - spent if budget else None,
        "spent_pct": round(100 * spent / budget) if budget else None,
        "fee_basis": basis["pricing"] if basis else None, "fee_source": fee_source,
        "fee_pct": fee_pct, "fee_cents": fee,
        "open_book_total_cents": business_cost + fee if fee is not None else None,
        "received_cents": received,
        "open_book_balance_cents": business_cost + fee - received if fee is not None else None,
        "final_account": final,
        # the owner's own checks — never in a client view
        "warnings": _warnings(final, basis, fee_source, fee_pct, business_cost + fee - received if fee is not None else None,
                              invoices, _draft_invoices_cents(tenant_id, key), received),
        "invoices": invoices, "invoiced_cents": invoiced, "invoices_paid_cents": paid,
        "invoices_owed_cents": invoiced - paid,
        "to_invoice_cents": max(contract - invoiced, 0) if contract else None,
        "claims": _claims(tenant_id, name),
        "programme": _programme(tenant_id, name),
        "documents": _documents(tenant_id, key),
        # the owner's own figures — never in a client view
        "overhead_share_cents": (costed or {}).get("overhead_share_cents"),
        "profit_cents": (costed or {}).get("profit_cents"),
        "unallocated_trade_cents": (costed or {}).get("unallocated_trade_cents") or 0,
        "as_of": datetime.now(timezone.utc).date().isoformat(),
    }


# Figures each client view may carry. Anything not listed never leaves the business.
_PRICE_FIELDS = ("project", "budget_title", "sections", "budget_cents", "variations_claimed_cents",
                 "contract_cents", "invoices", "invoiced_cents", "invoices_paid_cents",
                 "invoices_owed_cents", "to_invoice_cents", "as_of")
_QS_FIELDS = _PRICE_FIELDS + ("variations_extra_cost_cents", "baseline_locked", "claims", "programme",
                               "documents")
_OPEN_BOOK_FIELDS = _PRICE_FIELDS + ("trades", "business_cost_cents", "paid_directly_cents", "spent_cents",
                                     "direct_payments", "to_pay_directly_cents",
                                     "budget_left_cents", "fee_pct", "fee_cents", "open_book_total_cents",
                                     "received_cents", "open_book_balance_cents", "sheet", "final_account",
                                     "fee_basis")


def client_view(pos: Dict[str, Any], view: str) -> Dict[str, Any]:
    """What the client sees. `price` keeps the business's cost, fee and margin private; neither
    view carries the overhead share, profit or anything not listed above."""
    fields = {"open_book": _OPEN_BOOK_FIELDS, "qs": _QS_FIELDS}.get(view, _PRICE_FIELDS)
    out = {"view": view if view in VIEWS else "price", **{k: pos.get(k) for k in fields}}
    if view != "open_book":
        out["sections"] = [{k: v for k, v in s.items() if k not in _SECTION_COST} for s in out.get("sections") or []]
    return out


# ── share links ────────────────────────────────────────────────────────────

def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def public_url(token: str) -> str:
    from config import settings
    return f"{settings.public_base_url.rstrip('/')}/track/{token}"


def _fee_agreed(tenant_id: str, project: str) -> bool:
    from vula.commerce import job_costing
    key = _key(project)
    agreed = job_costing.agreed_terms(tenant_id)
    return bool(final_account(tenant_id, key) or agreed.get("*")
                or any(p != "*" and _key(p) == key for p in agreed))


def create_share(tenant_id: str, project: str, view: str, by: Optional[str] = None) -> Dict[str, Any]:
    """A new link for the project in `view`. Read back before it's reported."""
    if view not in VIEWS:
        raise ValueError(f"view must be one of {VIEWS}")
    if view == "open_book" and not _fee_agreed(tenant_id, project):
        raise ValueError("Agree this project's fee first — a percentage on cost or a fixed amount — or store its "
                         "final account. An open-book link shows the client a balance, and it must not be "
                         "worked out on a guessed fee.")
    from vula.commerce.service import canonical_project
    name = canonical_project(tenant_id, project) or project
    token = secrets.token_urlsafe(24)
    expires = (datetime.now(timezone.utc) + timedelta(days=LINK_DAYS)).isoformat()
    db = _client()
    db.table("vula_project_shares").insert({
        "tenant_id": tenant_id, "project": name, "view": view, "token_hash": _hash(token),
        "created_by": by, "expires_at": expires}).execute()
    rows = (db.table("vula_project_shares").select("id,project,view,expires_at")
            .eq("tenant_id", tenant_id).eq("token_hash", _hash(token)).limit(1).execute().data or [])
    if not rows:
        raise RuntimeError("the share link was not saved")
    return {**rows[0], "url": public_url(token)}


def list_shares(tenant_id: str, project: Optional[str] = None) -> List[Dict[str, Any]]:
    rows = _rows(lambda: _client().table("vula_project_shares")
                 .select("id,project,view,created_by,created_at,expires_at,revoked_at,last_viewed_at,views")
                 .eq("tenant_id", tenant_id).order("created_at", desc=True))
    now = datetime.now(timezone.utc).isoformat()
    out = [{**r, "active": not r.get("revoked_at") and str(r.get("expires_at") or "") > now} for r in rows]
    return [r for r in out if not project or _key(r["project"]) == _key(project)]


def revoke_share(tenant_id: str, share_id: str) -> bool:
    db = _client()
    db.table("vula_project_shares").update({"revoked_at": datetime.now(timezone.utc).isoformat()}) \
        .eq("tenant_id", tenant_id).eq("id", share_id).execute()
    rows = (db.table("vula_project_shares").select("revoked_at")
            .eq("tenant_id", tenant_id).eq("id", share_id).limit(1).execute().data or [])
    return bool(rows and rows[0].get("revoked_at"))


def share_for_token(token: str) -> Optional[Dict[str, Any]]:
    """The live share a token opens, or None (unknown, revoked or expired)."""
    if not token or len(token) > 100:
        return None
    try:
        rows = (_client().table("vula_project_shares").select("id,tenant_id,project,view,expires_at,revoked_at,views")
                .eq("token_hash", _hash(token)).limit(1).execute().data or [])
    except Exception as exc:
        log.warning("project share lookup failed: %s", exc)
        return None
    if not rows or rows[0].get("revoked_at") or str(rows[0].get("expires_at") or "") <= datetime.now(timezone.utc).isoformat():
        return None
    return rows[0]


def note_view(share: Dict[str, Any]) -> None:
    try:
        _client().table("vula_project_shares").update(
            {"views": int(share.get("views") or 0) + 1, "last_viewed_at": datetime.now(timezone.utc).isoformat()}
        ).eq("id", share["id"]).execute()
    except Exception as exc:
        log.debug("project share view count skipped: %s", exc)


# ── text and page ──────────────────────────────────────────────────────────

def _r(cents: Optional[int]) -> str:
    if cents is None:
        return "—"
    return ("-" if cents < 0 else "") + f"R{abs(cents) / 100:,.2f}".replace(",", " ")


def summary_text(pos: Dict[str, Any], view: str) -> str:
    """WhatsApp text of what a client link in `view` will show (the owner's preview)."""
    v = client_view(pos, view)
    lines = [f"*{v['project']}* — what the client will see ({VIEW_LABEL[v['view']]}):"]
    if v.get("contract_cents"):
        lines.append(f"Contract: {_r(v['contract_cents'])}"
                     + (f" (incl. variations {_r(v['variations_claimed_cents'])})" if v.get("variations_claimed_cents") else ""))
    if view == "open_book":
        lines += [f"Your cost to date: {_r(v['business_cost_cents'])}",
                  f"Paid by the client directly to suppliers: {_r(v['paid_directly_cents'])}"
                  + (f" (still to pay directly: {_r(v['to_pay_directly_cents'])})" if v.get("to_pay_directly_cents") else ""),
                  f"Paid to you so far: {_r(v['received_cents'])}"]
        fa = v.get("final_account")
        if fa:
            lines += [f"Final account ({fa['as_of']}): {_r(fa['total_cents'])}",
                      f"*Balance due: {_r(fa['balance_cents'])}*"]
        elif v.get("fee_basis") == "per_bill":
            lines += [f"Priced as per the bill · invoiced {_r(v['invoiced_cents'])}, owed {_r(v['invoices_owed_cents'])}"]
        elif v.get("fee_cents") is None:
            lines.append("Fee: not agreed yet — the client sees no balance until you set it")
        else:
            lines += [(f"Fee ({v['fee_pct']:g}%)" if v.get("fee_basis") == "cost_plus" else "Fee (agreed amount)")
                      + f": {_r(v['fee_cents'])}",
                      f"*Balance due: {_r(v['open_book_balance_cents'])}*"]
    else:
        if view == "qs":
            c = (v.get("claims") or {}).get("latest")
            if c:
                lines.append(f"Valuation {c['number']}: certified to date {_r(c['certified_to_date_cents'])}, "
                             f"retention {_r(c['retention_cents'])}")
            certified = sum(s.get("certified_cents") or 0 for s in v.get("sections") or [])
            if certified:
                lines.append(f"Certified per the bill: {_r(certified)}")
            if v.get("programme"):
                pg = v["programme"]
                lines.append(f"Programme: {pg['time_pct']}% of the time gone, {pg['done']} of {pg['tasks']} tasks done")
            lines.append(f"Documents in the register: {len(v.get('documents') or [])}")
        lines += [f"Invoiced: {_r(v['invoiced_cents'])}", f"Paid: {_r(v['invoices_paid_cents'])}",
                  f"*Owed now: {_r(v['invoices_owed_cents'])}*"]
        if v.get("to_invoice_cents") is not None:
            lines.append(f"Still to invoice: {_r(v['to_invoice_cents'])}")
    return "\n".join(lines)


def _downloads(links: Optional[Dict[str, str]]) -> str:
    if not links:
        return ""
    e = html.escape
    return ("<p class='dl'>" + " ".join(f"<a href='{e(url)}'>⬇ {e(label)}</a>" for label, url in links.items())
            + "</p>")


def pdf(brand: Dict[str, Any], v: Dict[str, Any]) -> bytes:
    """The shared page as a PDF (no download links on it)."""
    from weasyprint import HTML
    return HTML(string=render(brand, v)).write_pdf()


def _basis_label(v: Dict[str, Any]) -> str:
    """How the account works, for the page's heading — never "cost-plus" for a project that isn't."""
    if v.get("view") != "open_book":
        return VIEW_LABEL.get(v.get("view"), "")
    fa = v.get("final_account")
    if fa:
        return f"Open book · final account of {fa['as_of']}"
    return {"per_bill": "Open book · priced as per the bill", "fixed_fee": "Open book · agreed fixed fee",
            "cost_plus": f"Open book · cost plus {v.get('fee_pct') or 0:g}%"}.get(v.get("fee_basis"), "Open book")


def _owed_summary(v: Dict[str, Any], e) -> str:
    """What the client still has to pay, at the top of an open-book page (10 Nov 2026, Ian: "we need to
    show what Rudi still owes DIGG — we need to be fully open"): to the business, to suppliers
    directly, and the two together. Only figures the page already carries; nothing estimated."""
    if v.get("view") != "open_book":
        return ""
    fa = v.get("final_account")
    if fa:
        to_us, how = fa["balance_cents"], f"per the final account of {fa['as_of']}"
    elif v.get("open_book_balance_cents") is not None:
        to_us, how = v["open_book_balance_cents"], "cost plus the agreed fee, less what you've paid"
    elif v.get("fee_basis") == "per_bill":
        to_us, how = v.get("invoices_owed_cents") or 0, "on our invoices"
    else:
        return ""
    direct = int(v.get("to_pay_directly_cents") or 0)
    rows = [("Still owed to us" if to_us >= 0 else "We owe you", abs(to_us), how)]
    if direct:
        rows.append(("Still to pay your suppliers directly", direct, "listed below"))
    if direct and to_us >= 0:
        rows.append(("Total still to pay on the project", to_us + direct, ""))
    body = "".join(f"<div class='owe{' tot' if i == len(rows) - 1 and len(rows) > 2 else ''}'>"
                   f"<span>{e(label)}<small>{e(sub)}</small></span><b>{_r(c)}</b></div>"
                   for i, (label, c, sub) in enumerate(rows))
    return f"<div class='owed'>{body}</div>"


def render(brand: Dict[str, Any], v: Dict[str, Any], links: Optional[Dict[str, str]] = None,
           extra_html: str = "") -> str:
    """The client's page. Every value escaped; every figure from client_view(). `links`: the
    downloads offered on it ({label: url}) — PDFs only; nobody outside the team gets the Excel.
    `extra_html`: a block built (and escaped) by the caller, e.g. the project's documents
    (vula/api/project_track.py)."""
    from vula.commerce import powered_by
    e = html.escape
    name = e(brand.get("name") or "")
    accent = e(brand.get("accent_color") or "#2C5545")
    logo = brand.get("logo_url")
    logo_html = f"<img src='{e(logo)}' alt='{name}' class='logo'>" if logo else f"<h2>{name}</h2>"

    def row(label, cents, strong=False):
        cls = " class='total'" if strong else ""
        return f"<tr{cls}><td>{e(label)}</td><td class='n'>{_r(cents)}</td></tr>"

    def table(title, rows):
        return f"<section class='blk'><h3>{e(title)}</h3><table>{''.join(rows)}</table></section>" if rows else ""

    def wide(title, head, body):
        # a table with several money columns: full width, and on a phone it scrolls inside its own box
        return (f"<section class='blk wide'><h3>{e(title)}</h3><p class='muted swipe'>Swipe the table sideways to see every column.</p>"
                f"<div class='scroll'><table class='cols'>{head}{body}</table></div></section>")

    parts = []
    head = [row("Agreed budget", v.get("budget_cents"))] if v.get("budget_cents") else []
    if v.get("variations_claimed_cents"):
        head.append(row("Variations", v["variations_claimed_cents"]))
    if v.get("contract_cents"):
        head.append(row("Contract total", v["contract_cents"], True))
    parts.append(table("The project", head))
    if v["view"] == "open_book":
        cost = [row(t["trade"], t["cents"]) for t in v.get("trades") or []]
        cost.append(row("Cost through us", v.get("business_cost_cents"), True))
        parts.append(table("Paid through our bank so far, by trade", cost))
        fa = v.get("final_account")
        if fa:
            # the agreed final account, not the cost + fee estimate
            acct = [row(f"Costs and fee (final account, {fa['as_of']})", fa["charged_cents"])]
            acct += [row(a["label"], a["cents"]) for a in fa.get("adjustments") or []]
            if fa.get("adjustments"):
                acct.append(row("Total for our part", fa["total_cents"], True))
            acct += [row("Paid to us so far", -(fa.get("received_cents") or 0)),
                     row("Balance due to us" if fa["balance_cents"] >= 0 else "Balance due to you",
                         abs(fa["balance_cents"]), True)]
            parts.append(table("Account", acct))
        else:
            if v.get("fee_basis") == "per_bill":
                # priced as per the bill: the fee is in the bill's rates — the account is the invoices
                acct = [row("Contract (as per the bill)", v.get("contract_cents")),
                        row("Invoiced to date", v.get("invoiced_cents")), row("Paid", v.get("invoices_paid_cents")),
                        row("Owed now", v.get("invoices_owed_cents"), True)]
                parts.append(table("Account", acct))
            elif v.get("fee_cents") is None:
                # no fee agreed for this project: costs and payments only — never a balance on a guess
                acct = [row("Cost through us", v.get("business_cost_cents")),
                        "<tr><td>Fee</td><td class='n'>Not agreed yet</td></tr>",
                        row("Paid to us so far", -(v.get("received_cents") or 0))]
                parts.append(table("Account", acct)[:-len("</table></section>")]
                             + "</table><p class='muted'>The balance shows here once the fee for this project is "
                               "agreed.</p></section>")
            else:
                fee_label = (f"Fee ({v.get('fee_pct') or 0:g}%)" if v.get("fee_basis") == "cost_plus"
                             else "Fee (agreed amount)")
                acct = [row("Cost through us", v.get("business_cost_cents")),
                        row(fee_label, v.get("fee_cents")),
                        row("Total for our part", v.get("open_book_total_cents"), True),
                        row("Paid to us so far", -(v.get("received_cents") or 0)),
                        row("Balance due to us" if (v.get("open_book_balance_cents") or 0) >= 0 else "Balance due to you",
                            abs(v.get("open_book_balance_cents") or 0), True)]
                parts.append(table("Account", acct))
        if v.get("paid_directly_cents") or v.get("to_pay_directly_cents"):
            spent = [row("Through our bank so far", v.get("business_cost_cents")),
                     row("Paid by you directly to suppliers", v.get("paid_directly_cents")),
                     row("Total spent on the project", v.get("spent_cents"), True)]
            if v.get("budget_left_cents") is not None:
                spent.append(row("Budget left", v["budget_left_cents"]))
            parts.append(table("Against the budget", spent))
            mine = v.get("direct_payments") or []
            for status, title in (("paid", "Paid by you directly"), ("to_pay", "Still to be paid by you directly")):
                rows_ = [row(f"{d['supplier']}{' — ' + d['description'] if d.get('description') else ''}", d["amount_cents"])
                         for d in mine if d["status"] == status]
                if rows_:
                    rows_.append(row("Total", sum(d["amount_cents"] for d in mine if d["status"] == status), True))
                    parts.append(table(title, rows_))
    else:
        acct = [row("Invoiced to date", v.get("invoiced_cents")), row("Paid", v.get("invoices_paid_cents")),
                row("Owed now", v.get("invoices_owed_cents"), True)]
        if v.get("to_invoice_cents") is not None:
            acct.append(row("Still to invoice", v["to_invoice_cents"]))
        parts.append(table("Account", acct))
    secs = v.get("sections") or []
    if secs and v["view"] == "qs" and any("certified_cents" in s or "anticipated_cents" in s for s in secs):
        cell = lambda c: f"<td class='n'>{_r(c)}</td>"
        body = [f"<tr><td>{e(s['section'])}</td>{cell(s['budget_cents'])}{cell(s.get('certified_cents'))}"
                f"{cell(s.get('anticipated_cents'))}</tr>" for s in secs]
        tot = lambda k: sum(s.get(k) or 0 for s in secs)
        body.append(f"<tr class='total'><td>Total</td>{cell(tot('budget_cents'))}{cell(tot('certified_cents'))}"
                    f"{cell(tot('anticipated_cents'))}</tr>")
        parts.append(wide("By bill", "<tr class='h'><td></td><td class='n'>Contract</td><td class='n'>Certified</td>"
                                     "<td class='n'>Anticipated final</td></tr>", ''.join(body)))
    elif secs and v["view"] == "open_book" and v.get("sheet"):
        cell = lambda c: f"<td class='n'>{_r(c)}</td>"
        body = [f"<tr><td>{e(s['section'])}</td>{cell(s['budget_cents'])}{cell(s.get('actual_cents'))}"
                f"{cell(s.get('paid_cents'))}</tr>" for s in secs]
        sh = v["sheet"]
        body.append(f"<tr class='total'><td>Total</td>{cell(sum(s['budget_cents'] for s in secs))}"
                    f"{cell(sh['actual_cents'])}{cell(sh['paid_cents'])}</tr>")
        parts.append(wide("By section", "<tr class='h'><td></td><td class='n'>Budget</td><td class='n'>Actual</td>"
                                        "<td class='n'>Paid</td></tr>", ''.join(body)))
    elif secs:
        parts.append(table("Budget by section", [row(s["section"], s["budget_cents"]) for s in secs]))
    if v["view"] == "qs":
        c = (v.get("claims") or {}).get("latest")
        if c:
            parts.append(table(f"Valuation {c['number']} ({c.get('status') or ''})", [
                row("Value of work to date", c["cumulative_value_cents"]),
                row("Retention held", c["retention_cents"]),
                row("Certified to date", c["certified_to_date_cents"], True),
                row("This payment", c["this_payment_cents"])]))
        if v.get("variations_extra_cost_cents"):
            parts.append(table("Variations", [row("Claimed from the client", v.get("variations_claimed_cents")),
                                              row("Extra charged by suppliers", v["variations_extra_cost_cents"])]))
        pg = v.get("programme")
        if pg:
            parts.append(f"<section class='blk'><h3>Programme</h3><p class='muted'>{e(pg['start'])} to {e(pg['finish'])}: "
                         f"{pg['time_pct']}% of the time gone · {pg['done']} of {pg['tasks']} tasks done "
                         f"({pg['done_pct']}%)</p></section>")
        docs = v.get("documents") or []
        if docs:
            parts.append(table(f"Document register ({len(docs)})", [
                f"<tr><td>{e(str(d.get('name') or ''))}<span class='d'>{e(str(d.get('category') or ''))}</span></td>"
                f"<td class='n'>{e(d.get('filed') or '')}</td></tr>" for d in docs]))
    inv = [f"<tr><td>{e(str(i.get('number') or ''))}<span class='d'>{e(str(i.get('date') or ''))}</span></td>"
           f"<td class='n'>{_r(i['total_cents'])}<span class='d'>{'paid' if i['owed_cents'] <= 0 else _r(i['owed_cents']) + ' owed'}</span></td></tr>"
           for i in v.get("invoices") or []]
    parts.append(table("Invoices", inv))
    title = e(v.get("project") or "")
    summary = _owed_summary(v, e)
    basis = _basis_label(v)
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><meta name="robots" content="noindex">
<title>{title} · {name}</title><style>
*{{box-sizing:border-box}}
body{{margin:0;padding:0 12px;background:#f5f3ee;font-family:system-ui,-apple-system,sans-serif;color:#2a2a2a;
-webkit-text-size-adjust:100%}}
.card{{width:100%;max-width:1040px;margin:24px auto;background:#fff;border-radius:14px;padding:24px 28px;
box-shadow:0 2px 10px rgba(0,0,0,.06);overflow:hidden}}
.logo{{max-height:56px;max-width:200px}}h1{{font-size:22px;margin:14px 0 2px;color:{accent};overflow-wrap:anywhere}}
.muted{{color:#6e6a63;margin:0 0 8px;font-size:14px;line-height:1.45}}h3{{font-size:15px;margin:28px 0 8px;color:{accent}}}
.owed{{margin:18px 0 6px;border:1.5px solid {accent};border-radius:12px;overflow:hidden}}
.owe{{display:flex;justify-content:space-between;align-items:flex-start;gap:12px;padding:12px 14px;border-top:1px solid #eee}}
.owe:first-child{{border-top:0}}.owe span{{min-width:0;overflow-wrap:anywhere}}
.owe small{{display:block;color:#8a857c;font-size:12px;margin-top:2px}}
.owe b{{white-space:nowrap;font-variant-numeric:tabular-nums;font-size:17px}}
.owe:first-child b{{color:{accent};font-size:20px}}.owe.tot{{background:#f5f3ee}}
/* the account blocks side by side on a wide screen, one column on a phone */
.grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,400px),1fr));column-gap:40px;align-items:start}}
.blk{{min-width:0}}.blk.wide{{grid-column:1/-1}}
.scroll{{overflow-x:auto;-webkit-overflow-scrolling:touch}}table.cols{{min-width:480px}}
table{{width:100%;border-collapse:collapse;font-size:15px;line-height:1.4}}td{{padding:9px 0;border-bottom:1px solid #eee;vertical-align:top}}
td:first-child{{overflow-wrap:anywhere;padding-right:12px}}
td.n{{text-align:right;white-space:nowrap;font-variant-numeric:tabular-nums}}table.cols td.n{{padding-left:14px}}
tr.total td{{font-weight:700;border-top:2px solid {accent}}}
.d{{display:block;color:#8a857c;font-size:12px}}tr.h td{{color:#8a857c;font-size:12px}}
.dl{{margin:22px 0 0}}.dl a{{display:inline-block;margin:0 10px 8px 0;padding:9px 14px;border-radius:9px;
background:{accent};color:#fff;text-decoration:none;font-weight:600;font-size:14px}}
.btn{{display:inline-flex;align-items:center;min-height:38px;padding:7px 14px;border-radius:9px;border:1px solid {accent};background:#fff;color:{accent};
font:600 13px system-ui,sans-serif;text-decoration:none;cursor:pointer;white-space:nowrap}}.btn.solid{{background:{accent};color:#fff}}
form.inline{{display:inline;margin:0}}.note{{background:#f2efe7;border-radius:9px;padding:9px 12px;font-size:14px;margin:10px 0}}
/* the documents: name and amount on one line, the details under it, the buttons on their own line on a phone */
.docs{{border-top:1px solid #eee}}h4.grp{{font-size:13px;margin:26px 0 6px;color:#2a2a2a;text-transform:uppercase;letter-spacing:.04em}}
h4.grp .cnt{{color:#8a857c;font-weight:400}}
.doc{{display:grid;grid-template-columns:minmax(0,1fr) auto;column-gap:12px;row-gap:4px;padding:14px 0;border-bottom:1px solid #eee}}
.doc .who{{overflow-wrap:anywhere}}.doc .amt{{text-align:right;white-space:nowrap;font-variant-numeric:tabular-nums}}
.doc .sub{{grid-column:1/-1;color:#8a857c;font-size:12px;overflow-wrap:anywhere}}
.doc .acts{{grid-column:1/-1;display:flex;flex-wrap:wrap;gap:8px;align-items:center;margin-top:8px}}
.doc .acts .d{{display:inline}}
@media (min-width:760px){{
.doc{{grid-template-columns:minmax(0,1fr) 140px auto;align-items:center}}
.doc .who{{grid-column:1;grid-row:1}}.doc .sub{{grid-column:1;grid-row:2}}
.doc .amt{{grid-column:2;grid-row:1 / span 2}}.doc .acts{{grid-column:3;grid-row:1 / span 2;justify-content:flex-end;margin:0}}
.swipe{{display:none}}}}
@media (max-width:480px){{.card{{padding:20px 16px;margin:12px auto}}table{{font-size:14px}}h1{{font-size:20px}}
.btn{{flex:1 1 auto;justify-content:center}}}}
@media print{{.swipe{{display:none}}.grid{{display:block}}.card{{box-shadow:none;max-width:none}}.scroll{{overflow:visible}}table.cols{{min-width:0}}}}
{powered_by.pill_css(dark_mode=False)}
</style></head><body><div class="card">{logo_html}<h1>{title}</h1>
<p class="muted">Project account · {e(basis)} · as of {e(str(v.get('as_of') or ''))}</p>{summary}
<div class="grid">{''.join(parts)}</div>{extra_html}{_downloads(links)}<p class="muted" style="margin-top:20px">Questions? Contact {name}.</p></div>{powered_by.pill_html()}</body></html>"""
