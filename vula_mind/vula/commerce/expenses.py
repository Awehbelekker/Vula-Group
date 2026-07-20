"""
vula/commerce/expenses.py — expense CLAIMS: who paid for the business, what for, where it
belongs (category/account/project), and whether they must be reimbursed.

Two entry points: a receipt snapped on WhatsApp (owner or team) and a manual entry from the
dashboard. Both land in one place — `commerce_expenses` — categorised against the Books chart
(learned rules → AI → default) with input VAT backed out, optionally allocated to a project, and
tracked through a claim lifecycle (submitted → approved → reimbursed) when someone is out of pocket.

Builds on: accounting.categorize / vat_for / is_vat_registered (Books), doc_filing project list.
"""
from __future__ import annotations

import logging
import re
import uuid
from typing import Any, Dict, List, Optional

log = logging.getLogger(__name__)


def _client():
    from vula.commerce import service
    return service._client()


def _now():
    from vula.commerce import service
    return service._now()


# ── Project matching (so a claim lands on the right site/job) ──────────────────

def known_projects(tenant_id: str) -> List[str]:
    """Distinct project names Vula already knows for this tenant (field ops + prior expenses)."""
    names: set[str] = set()
    try:
        from vula.integrations import doc_filing
        for p in (doc_filing._field_projects(tenant_id) or []):
            if p:
                names.add(str(p))
    except Exception:
        pass
    db = _client()
    # Canonical project register (HPC Bokaap etc. live here).
    try:
        for r in (db.table("vula_projects").select("name").eq("tenant_id", tenant_id)
                  .limit(500).execute().data or []):
            v = (r.get("name") or "").strip()
            if v:
                names.add(v)
    except Exception:
        pass
    for table, col in (("commerce_expenses", "project"), ("commerce_invoices", "project")):
        try:
            for r in (db.table(table).select(col).eq("tenant_id", tenant_id)
                      .not_.is_(col, "null").limit(500).execute().data or []):
                v = (r.get(col) or "").strip()
                if v:
                    names.add(v)
        except Exception:
            pass
    return sorted(names)


def match_project(tenant_id: str, text: str, projects: Optional[List[str]] = None) -> Optional[str]:
    """Loose-match a project from free text (e.g. 'for the bokaap site') to a known project name.
    Returns the canonical project name, or None if nothing matches confidently."""
    text = (text or "").strip().lower()
    if not text:
        return None
    projects = projects if projects is not None else known_projects(tenant_id)
    # exact / substring first
    for p in projects:
        if p.lower() in text or text in p.lower():
            return p
    # token overlap (e.g. "HPC Bokaap" ~ "hpc_bokaap" ~ "bokaap hpc")
    def toks(s):
        return {t for t in re.findall(r"[a-z0-9]{2,}", s.lower()) if t not in _PROJ_STOP}
    want = toks(text)
    best, best_score = None, 0
    for p in projects:
        score = len(want & toks(p))
        if score > best_score:
            best, best_score = p, score
    return best if best_score >= 1 else None


_PROJ_STOP = {"the", "for", "site", "project", "job", "this", "that", "on", "at", "to"}


# ── Company cards (whose money?) ───────────────────────────────────────────────

def list_cards(tenant_id: str, active_only: bool = True) -> List[dict]:
    try:
        q = _client().table("commerce_cards").select("*").eq("tenant_id", tenant_id)
        if active_only:
            q = q.eq("active", True)
        return q.execute().data or []
    except Exception as exc:
        log.debug("list_cards skipped (run migration 061?): %s", exc)
        return []


def upsert_card(tenant_id: str, last4: str, label: Optional[str] = None) -> dict:
    last4 = re.sub(r"\D", "", last4 or "")[-4:]
    if len(last4) != 4:
        raise ValueError("last4 must be 4 digits")
    res = _client().table("commerce_cards").upsert(
        {"tenant_id": tenant_id, "last4": last4, "label": label, "active": True},
        on_conflict="tenant_id,last4").execute()
    return (res.data or [{}])[0]


def delete_card(tenant_id: str, card_id: str) -> None:
    _client().table("commerce_cards").delete().eq("tenant_id", tenant_id).eq("id", card_id).execute()


def resolve_paid_with(tenant_id: str, *, card_last4: Optional[str] = None,
                      payment_method: Optional[str] = None) -> Optional[str]:
    """Whose money, from what the receipt shows: registered company card → company_card;
    a different card → personal; cash → cash; nothing readable → None (ask).
    Suffix-tolerant: Capitec prints 'Card 572' (3 digits) while slips may show 4."""
    digits = re.sub(r"\D", "", card_last4 or "")[-4:]
    if len(digits) >= 3:
        for c in list_cards(tenant_id):
            reg = (c.get("last4") or "")
            if reg.endswith(digits) or digits.endswith(reg[-3:]):
                return "company_card"
        return "personal"
    if (payment_method or "").lower() == "cash":
        return "cash"
    return None


