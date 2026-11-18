"""vula/commerce/ledger_v2.py — one book you can trust (Phase 1 of Xero-quality books).

17 Nov 2026 (Ian: "finish Vula's own books first"). The journal becomes the one source of truth,
kept on an accrual basis with a cash view of the same entries (migration 288):

  - a line tagged `basis` 'accrual' or 'cash' counts in that view only; untagged counts in both.
    Each view balances on its own. An invoice receipt: Dr bank (both) / Cr debtors (accrual) /
    Cr sales + VAT (cash) — the accrual view already has the sale from when it was issued.
  - one live entry per source. Posting the same source again is a no-op; correcting it is a
    reversal of the live entry plus the source posted again as its next version (the journal stays
    append-only, migration 253).
  - every posting that fails is kept (journal_post_failures) to retry and to show — never silent.

This module only builds, posts, reverses and reads entries. Nothing calls it for a business until
that business's `books_v2` switch is on (vula/features.py), and a business's history is rebuilt only
after its books audit (vula/commerce/books_audit.py) is signed off. Money is integer cents throughout,
worked out in code.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

log = logging.getLogger(__name__)

# Accounts the trusted book needs beyond the default chart (accounting.DEFAULT_CHART), added only
# for a business whose books_v2 is on. (code, name, type, vat_treatment, deductible, sort)
LEDGER_CHART = [
    ("clearing",               "Payouts & money in transit",      "asset",     "none", False, 6),
    ("transfer_clearing",      "Transfers between own accounts",  "asset",     "none", False, 7),
    ("cash_on_hand",           "Cash on hand",                    "asset",     "none", False, 8),
    ("suspense",               "To review",                       "asset",     "none", False, 9),
    ("staff_reimbursements",   "Owed to staff (claims)",          "liability", "none", False, 175),
    ("owner_loan",             "Loan from owner",                 "liability", "none", False, 176),
    ("owner_capital",          "Owner's capital",                 "equity",    "none", False, 161),
    ("opening_balance_equity", "Opening balances",                "equity",    "none", False, 162),
]
# Never offered when someone categorises a bank line or an expense; the books use them. (clearing
# is offered for money in only — a card-machine or payment-link payout of sales already recorded —
# see accounting.usable_for.)
SYSTEM_CODES = frozenset({"suspense", "opening_balance_equity", "staff_reimbursements"})
VIEWS = ("accrual", "cash")


class Unbalanced(ValueError):
    pass


def _client():
    from vula.commerce import service
    return service._client()


def _today() -> str:
    return datetime.now(timezone.utc).date().isoformat()


def is_on(tenant_id: str) -> bool:
    from vula import features
    try:
        return features.on(tenant_id, "books_v2")
    except Exception:  # noqa: BLE001
        return False


def ensure_ledger_chart(tenant_id: str) -> None:
    """Add the trusted book's accounts this business doesn't have yet."""
    from vula.commerce import accounting
    have = {a["code"] for a in accounting.ensure_chart(tenant_id)}
    missing = [{"tenant_id": tenant_id, "code": c, "name": n, "type": t, "vat_treatment": v,
                "deductible": d, "sort": s} for (c, n, t, v, d, s) in LEDGER_CHART if c not in have]
    if missing:
        _client().table("commerce_accounts").insert(missing).execute()


# ── building and checking lines ──────────────────────────────────────────────

def line(code: str, debit: int = 0, credit: int = 0, *, basis: Optional[str] = None,
         bank_account_id: Optional[str] = None, memo: Optional[str] = None) -> Dict[str, Any]:
    if basis not in (None, "accrual", "cash"):
        raise ValueError(f"basis must be accrual, cash or None, not {basis!r}")
    d, c = int(debit), int(credit)
    if d < 0 or c < 0 or (d and c):
        raise ValueError("a line is a debit or a credit, never both or negative")
    out: Dict[str, Any] = {"account_code": code, "debit_cents": d, "credit_cents": c}
    if basis:
        out["basis"] = basis
    if bank_account_id:
        out["bank_account_id"] = str(bank_account_id)
    if memo:
        out["memo"] = memo[:300]
    return out


def view_totals(lines: List[Dict[str, Any]]) -> Dict[str, tuple]:
    """(debits, credits) per view."""
    out = {}
    for view in VIEWS:
        dr = sum(int(ln.get("debit_cents") or 0) for ln in lines if ln.get("basis") in (None, "", view))
        cr = sum(int(ln.get("credit_cents") or 0) for ln in lines if ln.get("basis") in (None, "", view))
        out[view] = (dr, cr)
    return out


def check(lines: List[Dict[str, Any]]) -> None:
    """Each view balances, and the entry is worth something — the database checks it again."""
    totals = view_totals(lines)
    for view, (dr, cr) in totals.items():
        if dr != cr:
            raise Unbalanced(f"{view} view doesn't balance: debits {dr} ≠ credits {cr}")
    if not any(dr for dr, _ in totals.values()):
        raise Unbalanced("an entry worth nothing")


def mirror(lines: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """The reversing lines: every debit a credit and back, same view and bank account."""
    return [{**ln, "debit_cents": int(ln.get("credit_cents") or 0), "credit_cents": int(ln.get("debit_cents") or 0)}
            for ln in lines]


# ── posting ──────────────────────────────────────────────────────────────────

def _failed(tenant_id: str, source_type: str, source_id: Optional[str], payload: Dict[str, Any], exc: Exception) -> None:
    log.warning("ledger v2 posting failed (%s/%s): %s", source_type, source_id, exc)
    try:
        _client().table("journal_post_failures").insert({
            "tenant_id": tenant_id, "source_type": source_type, "source_id": source_id,
            "payload": payload, "error": str(exc)[:500]}).execute()
    except Exception as exc2:  # noqa: BLE001
        log.error("LEDGER FAILURE NOT RECORDED (%s/%s): %s — run migration 288?", source_type, source_id, exc2)


def post(tenant_id: str, *, entry_date: Optional[str], description: str, source_type: str,
         source_id: Optional[str], lines: List[Dict[str, Any]], doc_id: Optional[str] = None,
         bank_txn_id: Optional[str] = None, reverses_entry_id: Optional[str] = None) -> Optional[str]:
    """Post one entry; returns its id (the live one if the source is already posted), or None when it
    couldn't be posted — the failure is kept. Never raises: a posting must not block the business
    action behind it."""
    meta = {k: str(v) for k, v in (("doc_id", doc_id), ("bank_txn_id", bank_txn_id),
                                   ("reverses_entry_id", reverses_entry_id)) if v}
    payload = {"p_tenant_id": tenant_id, "p_entry_date": (entry_date or _today())[:10],
               "p_description": (description or source_type)[:500], "p_source_type": source_type,
               "p_source_id": str(source_id) if source_id is not None else None,
               "p_lines": lines, "p_meta": meta}
    try:
        check(lines)
        got = _client().rpc("post_journal_entry_v2", payload).execute().data
        return str(got) if got else None
    except Exception as exc:  # noqa: BLE001
        _failed(tenant_id, source_type, payload["p_source_id"], payload, exc)
        return None


def live_entry(tenant_id: str, source_type: str, source_id: str) -> Optional[Dict[str, Any]]:
    """The entry that currently stands for a source (the newest version nothing reverses)."""
    db = _client()
    rows = (db.table("journal_entries").select("id,entry_date,version,reverses_entry_id,doc_id,bank_txn_id")
            .eq("tenant_id", tenant_id).eq("source_type", source_type).eq("source_id", str(source_id))
            .order("version", desc=True).execute().data or [])
    if not rows:
        return None
    reversed_ids = {r["reverses_entry_id"] for r in
                    (db.table("journal_entries").select("reverses_entry_id").eq("tenant_id", tenant_id)
                     .in_("reverses_entry_id", [r["id"] for r in rows]).execute().data or [])}
    return next((r for r in rows if r["id"] not in reversed_ids), None)


def entry_lines(tenant_id: str, entry_id: str) -> List[Dict[str, Any]]:
    """An entry's lines, with account codes (only this business's entry)."""
    db = _client()
    head = (db.table("journal_entries").select("id").eq("tenant_id", tenant_id).eq("id", entry_id)
            .limit(1).execute().data or [])
    if not head:
        return []
    lines = (db.table("journal_lines").select("account_id,debit_cents,credit_cents,memo,basis,bank_account_id")
             .eq("journal_entry_id", entry_id).execute().data or [])
    codes = {a["id"]: a["code"] for a in (db.table("commerce_accounts").select("id,code")
                                           .eq("tenant_id", tenant_id).execute().data or [])}
    out = []
    for ln in lines:
        item = {"account_code": codes.get(ln["account_id"]), "debit_cents": int(ln.get("debit_cents") or 0),
                "credit_cents": int(ln.get("credit_cents") or 0)}
        for k in ("basis", "bank_account_id", "memo"):
            if ln.get(k):
                item[k] = ln[k]
        out.append(item)
    return out


def reverse(tenant_id: str, entry_id: str, *, entry_date: Optional[str] = None,
            reason: str = "corrected") -> Optional[str]:
    """Reverse one entry with its mirror image (dated the original's date unless told otherwise)."""
    db = _client()
    head = (db.table("journal_entries").select("id,entry_date,description").eq("tenant_id", tenant_id)
            .eq("id", entry_id).limit(1).execute().data or [])
    if not head:
        return None
    lines = entry_lines(tenant_id, entry_id)
    if not lines:
        return None
    return post(tenant_id, entry_date=entry_date or str(head[0]["entry_date"]),
                description=f"Reversal ({reason}): {head[0].get('description') or ''}"[:500],
                source_type="reversal", source_id=entry_id, lines=mirror(lines), reverses_entry_id=entry_id)


def _same(a: List[Dict[str, Any]], b: List[Dict[str, Any]]) -> bool:
    key = lambda ln: (ln.get("account_code"), int(ln.get("debit_cents") or 0), int(ln.get("credit_cents") or 0),  # noqa: E731
                      ln.get("basis") or None, ln.get("bank_account_id") or None)
    return sorted(map(key, a)) == sorted(map(key, b))


def repost(tenant_id: str, *, source_type: str, source_id: str, entry_date: Optional[str], description: str,
           lines: List[Dict[str, Any]], doc_id: Optional[str] = None, bank_txn_id: Optional[str] = None,
           reason: str = "corrected") -> Optional[str]:
    """Make the source's live entry say `lines`: nothing when it already does; otherwise reverse it and
    post again (the next version). A source with nothing posted is simply posted."""
    current = live_entry(tenant_id, source_type, str(source_id))
    if current:
        if _same(entry_lines(tenant_id, current["id"]), lines) and str(current.get("entry_date")) == (entry_date or "")[:10]:
            return current["id"]
        if reverse(tenant_id, current["id"], reason=reason) is None:
            return None
    return post(tenant_id, entry_date=entry_date, description=description, source_type=source_type,
                source_id=source_id, lines=lines, doc_id=doc_id, bank_txn_id=bank_txn_id)


def unpost(tenant_id: str, source_type: str, source_id: str, reason: str = "removed") -> Optional[str]:
    """The source no longer stands (an invoice cancelled, a bank line set aside): reverse its live entry."""
    current = live_entry(tenant_id, source_type, str(source_id))
    return reverse(tenant_id, current["id"], reason=reason) if current else None


# ── reading ──────────────────────────────────────────────────────────────────

def balances(tenant_id: str, *, since: Optional[str] = None, until: Optional[str] = None,
             basis: str = "accrual") -> List[Dict[str, Any]]:
    """Debits and credits per account (and bank account) for one view, summed in the database."""
    if basis not in VIEWS:
        raise ValueError("basis is accrual or cash")
    rows = _client().rpc("ledger_balances", {"p_tenant_id": tenant_id, "p_from": since, "p_to": until,
                                             "p_basis": basis}).execute().data or []
    return [{**r, "debit_cents": int(r.get("debit_cents") or 0), "credit_cents": int(r.get("credit_cents") or 0)}
            for r in rows]


def trial_balance(tenant_id: str, *, until: Optional[str] = None, basis: str = "accrual") -> Dict[str, Any]:
    """Per account: net debit or credit; and whether the view balances."""
    per: Dict[str, Dict[str, Any]] = {}
    for r in balances(tenant_id, until=until, basis=basis):
        acc = per.setdefault(r["account_code"], {"account_code": r["account_code"], "name": r.get("account_name"),
                                                  "type": r.get("account_type"), "debit_cents": 0, "credit_cents": 0})
        acc["debit_cents"] += r["debit_cents"]
        acc["credit_cents"] += r["credit_cents"]
    rows = sorted(per.values(), key=lambda a: a["account_code"] or "")
    dr = sum(a["debit_cents"] for a in rows)
    cr = sum(a["credit_cents"] for a in rows)
    return {"basis": basis, "until": until, "accounts": rows, "debit_cents": dr, "credit_cents": cr,
            "balanced": dr == cr}


def open_failures(tenant_id: str) -> List[Dict[str, Any]]:
    try:
        return (_client().table("journal_post_failures").select("id,source_type,source_id,error,attempts,created_at")
                .eq("tenant_id", tenant_id).is_("resolved_at", "null").order("created_at", desc=True)
                .limit(200).execute().data or [])
    except Exception:  # noqa: BLE001
        return []
