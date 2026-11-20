"""vula/commerce/books_rebuild.py — a business's history into the one trusted book (Phase 1, PR 3).

18 Nov 2026 (Ian: "finish Vula's own books first … make sure all DIGG's data is used accurately").
New money events post to the one book once a business is on books_v2 (ledger_events). This rebuilds
everything that happened before, from the records themselves — invoices and their payments and credit
notes, supplier bills and their payments, orders, expenses and staff claims, every live bank line, the
card machine, and an opening balance per bank account — and compares that with what the journal holds.

  plan(tenant, accrual_from)   the dry run: writes nothing. What would be posted, corrected or taken out,
                               the trial balance on both views before and after, P&L per month, debtors
                               and creditors against what's still owed, suspense, money in transit, the bank
                               reconciliation per account, the books audit's own warnings, and a plan_hash.
  commit(tenant, accrual_from, plan_hash, person)
                               only for the Master operator, only once the business's books audit is signed
                               off, and only while plan_hash still matches (nothing moved since the dry run).
                               Saves the books settings, turns books_v2 on, applies the plan in date order,
                               then plans again: nothing left to do and both views balanced, or it says so.

Every entry is keyed by its source, so committing twice changes nothing. Old cash-only entries the one book
doesn't use any more are taken out by reversal (the journal stays append-only). Opening balances brought in
from Xero, and reversals themselves, are never touched. Money is integer cents, in code; no model.
"""
from __future__ import annotations

import hashlib
import json
import logging
from typing import Any, Dict, Iterable, List, Optional, Tuple

from vula.commerce import bank_recon
from vula.commerce import ledger_events as ev
from vula.commerce import ledger_v2 as lv

log = logging.getLogger(__name__)

KEEP = frozenset({"opening_balance", "opening_balance_reversal", "reversal"})
POSTED_EXPENSE = frozenset({"submitted", "approved", "paid", "reimbursed"})
NOT_PAID_ORDER = frozenset({"pending_payment", "cancelled", "draft", "abandoned"})


def _client():
    from vula.commerce import service
    return service._client()


def _rows(table: str, tenant_id: str, select: str = "*") -> List[Dict[str, Any]]:
    from vula.commerce.ledger import _all_pages
    db = _client()
    return _all_pages(lambda: db.table(table).select(select).eq("tenant_id", tenant_id).order("id"))


def _safe_rows(table: str, tenant_id: str, select: str = "*") -> List[Dict[str, Any]]:
    try:
        return _rows(table, tenant_id, select)
    except Exception as exc:  # noqa: BLE001 — a table a business doesn't use (e.g. no card machine)
        log.debug("rebuild: %s not read for %s: %s", table, tenant_id, exc)
        return []


# ── what the books should hold ───────────────────────────────────────────────

def _load(tenant_id: str) -> Dict[str, Any]:
    return {"invoices": _rows("commerce_invoices", tenant_id),
            "payments": _safe_rows("commerce_invoice_payments", tenant_id),
            "orders": _safe_rows("commerce_orders", tenant_id),
            "expenses": _safe_rows("commerce_expenses", tenant_id),
            "bank": _safe_rows("commerce_bank_transactions", tenant_id),
            "line_docs": _safe_rows("commerce_bank_line_documents", tenant_id),
            "bank_accounts": _safe_rows("commerce_bank_accounts", tenant_id, "id,account_number"),
            "tap_payments": _safe_rows("kb_payments", tenant_id),
            "tap_lines": _safe_rows("kb_ledger_lines", tenant_id),
            "tap_sessions": _safe_rows("kb_sessions", tenant_id, "id,bill_cents")}


def _order_paid_by_invoice(inv: Dict[str, Any], pays: List[Dict[str, Any]]) -> bool:
    """Did this order's own invoice carry the sale? (paid through the invoice: payments recorded on it,
    or marked paid with less than its total recorded — the order was then flipped without posting)."""
    from vula.commerce import order_deposit
    if order_deposit.kind_of(inv):
        return True
    if pays:
        return True
    return (inv.get("status") == "paid"
            and int(inv.get("total_paid_cents") or 0) < int(inv.get("total_cents") or 0))


