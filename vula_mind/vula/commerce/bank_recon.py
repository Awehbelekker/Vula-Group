"""vula/commerce/bank_recon.py — does the bank in the books equal the bank statements? (Phase 1, PR 3)

18 Nov 2026 (Ian: "finish Vula's own books first"). In the one trusted book every live bank line posts
its own entry (ledger_events.bank_line_lines), so for each bank account:

    opening balance (the first statement line's balance before it moved)
  + every live line's movement
  = the last statement line's balance

when nothing is missing. A difference means a line is missing or set aside by mistake, or a
statement wasn't read — the same breaks the books audit names, now as one number per account.

Lines with no account number (read before migration 261, or from a statement whose number wasn't
read) can't be reconciled per account: they're counted and listed, and no opening balance is posted
for them until the official statements tag them (statement_compare).

Pure functions over rows and entries; no model, integer cents.
"""
from __future__ import annotations

from datetime import date, timedelta
from typing import Any, Dict, Iterable, List

from vula.commerce import ledger_events as ev
from vula.commerce import ledger_v2 as lv

UNASSIGNED = ""


def _signed(row: Dict[str, Any]) -> int:
    amt = int(row.get("amount_cents") or 0)
    return amt if (row.get("direction") or "out") == "in" else -amt


def _key(row: Dict[str, Any]) -> str:
    return str(row.get("bank_account") or UNASSIGNED)