# ── Create a claim ─────────────────────────────────────────────────────────────

async def create_claim(
    tenant_id: str,
    *,
    amount_cents: int,
    description: str,
    supplier: Optional[str] = None,
    supplier_id: Optional[str] = None,
    date: Optional[str] = None,
    vat_cents: Optional[int] = None,
    category: Optional[str] = None,
    account_code: Optional[str] = None,
    project: Optional[str] = None,
    paid_by: Optional[str] = None,
    paid_by_name: Optional[str] = None,
    reimbursable: bool = False,
    paid_with: Optional[str] = None,      # 'company_card' | 'personal' | 'cash' | None=unknown
    card_last4: Optional[str] = None,
    channel: str = "dashboard",
    receipt_doc_id: Optional[str] = None,
    receipt_url: Optional[str] = None,
    notes: Optional[str] = None,
    auto_categorize: bool = True,
    dedupe: bool = True,
) -> Dict[str, Any]:
    """Insert an expense claim, auto-categorising against the Books chart + backing out input VAT.
    Returns the created row plus `needs_project` (True when it couldn't be allocated to a site).
    If `dedupe` and an expense with the same amount + supplier + date already exists, returns that
    existing row with `duplicate=True` and creates nothing."""
    from vula.commerce import accounting
    db = _client()
    amount_cents = int(amount_cents or 0)

    # Sanitise the date early (also used by the dup check below).
    _d = (date or "").strip()[:10]
    if not re.match(r"^\d{4}-\d{2}-\d{2}$", _d):
        _d = _now()[:10]

    # Duplicate guard. The strongest key is the source receipt/media id: the SAME inbound photo
    # (Meta redelivers webhooks, and workers don't share memory) must only ever book once — and
    # this survives the OCR reading a different total/vendor each delivery. Fall back to
    # (amount + date) or (supplier + date) for a genuinely re-sent receipt.
    if dedupe:
        try:
            dup = []
            if receipt_doc_id:
                dup = (db.table("commerce_expenses").select("*").eq("tenant_id", tenant_id)
                       .eq("receipt_doc_id", receipt_doc_id).limit(1).execute().data or [])
            if not dup and amount_cents > 0:
                dup = (db.table("commerce_expenses").select("*").eq("tenant_id", tenant_id)
                       .eq("date", _d).eq("amount_cents", amount_cents).limit(1).execute().data or [])
            if not dup and amount_cents > 0 and supplier:
                dup = (db.table("commerce_expenses").select("*").eq("tenant_id", tenant_id)
                       .eq("date", _d).eq("supplier", supplier).limit(1).execute().data or [])
            if dup:
                out = dup[0]
                out["duplicate"] = True
                out["needs_project"] = False
                return out
        except Exception as exc:
            log.debug("expense dedupe check skipped: %s", exc)

    accounts = accounting.ensure_chart(tenant_id)
    txn = {"description": f"{supplier or ''} {description or ''}".strip(),
           "reference": supplier or "", "amount_cents": amount_cents}

    if not account_code:
        try:
            # An expense is never income — only offer expense accounts to the categoriser.
            exp_accounts = [a for a in accounts if a.get("type") == "expense"] or accounts
            cat = await accounting.categorize(tenant_id, txn, exp_accounts)
            account_code = cat.get("account_code")
        except Exception as exc:
            log.warning("expense categorize failed: %s", exc)
            account_code = None
    acct = next((a for a in accounts if a["code"] == account_code), None)
    category = category or (acct.get("name") if acct else "other")

    if vat_cents is None:
        try:
            vat_cents = accounting.vat_for(acct, amount_cents, accounting.is_vat_registered(tenant_id))
        except Exception:
            vat_cents = 0

    # Whose money decides reimbursement: business money (company card) is never owed back;
    # a personal card definitely is. Cash/unknown keeps the caller's flag.
    if paid_with is None and card_last4:
        paid_with = resolve_paid_with(tenant_id, card_last4=card_last4)
    if paid_with == "company_card":
        reimbursable = False
    elif paid_with == "personal":
        reimbursable = True

    row = {
        "id": str(uuid.uuid4()), "tenant_id": tenant_id,
        "date": _d,
        "description": description or (supplier or "Expense"),
        "amount_cents": amount_cents, "vat_cents": int(vat_cents or 0),
        "supplier": supplier, "supplier_id": supplier_id, "category": category, "account_code": account_code,
        "project": project, "paid_by": paid_by, "paid_by_name": paid_by_name,
        "reimbursable": bool(reimbursable), "status": "submitted",
        "paid_with": paid_with, "card_last4": re.sub(r"\D", "", card_last4 or "")[-4:] or None,
        "channel": channel, "receipt_doc_id": receipt_doc_id, "receipt_url": receipt_url,
        "notes": notes, "source": channel, "updated_at": _now(),
    }
    # Tolerate a DB that hasn't had migrations 060/061 yet (drop the new columns, retry).
    try:
        res = db.table("commerce_expenses").insert(row).execute()
    except Exception as exc:
        log.warning("expense insert retry (pre-060/061?): %s", exc)
        for k in ("paid_by", "paid_by_name", "reimbursable", "channel", "paid_with",
                  "card_last4", "receipt_doc_id", "notes", "updated_at", "supplier_id"):
            row.pop(k, None)
        res = db.table("commerce_expenses").insert(row).execute()
    out = (res.data or [row])[0]
    out["needs_project"] = (not project) and bool(known_projects(tenant_id))
    return out