def target(tenant_id: str, accrual_from: Optional[str] = None,
           data: Optional[Dict[str, Any]] = None) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """Every entry the one book should hold for this business, and the source data it came from."""
    data = data or _load(tenant_id)
    settings = {**ev._read_settings(tenant_id), **({"accrual_from": accrual_from} if accrual_from else {})}
    pays: Dict[str, List[Dict[str, Any]]] = {}
    for p in data["payments"]:
        pays.setdefault(str(p.get("invoice_id")), []).append(p)
    for v in pays.values():
        v.sort(key=lambda p: (str(p.get("paid_at") or ""), str(p.get("id"))))
    invoices = {str(i["id"]): i for i in data["invoices"]}
    accounts = {str(a.get("account_number")): str(a["id"]) for a in data["bank_accounts"]
                if a.get("account_number") and a.get("id")}

    with ev.capture({("settings", tenant_id): settings}) as entries:
        def tag(start: int, kind: str, row: Dict[str, Any]) -> None:
            for e in entries[start:]:
                e.setdefault("doc", {"kind": kind, "id": str(row.get("id")),
                                     "label": str(row.get("invoice_number") or row.get("display_id")
                                                  or row.get("description") or row.get("supplier") or "")[:60],
                                     "date": str(row.get("issue_date") or row.get("date") or row.get("txn_date")
                                                 or row.get("updated_at") or "")[:10]})

        for inv in data["invoices"]:
            start = len(entries)
            inv = {"tenant_id": tenant_id, **inv}
            doc_type = inv.get("doc_type") or "invoice"
            inbound = (inv.get("direction") or "outbound") == "inbound"
            if doc_type == "credit_note" and not inbound and inv.get("credited_invoice_id"):
                src = invoices.get(str(inv["credited_invoice_id"]))
                if src:
                    against = sum(int(p.get("amount_cents") or 0) for p in pays.get(str(src["id"]), [])
                                  if p.get("payment_method") == "credit_note"
                                  and (p.get("note") or "") == f"Credit note {inv.get('invoice_number')}")
                    refund = int(inv.get("total_cents") or 0) - against
                    if refund > 0:
                        ev.invoice_refund(tenant_id, {"tenant_id": tenant_id, **src}, inv, refund)
                tag(start, "credit_note", inv)
                continue
            if doc_type != "invoice":
                continue
            ev.invoice_state(tenant_id, inv)
            before = 0
            for p in pays.get(str(inv["id"]), []):
                amt = int(p.get("amount_cents") or 0)
                at = {**inv, "total_paid_cents": before}
                if p.get("payment_method") == "credit_note" and not inbound:
                    ev.credit_note(tenant_id, at, {"id": p["id"], "invoice_number": (p.get("note") or "")[12:],
                                                   "issue_date": p.get("paid_at")}, amt)
                elif inbound:
                    ev.bill_payment(tenant_id, at, amount=amt, paid_at=p.get("paid_at"), source_type="bill_payment",
                                    source_id=str(p["id"]), method=p.get("payment_method"))
                else:
                    ev.invoice_payment(tenant_id, at, amount=amt, paid_at=p.get("paid_at"),
                                       source_type="invoice_payment", source_id=str(p["id"]),
                                       method=p.get("payment_method"))
                before += amt
            if (inv.get("status") or "") == "paid":
                # what was paid beyond the payments on record — counted from the payments themselves,
                # not the stored total_paid_cents (DIGG had bills marked paid in full with no payment
                # recorded). An order's own invoice keeps its stored figure: the order carried the sale.
                paid_doc = inv if (inv.get("order_id") and not inbound) else {**inv, "total_paid_cents": before}
                (ev.bill_paid if inbound else ev.invoice_paid)(tenant_id, paid_doc)
            tag(start, "bill" if inbound else ("credit_note" if doc_type == "credit_note" else "invoice"), inv)

        by_order: Dict[str, List[Dict[str, Any]]] = {}
        for inv in data["invoices"]:
            if inv.get("order_id") and (inv.get("direction") or "outbound") == "outbound":
                by_order.setdefault(str(inv["order_id"]), []).append(inv)
        for o in data["orders"]:
            status = (o.get("status") or "").lower()
            if status in NOT_PAID_ORDER or int(o.get("total_cents") or 0) <= 0:
                continue
            if any(_order_paid_by_invoice(i, pays.get(str(i["id"]), [])) for i in by_order.get(str(o["id"]), [])):
                continue          # its invoice carried the sale
            start = len(entries)
            ev.order_paid(tenant_id, o)
            if status == "refunded":
                ev.order_refund(tenant_id, o, int(o.get("total_cents") or 0), str(o["id"]))
            tag(start, "order", o)

        for e in data["expenses"]:
            if (e.get("status") or "") in POSTED_EXPENSE:
                start = len(entries)
                ev.expense(tenant_id, e)
                if e.get("status") == "reimbursed":
                    ev.expense_reimbursed(tenant_id, e)
                tag(start, "expense", e)

        for r in data["bank"]:
            if ev.is_live(r):
                start = len(entries)
                ev.bank_line(tenant_id, r, accounts)
                tag(start, "bank_line", r)

        sessions = {str(s["id"]): s for s in data["tap_sessions"]}
        tap_lines: Dict[str, List[Dict[str, Any]]] = {}
        for ln in data["tap_lines"]:
            if not ln.get("reverses"):
                tap_lines.setdefault(str(ln.get("payment_id")), []).append(ln)
        for p in data["tap_payments"]:
            lines = tap_lines.get(str(p["id"]))
            if lines and (p.get("status") or "") in ("succeeded", "refunded", "partially_refunded"):
                ev.tap_payment(tenant_id, p, lines,
                               bill_cents=int((sessions.get(str(p.get("session_id"))) or {}).get("bill_cents") or 0))

    entries = entries + bank_recon.opening_entries(tenant_id, data["bank"], accounts)
    return entries, {"data": data, "accounts": accounts, "settings": settings}