def order_lines(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """One account's live lines in statement order: by date, and within a day by the running balance
    (a line whose balance before is another's balance after comes after it)."""
    out: List[Dict[str, Any]] = []
    by_day: Dict[str, List[Dict[str, Any]]] = {}
    for r in rows:
        by_day.setdefault(str(r.get("txn_date"))[:10], []).append(r)
    for day in sorted(by_day):
        todo = sorted(by_day[day], key=lambda r: str(r.get("id")))
        if all(r.get("balance_cents") is not None for r in todo):
            after = {int(r["balance_cents"]) for r in todo}
            # start with a line whose balance-before isn't another line's balance-after
            chain: List[Dict[str, Any]] = []
            left = list(todo)
            prev_after = int(out[-1]["balance_cents"]) if out and out[-1].get("balance_cents") is not None else None
            while left:
                nxt = None
                if prev_after is not None:
                    nxt = next((r for r in left if int(r["balance_cents"]) - _signed(r) == prev_after), None)
                if nxt is None:
                    nxt = next((r for r in left if int(r["balance_cents"]) - _signed(r) not in after), left[0])
                chain.append(nxt)
                left.remove(nxt)
                prev_after = int(nxt["balance_cents"])
            out += chain
        else:
            out += todo
    return out


def opening_entries(tenant_id: str, rows: Iterable[Dict[str, Any]], account_ids: Dict[str, str]) -> List[Dict[str, Any]]:
    """Per bank account with a number: the balance before its first live line, as an opening entry
    (Dr/Cr bank against opening balances), dated the day before. An accountant's opening balances
    replace it later under the same source."""
    groups: Dict[str, List[Dict[str, Any]]] = {}
    for r in rows:
        if ev.is_live(r) and _key(r) != UNASSIGNED:
            groups.setdefault(_key(r), []).append(r)
    out = []
    for acct, lines in groups.items():
        ordered = order_lines(lines)
        first = ordered[0]
        if first.get("balance_cents") is None:
            continue
        opening = int(first["balance_cents"]) - _signed(first)
        if opening == 0:
            continue
        when = (date.fromisoformat(str(first["txn_date"])[:10]) - timedelta(days=1)).isoformat()
        bank_id = account_ids.get(acct)
        lines_ = ([lv.line(ev.BANK, opening, bank_account_id=bank_id), lv.line("opening_balance_equity", credit=opening)]
                  if opening > 0 else
                  [lv.line("opening_balance_equity", -opening), lv.line(ev.BANK, credit=-opening, bank_account_id=bank_id)])
        out.append({"tenant_id": tenant_id, "source_type": "opening_bank", "source_id": acct, "entry_date": when,
                    "description": f"Opening balance, bank account …{acct[-4:]}", "lines": lines_,
                    "doc_id": None, "bank_txn_id": None})
    return out


def check(rows: Iterable[Dict[str, Any]], entries: Iterable[Dict[str, Any]],
          account_ids: Dict[str, str]) -> Dict[str, Any]:
    """Per bank account: the last statement balance against bank in the books (from `entries` — the
    live journal, or what a rebuild would leave). Plus what can't be reconciled."""
    rows = [r for r in rows]
    live = [r for r in rows if ev.is_live(r)]
    by_txn = {str(e.get("bank_txn_id")): e for e in entries if e.get("bank_txn_id")}
    entries = list(entries)
    accounts: List[Dict[str, Any]] = []
    groups: Dict[str, List[Dict[str, Any]]] = {}
    for r in live:
        groups.setdefault(_key(r), []).append(r)
    for acct, lines in sorted(groups.items()):
        if acct == UNASSIGNED:
            continue
        ordered = order_lines(lines)
        last = ordered[-1]
        bank_id = account_ids.get(acct)
        until = str(last.get("txn_date"))[:10]
        # this account's own entries: its opening balance and the entries of its own lines
        mine = {str(r["id"]) for r in rows if _key(r) == acct}
        books = 0
        for e in entries:
            own = (e.get("source_type") == "opening_bank" and str(e.get("source_id")) == acct) or \
                  str(e.get("bank_txn_id")) in mine
            if not own or str(e.get("entry_date"))[:10] > until:
                continue
            for ln in e["lines"]:
                if ln.get("account_code") == ev.BANK and ln.get("basis") in (None, "accrual"):
                    books += int(ln.get("debit_cents") or 0) - int(ln.get("credit_cents") or 0)
        statement = int(last["balance_cents"]) if last.get("balance_cents") is not None else None
        accounts.append({"account": f"…{acct[-4:]}", "number": acct, "bank_account_id": bank_id,
                         "lines": len(lines), "from": str(ordered[0].get("txn_date"))[:10], "until": until,
                         "statement_cents": statement, "books_cents": books,
                         "difference_cents": None if statement is None else books - statement,
                         "reconciles": statement is not None and books == statement})
    unassigned = groups.get(UNASSIGNED, [])
    no_entry = [r for r in live if str(r["id"]) not in by_txn]
    set_aside_posted = [r for r in rows if not ev.is_live(r) and str(r["id"]) in by_txn]
    return {"accounts": accounts,
            "all_reconcile": bool(accounts) and all(a["reconciles"] for a in accounts) and not unassigned,
            "unassigned": {"lines": len(unassigned), "in_cents": sum(_signed(r) for r in unassigned if _signed(r) > 0),
                           "out_cents": -sum(_signed(r) for r in unassigned if _signed(r) < 0)},
            "live_lines_without_entry": [r["id"] for r in no_entry][:200],
            "set_aside_lines_still_posted": [r["id"] for r in set_aside_posted][:200]}


def as_at(rows: Iterable[Dict[str, Any]], live_entries: Dict[Any, Dict[str, Any]], until: str,
          account_ids: Dict[str, str]) -> Dict[str, Any]:
    """`check` at a date (a year end), for a business on the one book: the bank lines and the live
    journal (books_rebuild.live_journal's live entries) up to that date only."""
    rows = [r for r in rows if str(r.get("txn_date"))[:10] <= until]
    entries = []
    for (stype, sid), e in live_entries.items():
        if str(e.get("entry_date"))[:10] > until:
            continue
        entries.append({"source_type": stype, "source_id": sid, "entry_date": str(e.get("entry_date"))[:10],
                        "lines": e.get("lines") or [], "bank_txn_id": sid if stype == "bank_line" else None})
    return check(rows, entries, account_ids)


def statement_check(rows: Iterable[Dict[str, Any]], since: str, until: str) -> Dict[str, Any]:
    """Per bank account, for a business not on the one book yet (22 Nov 2026, the year-end pack): do the
    statement lines in the books for the period add up? The balance before the first live line, plus
    every live line's movement, must equal the last line's balance; a line whose balance isn't the one
    before it plus its own movement is a break (a line missing, set aside by mistake, or misread).
    Lines on no account number are counted, never reconciled."""
    live = [r for r in rows if ev.is_live(r) and since <= str(r.get("txn_date"))[:10] <= until]
    groups: Dict[str, List[Dict[str, Any]]] = {}
    for r in live:
        groups.setdefault(_key(r), []).append(r)
    accounts = []
    for acct, lines in sorted(groups.items()):
        if acct == UNASSIGNED:
            continue
        ordered = order_lines(lines)
        first, last = ordered[0], ordered[-1]
        has_balances = all(r.get("balance_cents") is not None for r in ordered)
        moved_in = sum(_signed(r) for r in ordered if _signed(r) > 0)
        moved_out = -sum(_signed(r) for r in ordered if _signed(r) < 0)
        breaks = 0
        if has_balances:
            for a, b in zip(ordered, ordered[1:]):
                if int(a["balance_cents"]) + _signed(b) != int(b["balance_cents"]):
                    breaks += 1
        opening = int(first["balance_cents"]) - _signed(first) if first.get("balance_cents") is not None else None
        closing = int(last["balance_cents"]) if last.get("balance_cents") is not None else None
        books = None if opening is None else opening + moved_in - moved_out
        accounts.append({"account": f"…{acct[-4:]}", "number": acct, "lines": len(lines),
                         "from": str(first.get("txn_date"))[:10], "until": str(last.get("txn_date"))[:10],
                         "opening_cents": opening, "in_cents": moved_in, "out_cents": moved_out,
                         "statement_cents": closing, "books_cents": books,
                         "difference_cents": None if books is None or closing is None else books - closing,
                         "breaks": breaks if has_balances else None,
                         "reconciles": books is not None and closing is not None and books == closing and not breaks})
    unassigned = groups.get(UNASSIGNED, [])
    return {"accounts": accounts,
            "all_reconcile": bool(accounts) and all(a["reconciles"] for a in accounts) and not unassigned,
            "unassigned": {"lines": len(unassigned), "in_cents": sum(_signed(r) for r in unassigned if _signed(r) > 0),
                           "out_cents": -sum(_signed(r) for r in unassigned if _signed(r) < 0)}}