# ── Lifecycle ──────────────────────────────────────────────────────────────────

def list_claims(tenant_id: str, *, status: Optional[str] = None, reimbursable: Optional[bool] = None,
                project: Optional[str] = None, since: Optional[str] = None,
                until: Optional[str] = None, limit: int = 500) -> List[dict]:
    q = (_client().table("commerce_expenses").select("*").eq("tenant_id", tenant_id)
         .order("date", desc=True).limit(limit))
    if status:
        q = q.eq("status", status)
    if reimbursable is not None:
        q = q.eq("reimbursable", reimbursable)
    if project:
        q = q.eq("project", project)
    if since:
        q = q.gte("date", since)
    if until:
        q = q.lte("date", until)
    try:
        return q.execute().data or []
    except Exception as exc:
        log.warning("list_claims failed: %s", exc)
        return []


def set_status(tenant_id: str, expense_id: str, status: str) -> dict:
    patch: Dict[str, Any] = {"status": status, "updated_at": _now()}
    if status == "reimbursed":
        patch["reimbursed_at"] = _now()
    if status == "paid":
        patch["paid_at"] = _now()
    res = (_client().table("commerce_expenses").update(patch)
           .eq("tenant_id", tenant_id).eq("id", expense_id).execute())
    return (res.data or [{}])[0]


def assign(tenant_id: str, expense_id: str, *, project: Optional[str] = None,
           account_code: Optional[str] = None, category: Optional[str] = None) -> dict:
    """Owner corrects/allocates a claim → apply + LEARN the account rule for next time."""
    db = _client()
    row = (db.table("commerce_expenses").select("*").eq("tenant_id", tenant_id)
           .eq("id", expense_id).limit(1).execute().data or [None])[0]
    patch: Dict[str, Any] = {"updated_at": _now()}
    if project is not None:
        patch["project"] = project or None
    if account_code:
        patch["account_code"] = account_code
        if category is None and row:
            from vula.commerce import accounting
            acct = next((a for a in accounting.ensure_chart(tenant_id)
                         if a["code"] == account_code), None)
            patch["category"] = acct.get("name") if acct else category
    if category is not None:
        patch["category"] = category
    res = (db.table("commerce_expenses").update(patch)
           .eq("tenant_id", tenant_id).eq("id", expense_id).execute())
    # Learn the categorisation so similar receipts auto-file.
    if account_code and row:
        try:
            from vula.commerce import accounting
            accounting.learn_category_rule(
                tenant_id,
                {"description": f"{row.get('supplier') or ''} {row.get('description') or ''}",
                 "reference": row.get("supplier") or ""},
                account_code)
        except Exception:
            pass
    return (res.data or [{}])[0]


# ── Report (accountant's record + reimbursements owed) ─────────────────────────

def report(tenant_id: str, *, since: Optional[str] = None, until: Optional[str] = None,
           project: Optional[str] = None) -> Dict[str, Any]:
    rows = list_claims(tenant_id, since=since, until=until, project=project, limit=2000)
    by_cat: Dict[str, int] = {}
    by_person: Dict[str, Dict[str, Any]] = {}
    total = vat = owed = 0
    for r in rows:
        amt = int(r.get("amount_cents") or 0)
        total += amt
        vat += int(r.get("vat_cents") or 0)
        cat = r.get("category") or "other"
        by_cat[cat] = by_cat.get(cat, 0) + amt
        who = r.get("paid_by_name") or r.get("paid_by") or "—"
        p = by_person.setdefault(who, {"name": who, "total_cents": 0, "owed_cents": 0, "count": 0})
        p["total_cents"] += amt
        p["count"] += 1
        if r.get("reimbursable") and r.get("status") != "reimbursed":
            p["owed_cents"] += amt
            owed += amt
    return {
        "total_cents": total, "vat_cents": vat, "reimbursable_owed_cents": owed,
        "count": len(rows),
        "by_category": [{"category": k, "amount_cents": v} for k, v in
                        sorted(by_cat.items(), key=lambda x: -x[1])],
        "by_person": sorted(by_person.values(), key=lambda x: -x["total_cents"]),
    }