# ── what the books hold now ──────────────────────────────────────────────────

def live_journal(tenant_id: str) -> Tuple[Dict[Tuple[str, str], Dict[str, Any]], List[Dict[str, Any]]]:
    """(the live entry per source with its lines, every line of every entry — for the trial balance)."""
    from vula.commerce.ledger import _all_pages
    db = _client()
    heads = _all_pages(lambda: db.table("journal_entries")
                       .select("id,entry_date,source_type,source_id,version,reverses_entry_id")
                       .eq("tenant_id", tenant_id).order("id"))
    codes = {a["id"]: a["code"] for a in (db.table("commerce_accounts").select("id,code")
                                           .eq("tenant_id", tenant_id).execute().data or [])}
    lines_by: Dict[str, List[Dict[str, Any]]] = {}
    ids = [h["id"] for h in heads]
    for i in range(0, len(ids), 150):
        chunk = ids[i:i + 150]
        for ln in _all_pages(lambda c=chunk: db.table("journal_lines")
                             .select("id,journal_entry_id,account_id,debit_cents,credit_cents,basis,bank_account_id")
                             .in_("journal_entry_id", c).order("id")):
            item = {"account_code": codes.get(ln["account_id"]), "debit_cents": int(ln.get("debit_cents") or 0),
                    "credit_cents": int(ln.get("credit_cents") or 0)}
            for k in ("basis", "bank_account_id"):
                if ln.get(k):
                    item[k] = ln[k]
            lines_by.setdefault(ln["journal_entry_id"], []).append(item)
    reversed_ids = {h.get("reverses_entry_id") for h in heads if h.get("reverses_entry_id")}
    live: Dict[Tuple[str, str], Dict[str, Any]] = {}
    for h in sorted(heads, key=lambda h: int(h.get("version") or 1)):
        if h["source_type"] == "reversal" or h["id"] in reversed_ids or h.get("source_id") is None:
            continue
        live[(h["source_type"], str(h["source_id"]))] = {**h, "lines": lines_by.get(h["id"], [])}
    every = [ln for h in heads for ln in lines_by.get(h["id"], [])]
    return live, every


# ── the plan ─────────────────────────────────────────────────────────────────

def diff(entries: List[Dict[str, Any]], live: Dict[Tuple[str, str], Dict[str, Any]]) -> List[Dict[str, Any]]:
    want: Dict[Tuple[str, str], Dict[str, Any]] = {}
    for e in entries:
        want[(e["source_type"], str(e["source_id"]))] = e      # the last word on a source wins
    actions = []
    for key, e in sorted(want.items(), key=lambda kv: (kv[1]["entry_date"], kv[0])):
        cur = live.get(key)
        if cur is None:
            actions.append({"kind": "new", "entry": e})
        elif not lv._same(cur["lines"], e["lines"]) or str(cur.get("entry_date"))[:10] != e["entry_date"]:
            actions.append({"kind": "changed", "entry": e, "was": {"id": cur["id"], "lines": cur["lines"],
                                                                  "entry_date": str(cur.get("entry_date"))[:10]}})
    for key, cur in sorted(live.items()):
        if key not in want and cur["source_type"] not in KEEP:
            actions.append({"kind": "remove", "source_type": key[0], "source_id": key[1],
                            "was": {"id": cur["id"], "lines": cur["lines"], "entry_date": str(cur.get("entry_date"))[:10]}})
    return actions


def _canon(lines: Iterable[Dict[str, Any]]) -> List[tuple]:
    return sorted((ln.get("account_code"), int(ln.get("debit_cents") or 0), int(ln.get("credit_cents") or 0),
                   ln.get("basis") or None, ln.get("bank_account_id") or None) for ln in lines)


def plan_hash(actions: List[Dict[str, Any]]) -> str:
    canon = []
    for a in actions:
        e = a.get("entry") or {}
        canon.append([a["kind"], e.get("source_type") or a.get("source_type"), str(e.get("source_id") or a.get("source_id")),
                      e.get("entry_date"), _canon(e.get("lines") or []), a.get("was", {}).get("id")])
    return hashlib.sha256(json.dumps(canon, sort_keys=True, default=str).encode()).hexdigest()[:16]


def _totals(lines: Iterable[Dict[str, Any]], sign: int = 1, into: Optional[Dict[str, Dict[str, int]]] = None):
    into = into if into is not None else {v: {} for v in lv.VIEWS}
    for ln in lines:
        for view in lv.VIEWS:
            if ln.get("basis") in (None, "", view):
                code = ln.get("account_code")
                into[view][code] = into[view].get(code, 0) + sign * (int(ln.get("debit_cents") or 0) - int(ln.get("credit_cents") or 0))
    return into


def _tb(per: Dict[str, Dict[str, int]]) -> Dict[str, Any]:
    out = {}
    for view, accts in per.items():
        rows = {k: v for k, v in accts.items() if v}
        out[view] = {"accounts": dict(sorted(rows.items(), key=lambda kv: str(kv[0]))),
                     "balanced": sum(accts.values()) == 0}
    return out


def _pnl_by_month(entries_after: Iterable[Tuple[str, List[Dict[str, Any]]]], types: Dict[str, str]) -> Dict[str, Any]:
    out: Dict[str, Dict[str, Dict[str, int]]] = {v: {} for v in lv.VIEWS}
    for when, lines in entries_after:
        month = str(when)[:7]
        for ln in lines:
            kind = types.get(ln.get("account_code"))
            if kind not in ("income", "expense"):
                continue
            for view in lv.VIEWS:
                if ln.get("basis") in (None, "", view):
                    m = out[view].setdefault(month, {"income_cents": 0, "expense_cents": 0})
                    net = int(ln.get("debit_cents") or 0) - int(ln.get("credit_cents") or 0)
                    m["income_cents" if kind == "income" else "expense_cents"] += (-net if kind == "income" else net)
    for view in out:
        for m in out[view].values():
            m["profit_cents"] = m["income_cents"] - m["expense_cents"]
        out[view] = dict(sorted(out[view].items()))
    return out


def _paid_on_record(data: Dict[str, Any]) -> Dict[str, int]:
    out: Dict[str, int] = {}
    for p in data["payments"]:
        out[str(p.get("invoice_id"))] = out.get(str(p.get("invoice_id")), 0) + int(p.get("amount_cents") or 0)
    return out


def _still_owed(inv: Dict[str, Any], paid: Dict[str, int]) -> int:
    if (inv.get("status") or "draft") in ("draft", "cancelled", "void", "paid"):
        return 0
    on_record = int(inv.get("total_paid_cents") or 0) if inv.get("order_id") else paid.get(str(inv["id"]), 0)
    return max(0, int(inv.get("total_cents") or 0) - on_record)


def _owed(rows: List[Dict[str, Any]], tenant_id: str, inbound: bool, paid: Dict[str, int]) -> int:
    total = 0
    for inv in rows:
        if (inv.get("doc_type") or "invoice") != "invoice" or ((inv.get("direction") or "outbound") == "inbound") != inbound:
            continue
        if (inv.get("status") or "draft") in ("draft", "cancelled", "void", "paid"):
            continue
        if not ev._on_accrual(tenant_id, {"tenant_id": tenant_id, **inv}):
            continue
        total += _still_owed(inv, paid)
    return total


def plan(tenant_id: str, accrual_from: Optional[str] = None, *, audit: bool = True) -> Dict[str, Any]:
    """The dry run. Writes nothing."""
    entries, ctx = target(tenant_id, accrual_from)
    live, every = live_journal(tenant_id)
    actions = diff(entries, live)
    before = _totals(every)
    after = _totals(every)
    for a in actions:
        if a["kind"] in ("changed", "remove"):
            _totals(a["was"]["lines"], -1, after)
        if a["kind"] in ("new", "changed"):
            _totals(a["entry"]["lines"], 1, after)
    counts: Dict[str, Dict[str, Any]] = {}
    for a in actions:
        st = (a.get("entry") or {}).get("source_type") or a.get("source_type")
        c = counts.setdefault(f"{a['kind']}:{st}", {"count": 0})
        c["count"] += 1
    data = ctx["data"]
    after_entries = {(e["source_type"], str(e["source_id"])): (e["entry_date"], e["lines"]) for e in entries}
    for key, cur in live.items():
        if cur["source_type"] in KEEP and key not in after_entries:
            after_entries[key] = (str(cur.get("entry_date"))[:10], cur["lines"])
    try:
        types = {a["code"]: a.get("type") for a in (_client().table("commerce_accounts").select("code,type")
                                                    .eq("tenant_id", tenant_id).execute().data or [])}
    except Exception:  # noqa: BLE001
        types = {}
    with ev.capture({("settings", tenant_id): ctx["settings"]}):
        paid = _paid_on_record(data)
        owed_to_you = _owed(data["invoices"], tenant_id, inbound=False, paid=paid)
        you_owe = _owed(data["invoices"], tenant_id, inbound=True, paid=paid)
    acc = after["accrual"]
    suspense = sorted((e for e in once_all(entries) if any(ln.get("account_code") == ev.SUSPENSE for ln in e["lines"])),
                      key=lambda e: -sum(int(ln.get("debit_cents") or 0) + int(ln.get("credit_cents") or 0)
                                         for ln in e["lines"] if ln.get("account_code") == ev.SUSPENSE))
    recon = bank_recon.check(data["bank"], [{"source_type": k[0], "source_id": k[1], "entry_date": v[0], "lines": v[1],
                                             "bank_txn_id": k[1] if k[0] == "bank_line" else None}
                                            for k, v in after_entries.items()], ctx["accounts"])
    # money in transit, document by document: a payment Vula recorded (clearing) is cleared by the bank
    # line matched to its document; what's left is a payment with no bank line (or a bank line matched
    # to a document whose payment wasn't recorded) — often the same cost booked twice.
    linked: Dict[str, int] = {}
    shares: Dict[str, List[Dict[str, Any]]] = {}      # one payment for several bills (migration 293)
    for d in data.get("line_docs") or []:
        shares.setdefault(str(d.get("txn_id")), []).append(d)
    for r in data["bank"]:
        if ev.is_live(r) and shares.get(str(r.get("id"))):
            sign = 1 if (r.get("direction") or "out") == "in" else -1
            for d in shares[str(r["id"])]:
                linked[str(d["invoice_id"])] = linked.get(str(d["invoice_id"]), 0) + sign * int(d.get("amount_cents") or 0)
            continue
        if ev.is_live(r):
            for k in ("matched_invoice_id", "matched_order_id", "matched_expense_id"):
                if r.get(k):
                    linked[str(r[k])] = linked.get(str(r[k]), 0) + (int(r.get("amount_cents") or 0) *
                                                                    (1 if (r.get("direction") or "out") == "in" else -1))
    per_doc: Dict[str, Dict[str, Any]] = {}
    once: Dict[Tuple[str, str], Dict[str, Any]] = {}
    for e in entries:                      # the last word on a source, as the plan posts it
        once[(e["source_type"], str(e["source_id"]))] = {**e, "doc": e.get("doc") or once.get(
            (e["source_type"], str(e["source_id"])), {}).get("doc")}
    for e in once.values():
        d = e.get("doc") or {}
        if d.get("kind") in (None, "bank_line"):
            continue
        row = per_doc.setdefault(d["id"], {**d, "recorded_cents": 0, "ap_cents": 0, "ar_cents": 0})
        for ln in e["lines"]:
            net = int(ln.get("debit_cents") or 0) - int(ln.get("credit_cents") or 0)
            if ln.get("account_code") in (ev.CLEARING,):
                row["recorded_cents"] += net
            if ln.get("account_code") == ev.AP and ln.get("basis") in (None, "accrual"):
                row["ap_cents"] += net
            if ln.get("account_code") == ev.AR and ln.get("basis") in (None, "accrual"):
                row["ar_cents"] += net
    open_items = []
    for doc_id, row in per_doc.items():
        left = row["recorded_cents"] - linked.get(doc_id, 0)
        if left:
            open_items.append({"kind": row["kind"], "id": doc_id, "label": row["label"], "date": row["date"],
                               "recorded_cents": row["recorded_cents"], "bank_cents": linked.get(doc_id, 0),
                               "open_cents": left})
    open_items.sort(key=lambda x: -abs(x["open_cents"]))
    owed_by_doc = {str(i["id"]): _still_owed(i, paid) for i in data["invoices"]}
    ap_off = sorted(({"id": k, "label": v["label"], "date": v["date"], "books_cents": -v["ap_cents"],
                      "owed_cents": owed_by_doc.get(k, 0)}
                     for k, v in per_doc.items() if v["kind"] == "bill" and -v["ap_cents"] != owed_by_doc.get(k, 0)),
                    key=lambda x: -abs(x["books_cents"] - x["owed_cents"]))
    ar_off = sorted(({"id": k, "label": v["label"], "date": v["date"], "books_cents": v["ar_cents"],
                      "owed_cents": owed_by_doc.get(k, 0)}
                     for k, v in per_doc.items() if v["kind"] == "invoice" and v["ar_cents"] != owed_by_doc.get(k, 0)),
                    key=lambda x: -abs(x["books_cents"] - x["owed_cents"]))
    by_kind: Dict[str, Dict[str, int]] = {}
    for x in open_items:
        k = by_kind.setdefault(x["kind"], {"count": 0, "open_cents": 0})
        k["count"] += 1
        k["open_cents"] += x["open_cents"]
    report: Dict[str, Any] = {
        "tenant_id": tenant_id, "accrual_from": ctx["settings"].get("accrual_from"),
        "actions": {"new": sum(a["kind"] == "new" for a in actions),
                    "changed": sum(a["kind"] == "changed" for a in actions),
                    "remove": sum(a["kind"] == "remove" for a in actions),
                    "unchanged": len({(e["source_type"], str(e["source_id"])) for e in entries})
                                 - sum(a["kind"] in ("new", "changed") for a in actions),
                    "by_source": counts},
        "trial_balance": {"before": _tb(before), "after": _tb(after)},
        "pnl_by_month_after": _pnl_by_month(after_entries.values(), types),
        "control": {"debtors_books_cents": acc.get(ev.AR, 0), "debtors_owed_cents": owed_to_you,
                    "debtors_tie": acc.get(ev.AR, 0) == owed_to_you,
                    "creditors_books_cents": -acc.get(ev.AP, 0), "creditors_owed_cents": you_owe,
                    "creditors_tie": -acc.get(ev.AP, 0) == you_owe},
        "suspense": {"lines": len(suspense), "cents": acc.get(ev.SUSPENSE, 0),
                     "largest": [{"bank_line": e["source_id"], "date": e["entry_date"], "what": e["description"][:80],
                                  "cents": next(int(ln.get("debit_cents") or 0) or -int(ln.get("credit_cents") or 0)
                                                for ln in e["lines"] if ln.get("account_code") == ev.SUSPENSE)}
                                 for e in suspense[:20]]},
        "in_transit_cents": acc.get(ev.CLEARING, 0),
        "in_transit": {"by_kind": by_kind, "largest": open_items[:500]},
        "creditors_by_bill": ap_off[:25], "debtors_by_invoice": ar_off[:25],
        "owed_to_staff_cents": -acc.get(ev.STAFF, 0), "cash_on_hand_cents": acc.get(ev.CASH, 0),
        "bank_reconciliation": recon,
        "plan_hash": plan_hash(actions),
    }
    if audit:
        try:
            from vula.commerce import books_audit
            a = books_audit.run(tenant_id)
            report["audit"] = {"summary": a.get("summary"),
                               "problems": [{"key": s["key"], "headline": s.get("headline")}
                                            for s in a.get("sections", []) if s.get("status") == "problem"]}
        except Exception as exc:  # noqa: BLE001
            report["audit"] = {"error": type(exc).__name__}
    report["_actions"] = actions
    return report


def once_all(entries: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Each source once, the last word winning (as diff() posts it)."""
    want: Dict[Tuple[str, str], Dict[str, Any]] = {}
    for e in entries:
        want[(e["source_type"], str(e["source_id"]))] = e
    return list(want.values())


def public(report: Dict[str, Any]) -> Dict[str, Any]:
    """The report without the full action list (for routes and the stored copy)."""
    return {k: v for k, v in report.items() if not k.startswith("_")}


# ── commit ───────────────────────────────────────────────────────────────────

def _signed_off(tenant_id: str) -> bool:
    try:
        rows = (_client().table("vula_books_audits").select("signed_off_by,signed_off_at,created_at")
                .eq("tenant_id", tenant_id).order("created_at", desc=True).limit(1).execute().data or [])
        return bool(rows and rows[0].get("signed_off_by"))
    except Exception:  # noqa: BLE001
        return False


def _switch_on(tenant_id: str) -> None:
    db = _client()
    rows = (db.table("vula_tenant_config").select("features").eq("tenant_id", tenant_id).limit(1).execute().data or [])
    features = {**((rows[0].get("features") if rows else None) or {}), "books_v2": True}
    if rows:
        db.table("vula_tenant_config").update({"features": features}).eq("tenant_id", tenant_id).execute()
    else:
        db.table("vula_tenant_config").insert({"tenant_id": tenant_id, "features": features}).execute()
    try:
        from vula.api import tenants
        tenants._CACHE.pop(tenant_id, None)
    except Exception:  # noqa: BLE001
        pass


def commit(tenant_id: str, *, accrual_from: str, plan_hash_: str, person: str, master: bool) -> Dict[str, Any]:
    if not master:
        return {"error": "Only the Vula operator can rebuild a business's books."}
    if not accrual_from:
        return {"error": "Say from which date the books are kept on accrual (the start of the financial year)."}
    if not _signed_off(tenant_id):
        return {"error": "The books audit for this business isn't signed off yet. Run it, fix what it finds "
                         "with the owner, and sign it off first."}
    report = plan(tenant_id, accrual_from, audit=False)
    if report["plan_hash"] != plan_hash_:
        return {"error": "The records changed since that dry run. Run the dry run again and check it.",
                "plan_hash": report["plan_hash"]}
    db = _client()
    settings = {"tenant_id": tenant_id, "accrual_from": accrual_from, "year_end_month": 2,
                "backfill_report": public(report)}
    existing = (db.table("commerce_books_settings").select("tenant_id").eq("tenant_id", tenant_id)
                .limit(1).execute().data or [])
    if existing:
        db.table("commerce_books_settings").update(settings).eq("tenant_id", tenant_id).execute()
    else:
        db.table("commerce_books_settings").insert(settings).execute()
    _switch_on(tenant_id)
    lv.ensure_ledger_chart(tenant_id)
    failed = 0
    for a in report["_actions"]:
        if a["kind"] == "remove":
            got = lv.unpost(tenant_id, a["source_type"], a["source_id"], reason="rebuilt into the one book")
        else:
            e = a["entry"]
            got = lv.repost(tenant_id, source_type=e["source_type"], source_id=e["source_id"],
                            entry_date=e["entry_date"], description=e["description"], lines=e["lines"],
                            doc_id=e.get("doc_id"), bank_txn_id=e.get("bank_txn_id"), reason="rebuilt into the one book")
        failed += got is None
    again = plan(tenant_id, accrual_from, audit=False)
    left = again["actions"]["new"] + again["actions"]["changed"] + again["actions"]["remove"]
    balanced = all(again["trial_balance"]["after"][v]["balanced"] for v in lv.VIEWS)
    try:
        from vula.api.merchant_audit import audit
        audit(tenant_id, {"email": person}, "books_rebuilt", accrual_from=accrual_from, applied=len(report["_actions"]),
              failed=failed, left=left, balanced=balanced)
    except Exception:  # noqa: BLE001
        pass
    out = {"ok": failed == 0 and left == 0 and balanced, "applied": len(report["_actions"]) - failed,
           "failed": failed, "left_to_do": left, "balanced": balanced, "after": public(again)}
    if not out["ok"]:
        out["error"] = (f"{failed} entries couldn't be posted (kept in journal_post_failures) and {left} still differ. "
                        "Nothing was hidden: run the dry run to see them.")
    return out
