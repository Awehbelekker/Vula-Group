"""vula/commerce/books_audit.py — is a business's bank data fit to build books from? (Phase 0)

17 Nov 2026, Ian: DIGG will give its accountant access to Vula, so "Vula must have its ducks in a
row ... a full audit as Vula with DIGG, so that when the accountant has access Vula doesn't drop
the ball." Before any history is posted to the ledger (Phase 1), this audit says, with the rows
behind every finding, whether the bank data underneath the books is complete and right.

Read only: nothing here inserts, updates or deletes. Every figure is worked out in code from the
business's own rows (integer cents); no model is asked anything. A finding names its rows so the
owner can open them, decide, and the audit is run again until it is clean.

How bank accounts are found without the statement files. Every statement line carries the balance
after it, so:
  - inside one statement file the lines chain: the balance before a line (its balance minus its
    own amount) is the balance after the previous one. A file that won't chain into one run has a
    missing or misread line (`chain`);
  - the same bank line read from two statements (a weekly and the month's) has the same date,
    direction, amount and balance after it — the statements are of the same account;
  - one statement's closing balance is the next statement's opening balance on the same account.
Files joined by either of the last two are one account (`accounts`). The account number, where a
line has one (commerce_bank_transactions.bank_account), names it.

Sections (the report's `sections`, in order):
  accounts      — the bank accounts behind the lines, private ones flagged
  coverage      — per account: the period covered, gaps between statements, overlaps
  chains        — statements whose running balance doesn't chain (a missing or misread line)
  duplicates    — the same bank line counted twice (not set aside)
  transfers     — money moved between the business's own accounts, counted as income and a cost
  categories    — lines Vula categorised itself (AI / default) for the owner to confirm, and the
                  owner-drawings and casual-labour lines an accountant will ask about
  documents     — payments with no bill, receipt or worker record; receipts with no invoice
  paperwork     — supplier bills and customer invoices still in draft; expenses with no document
  dates         — ledger and expense dates outside the business's own trading period
  vat           — turnover over any 12 months against the R1m compulsory VAT registration line
  doc_figures, doc_books, doc_duplicates, suppliers, price_vat, access
                — the documents behind the money, checked against their own files and the books
                  (vula/commerce/doc_audit.py, 19 Nov 2026)
"""
from __future__ import annotations

import logging
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, Iterable, List, Optional, Tuple

log = logging.getLogger(__name__)

TXN_COLS = ("id,source_file,txn_date,description,amount_cents,direction,balance_cents,match_status,"
            "account_code,categorized_by,worker_id,matched_invoice_id,matched_expense_id,"
            "matched_order_id,bank_account,payee,project,created_at")
PAGE = 1000
DOC_THRESHOLD_CENTS = 50_000            # a payment out over R500 should have a document behind it
REVIEW_THRESHOLD_CENTS = 100_000        # a self-categorised line over R1,000 is worth the owner's look
TRANSFER_DAYS = 2
VAT_THRESHOLD_CENTS = 100_000_000       # R1,000,000 of taxable supplies in any 12 months (VAT Act s23)
INCOME_CODES = {"sales", "other_income"}
NO_DOC_CODES = {"bank_charges", "owner_drawings", "interest", "owner_capital", "owner_loan", "transfer"}


def _client():
    from vula.commerce.service import _client as c
    return c()


def _rands(cents: int) -> str:
    sign = "-" if cents < 0 else ""
    return f"{sign}R{abs(cents) / 100:,.2f}"


def _rows(table: str, cols: str, tenant_id: str, order: str = "id") -> List[Dict[str, Any]]:
    """Every row of one table for this tenant, a page at a time (reads only)."""
    out: List[Dict[str, Any]] = []
    lo = 0
    while True:
        page = (_client().table(table).select(cols).eq("tenant_id", tenant_id).order(order)
                .range(lo, lo + PAGE - 1).execute().data or [])
        out.extend(page)
        if len(page) < PAGE:
            return out
        lo += PAGE


def _d(v: Any) -> Optional[date]:
    try:
        return date.fromisoformat(str(v)[:10])
    except Exception:  # noqa: BLE001
        return None


def _net(line: Dict[str, Any]) -> int:
    amt = int(line.get("amount_cents") or 0)
    return amt if line.get("direction") == "in" else -amt


def _live(line: Dict[str, Any]) -> bool:
    """Not set aside (a misread or superseded copy is 'ignored'), and a line of a bank statement — a proof
    of payment staged on WhatsApp is never counted (ledger_events.is_pop)."""
    return line.get("match_status") != "ignored" and (line.get("source_file") or "") != "whatsapp_pop"


# ── statements and accounts ──────────────────────────────────────────────────

def is_statement(source_file: str, lines: List[Dict[str, Any]]) -> bool:
    """A bank statement (lines with running balances), not one proof of payment or receipt."""
    name = (source_file or "").lower()
    if name in ("whatsapp_pop",) or name.startswith(("receipt-", "proofofpayment")):
        return False
    with_balance = [ln for ln in lines if ln.get("balance_cents") is not None]
    return len(with_balance) >= 3 or "statement" in name


def chain(lines: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Put one statement's lines back in order by their running balance.

    Returns the runs found (`segments`, each a list of line ids in order), the opening balance
    (before the first line), the closing balance (after the last), and lines with no balance.
    One run = the statement reads cleanly; more runs = a line is missing or misread between them."""
    with_bal = [ln for ln in lines if ln.get("balance_cents") is not None]
    no_bal = [ln["id"] for ln in lines if ln.get("balance_cents") is None]
    remaining = sorted(with_bal, key=lambda ln: (str(ln.get("txn_date") or ""), str(ln.get("created_at") or "")))
    before = {ln["id"]: int(ln["balance_cents"]) - _net(ln) for ln in remaining}
    segments: List[List[Dict[str, Any]]] = []
    while remaining:
        balances_after = Counter(int(ln["balance_cents"]) for ln in remaining)
        start = next((ln for ln in remaining
                      if balances_after[before[ln["id"]]] - (1 if int(ln["balance_cents"]) == before[ln["id"]] else 0) <= 0),
                     remaining[0])
        run = [start]
        remaining.remove(start)
        while True:
            cur = int(run[-1]["balance_cents"])
            nxt = next((ln for ln in remaining if before[ln["id"]] == cur), None)
            if nxt is None:
                break
            run.append(nxt)
            remaining.remove(nxt)
        segments.append(run)
    segments.sort(key=lambda s: (str(s[0].get("txn_date") or ""), -len(s)))
    opening = before[segments[0][0]["id"]] if segments else None
    last = max(segments, key=lambda s: (str(s[-1].get("txn_date") or ""), len(s))) if segments else None
    closing = int(last[-1]["balance_cents"]) if last else None
    return {"segments": [[ln["id"] for ln in s] for s in segments], "opening": opening, "closing": closing,
            "no_balance": no_bal}


class _Union:
    def __init__(self, items: Iterable[str]):
        self.parent = {i: i for i in items}

    def find(self, x: str) -> str:
        while self.parent[x] != x:
            self.parent[x] = self.parent[self.parent[x]]
            x = self.parent[x]
        return x

    def join(self, a: str, b: str) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.parent[max(ra, rb)] = min(ra, rb)


MAX_FILL = 60


def _fill(start_balance: int, target: int, pool: List[Dict[str, Any]], used: set) -> Optional[List[Dict[str, Any]]]:
    """A run of set-aside lines leading from one balance to another: the lines that were set aside
    by mistake between two live runs (17 Nov 2026, DIGG 18 Jul: the right copy of five purchases
    was set aside along with the misread one, so the books were missing them)."""
    path: List[Dict[str, Any]] = []
    cur = start_balance
    taken = set(used)
    for _ in range(MAX_FILL):
        nxt = next((ln for ln in pool if ln["id"] not in taken
                    and int(ln["balance_cents"]) - _net(ln) == cur), None)
        if nxt is None:
            return None
        path.append(nxt)
        taken.add(nxt["id"])
        cur = int(nxt["balance_cents"])
        if cur == target:
            return path
    return None


def misread_pairs(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Two copies of one line on the same day with the same words, one ×10 or ×100 the other in
    amount or balance — a decimal point read in the wrong place."""
    groups: Dict[Tuple, List[Dict[str, Any]]] = defaultdict(list)
    for ln in rows:
        groups[(str(ln.get("txn_date")), ln.get("direction"), (ln.get("description") or "").strip().lower())].append(ln)
    out = []
    for g in groups.values():
        for i, a in enumerate(g):
            for b in g[i + 1:]:
                for col in ("amount_cents", "balance_cents"):
                    x, y = a.get(col), b.get(col)
                    if x and y and x != y and (max(abs(x), abs(y)) in (10 * min(abs(x), abs(y)), 100 * min(abs(x), abs(y)))):
                        out.append({"rows": [a["id"], b["id"]], "column": col, "status": [a.get("match_status"), b.get("match_status")]})
                        break
    return out


def statements(lines: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    """Each statement file: its period, and how its LIVE lines (what the books use) chain by running
    balance. Set-aside lines that exactly fill a break between live runs are found (`restore`)."""
    by_file: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for ln in lines:
        by_file[ln.get("source_file") or "(unknown)"].append(ln)
    out = {}
    for name, rows in by_file.items():
        live = [r for r in rows if _live(r)]
        dates = [d for d in (_d(r.get("txn_date")) for r in live) if d]
        info = {"file": name, "lines": len(rows), "live": len(live),
                "first": min(dates) if dates else None, "last": max(dates) if dates else None,
                "statement": is_statement(name, rows), "superseded": not live,
                "accounts_named": sorted({r["bank_account"] for r in rows if r.get("bank_account")}),
                "restore": [], "misreads": misread_pairs(rows)}
        if info["statement"] and live:
            info.update(chain(live))
            by_id = {r["id"]: r for r in rows}
            runs = [[by_id[i] for i in seg] for seg in info["segments"]]
            pool = [r for r in rows if not _live(r) and r.get("balance_cents") is not None]
            used: set = set()
            starts = {int(run[0]["balance_cents"]) - _net(run[0]): k for k, run in enumerate(runs)}
            filled = 0
            for k, run in enumerate(runs):
                end = int(run[-1]["balance_cents"])
                for target, j in starts.items():
                    if j == k:
                        continue
                    path = _fill(end, target, pool, used)
                    if path:
                        used.update(p["id"] for p in path)
                        info["restore"].extend(path)
                        filled += 1
                        break
            info["breaks"] = max(len(runs) - 1 - filled, 0)
            # opening/closing once the restorable lines are back in
            if info["restore"]:
                info.update({k: v for k, v in chain(live + info["restore"]).items() if k in ("opening", "closing")})
        out[name] = info
    return out


def accounts(lines: List[Dict[str, Any]], stmts: Dict[str, Dict[str, Any]],
             private_numbers: Iterable[str] = ()) -> List[Dict[str, Any]]:
    """Group statement files into the bank accounts they are statements of. Only files with live
    lines count (a file whose lines were all set aside is a superseded read, not an account), and
    statements join only on exact evidence — a probable continuation within a few rand is reported
    (`continuations`), never silently joined."""
    files = [f for f, s in stmts.items() if s["statement"] and not s["superseded"]]
    uf = _Union(files)
    # 1. the same bank line in two statements
    seen: Dict[Tuple, str] = {}
    for ln in lines:
        f = ln.get("source_file") or "(unknown)"
        if f not in uf.parent or ln.get("balance_cents") is None:
            continue
        key = (str(ln.get("txn_date")), ln.get("direction"), int(ln.get("amount_cents") or 0), int(ln["balance_cents"]))
        if key in seen and seen[key] != f:
            uf.join(seen[key], f)
        seen.setdefault(key, f)
    # 2. one statement closes where the next opens
    ordered = sorted(files, key=lambda f: (stmts[f]["first"] or date.min))
    for a in ordered:
        for b in ordered:
            sa, sb = stmts[a], stmts[b]
            if a == b or sa.get("closing") is None or sb.get("opening") is None or not (sa["last"] and sb["first"]):
                continue
            if sa["closing"] == sb["opening"] and timedelta(days=-3) <= (sb["first"] - sa["last"]) <= timedelta(days=40):
                uf.join(a, b)
    # 3. a named account number
    by_number: Dict[str, str] = {}
    for f in files:
        for num in stmts[f]["accounts_named"]:
            if num in by_number:
                uf.join(by_number[num], f)
            by_number.setdefault(num, f)
    groups: Dict[str, List[str]] = defaultdict(list)
    for f in files:
        groups[uf.find(f)].append(f)
    private = {str(p) for p in private_numbers if p}
    out = []
    for i, (_, fs) in enumerate(sorted(groups.items(), key=lambda kv: min(stmts[f]["first"] or date.max for f in kv[1]))):
        fs.sort(key=lambda f: (stmts[f]["first"] or date.min, stmts[f]["last"] or date.min))
        numbers = sorted({n for f in fs for n in stmts[f]["accounts_named"]})
        rows = [ln for ln in lines if (ln.get("source_file") or "(unknown)") in fs]
        live = [ln for ln in rows if _live(ln)]
        is_private = bool(set(numbers) & private) or any("private" in f.lower() for f in fs)
        out.append({
            "key": f"A{i + 1}", "files": fs, "numbers": [f"…{n[-4:]}" for n in numbers],
            "first": min(stmts[f]["first"] for f in fs if stmts[f]["first"]),
            "last": max(stmts[f]["last"] for f in fs if stmts[f]["last"]),
            "lines": len(live), "set_aside": len(rows) - len(live),
            "in_cents": sum(int(ln["amount_cents"]) for ln in live if ln.get("direction") == "in"),
            "out_cents": sum(int(ln["amount_cents"]) for ln in live if ln.get("direction") == "out"),
            "opening": stmts[fs[0]].get("opening"), "closing": stmts[fs[-1]].get("closing"),
            "private": is_private,
        })
    return out


CONTINUATION_CENTS = 500_000            # within R5,000: probably the same account, a line missing


def continuations(accts: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Accounts that probably continue one another: B starts within 3 days of A's last statement and
    opens within R5,000 of where A closed. Reported for the owner and the official statements to
    settle — never joined, since two accounts with small balances would also look like this."""
    out = []
    for a in accts:
        for b in accts:
            if a is b or a.get("closing") is None or b.get("opening") is None:
                continue
            days = (b["first"] - a["last"]).days
            diff = b["opening"] - a["closing"]
            if 0 <= days <= 3 and abs(diff) <= CONTINUATION_CENTS:
                out.append({"from": a["key"], "to": b["key"], "a_closed": a["closing"], "b_opened": b["opening"],
                            "difference": diff, "a_last": str(a["last"]), "b_first": str(b["first"])})
    return out


def coverage(acct: Dict[str, Any], stmts: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
    """Gaps (a statement doesn't open where the one before closed) and overlaps in one account."""
    fs = acct["files"]
    gaps, overlaps = [], []
    covered_to: Optional[date] = None
    close: Optional[int] = None
    for f in fs:
        s = stmts[f]
        if covered_to and s["first"] and s["first"] <= covered_to:
            overlaps.append({"file": f, "from": str(s["first"]), "to": str(min(s["last"], covered_to))})
        elif covered_to and s["first"] and close is not None and s.get("opening") is not None and s["opening"] != close:
            gaps.append({"after": str(covered_to), "before": str(s["first"]), "closed_at": close,
                         "opened_at": s["opening"], "missing_cents": s["opening"] - close})
        if s["last"] and (covered_to is None or s["last"] >= covered_to):
            covered_to, close = s["last"], s.get("closing")
    months = sorted({(str(stmts[f]["first"])[:7]) for f in fs if stmts[f]["first"]} |
                    {(str(stmts[f]["last"])[:7]) for f in fs if stmts[f]["last"]})
    return {"account": acct["key"], "gaps": gaps, "overlaps": overlaps, "months": months}


def duplicates(lines: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """The same bank line (date, direction, amount, balance after) counted more than once."""
    groups: Dict[Tuple, List[Dict[str, Any]]] = defaultdict(list)
    for ln in lines:
        if _live(ln) and ln.get("balance_cents") is not None:
            groups[(str(ln.get("txn_date")), ln.get("direction"), int(ln.get("amount_cents") or 0),
                    int(ln["balance_cents"]))].append(ln)
    return [{"date": k[0], "direction": k[1], "cents": k[2], "rows": [r["id"] for r in g],
             "files": sorted({r.get("source_file") or "" for r in g})}
            for k, g in groups.items() if len(g) > 1]


def transfers(lines: List[Dict[str, Any]], acct_of: Dict[str, str]) -> List[Dict[str, Any]]:
    """Money out of one of the business's accounts and into another: same amount, within 2 days."""
    live = [ln for ln in lines if _live(ln) and acct_of.get(ln.get("source_file") or "")]
    ins: Dict[int, List[Dict[str, Any]]] = defaultdict(list)
    for ln in live:
        if ln.get("direction") == "in":
            ins[int(ln["amount_cents"])].append(ln)
    used, out = set(), []
    for ln in sorted(live, key=lambda r: str(r.get("txn_date"))):
        if ln.get("direction") != "out":
            continue
        d0 = _d(ln.get("txn_date"))
        for cand in ins.get(int(ln["amount_cents"]), []):
            if cand["id"] in used or acct_of[cand["source_file"]] == acct_of[ln["source_file"]]:
                continue
            d1 = _d(cand.get("txn_date"))
            if d0 and d1 and abs((d1 - d0).days) <= TRANSFER_DAYS:
                used.add(cand["id"])
                out.append({"out": ln["id"], "in": cand["id"], "cents": int(ln["amount_cents"]),
                            "date": str(d0), "from": acct_of[ln["source_file"]], "to": acct_of[cand["source_file"]],
                            "booked_as": [ln.get("account_code"), cand.get("account_code")]})
                break
    return out


# ── the report ───────────────────────────────────────────────────────────────

def _section(key: str, title: str, findings: List[Dict[str, Any]], headline: str,
             problem: bool = False) -> Dict[str, Any]:
    status = "ok" if not findings else ("problem" if problem else "attention")
    return {"key": key, "title": title, "status": status, "headline": headline, "findings": findings}


def run(tenant_id: str, today: Optional[date] = None, documents: bool = True,
        check_files: bool = True) -> Dict[str, Any]:
    """The whole audit for one business: the bank data, then (documents=True) the documents behind the
    money (vula/commerce/doc_audit.py). Reads only, except that check_files keeps what each PDF itself
    contains on the document (fields._file_check), so a file is read once."""
    today = today or datetime.now(timezone.utc).date()
    lines = _rows("commerce_bank_transactions", TXN_COLS, tenant_id)
    try:
        acct_rows = _rows("commerce_bank_accounts", "id,account_number,bank,private_to", tenant_id)
    except Exception:  # noqa: BLE001 — before migration 261
        acct_rows = []
    private_numbers = [r["account_number"] for r in acct_rows if r.get("private_to")]
    stmts = statements(lines)
    accts = accounts(lines, stmts, private_numbers)
    acct_of = {f: a["key"] for a in accts for f in a["files"]}
    sections: List[Dict[str, Any]] = []

    # accounts
    named = sum(1 for ln in lines if ln.get("bank_account"))
    f_accts = []
    for a in accts:
        f_accts.append({"kind": "account", "severity": "attention" if a["private"] else "info",
                        "text": (f"Account {a['key']}{' ' + ', '.join(a['numbers']) if a['numbers'] else ' (number not recorded)'}"
                                 f"{' — PRIVATE' if a['private'] else ''}: {len(a['files'])} statements, "
                                 f"{a['first']} to {a['last']}, {a['lines']} lines, in {_rands(a['in_cents'])}, "
                                 f"out {_rands(a['out_cents'])}, opening {_rands(a['opening'] or 0)}, "
                                 f"closing {_rands(a['closing'] or 0)}"),
                        "account": a["key"], "files": a["files"]})
    for c in continuations(accts):
        f_accts.append({"kind": "continuation", "severity": "attention", "accounts": [c["from"], c["to"]],
                        "cents": c["difference"],
                        "text": (f"{c['to']} probably continues {c['from']}: {c['from']} closed at {_rands(c['a_closed'])} "
                                 f"on {c['a_last']}, {c['to']} opens at {_rands(c['b_opened'])} on {c['b_first']} "
                                 f"({_rands(c['difference'])} between them — a line not on either statement, or two accounts).")})
    superseded = [f for f, s in stmts.items() if s["statement"] and s["superseded"]]
    if superseded:
        f_accts.append({"kind": "superseded_reads", "severity": "info", "files": superseded,
                        "text": f"{len(superseded)} statement files had every line set aside (re-read later) — not counted."})
    if named < len(lines):
        f_accts.append({"kind": "unnamed_lines", "severity": "problem",
                        "text": f"{len(lines) - named} of {len(lines)} bank lines don't record which account they came from.",
                        "cents": None})
    sections.append(_section("accounts", "Bank accounts behind the lines", f_accts,
                             f"{len(accts)} bank accounts found from {len(stmts) - len(superseded)} statement files in use.",
                             problem=named < len(lines)))

    # coverage
    f_cov = []
    for a in accts:
        cov = coverage(a, stmts)
        for g in cov["gaps"]:
            f_cov.append({"kind": "gap", "severity": "problem", "account": a["key"],
                          "text": (f"Account {a['key']}: no statement between {g['after']} and {g['before']} — closed at "
                                   f"{_rands(g['closed_at'])}, next opens at {_rands(g['opened_at'])} "
                                   f"({_rands(g['missing_cents'])} unaccounted for)."),
                          "cents": g["missing_cents"]})
    sections.append(_section("coverage", "Statement coverage per account", f_cov,
                             "Every account's statements follow on from each other." if not f_cov
                             else f"{len(f_cov)} gaps between statements.", problem=bool(f_cov)))

    # restore: the right copy of a line set aside by mistake
    f_res = []
    for f, s in stmts.items():
        if s.get("restore"):
            r = s["restore"]
            f_res.append({"kind": "restore", "severity": "problem", "file": f, "account": acct_of.get(f),
                          "rows": [x["id"] for x in r],
                          "cents_in": sum(int(x["amount_cents"]) for x in r if x.get("direction") == "in"),
                          "cents_out": sum(int(x["amount_cents"]) for x in r if x.get("direction") == "out"),
                          "text": (f"{f[:40]}: {len(r)} lines set aside by mistake — the running balance only adds up "
                                   f"with them back (out {_rands(sum(int(x['amount_cents']) for x in r if x.get('direction') == 'out'))}, "
                                   f"in {_rands(sum(int(x['amount_cents']) for x in r if x.get('direction') == 'in'))}).")})
    sections.append(_section("restore", "Real bank lines missing from the books", f_res,
                             "No real line was set aside by mistake." if not f_res
                             else f"{sum(len(x['rows']) for x in f_res)} real lines in {len(f_res)} statements were set aside "
                                  "by mistake.", problem=bool(f_res)))

    # chains
    f_chain = []
    for f, s in stmts.items():
        if not s["statement"] or s["superseded"]:
            continue
        no_bal = list(s.get("no_balance") or [])
        breaks = s.get("breaks", 0)
        if breaks or no_bal:
            extra = f", {len(no_bal)} live lines with no balance" if no_bal else ""
            runs = s.get("segments") or []
            f_chain.append({"kind": "chain_break", "severity": "attention", "file": f, "account": acct_of.get(f),
                            "text": (f"{f[:40]}: {breaks} breaks in the running balance{extra} that no set-aside "
                                     "line fills — a line is missing or misread; re-read that statement page."),
                            "rows": [r[0] for r in runs[1:]] + no_bal})
    pairs = sum(len(s.get("misreads") or []) for s in stmts.values())
    sections.append(_section("chains", "Statements that don't add up line by line", f_chain,
                             ("Every statement's running balance adds up." if not f_chain
                              else f"{len(f_chain)} statements still have a break in the running balance.")
                             + (f" {pairs} lines were read twice with the decimal point in the wrong place "
                                "(one copy set aside)." if pairs else "")))

    # duplicates
    dups = duplicates(lines)
    f_dup = [{"kind": "duplicate", "severity": "problem", "rows": d["rows"], "cents": d["cents"] * (len(d["rows"]) - 1),
              "text": f"{d['date']} {d['direction']} {_rands(d['cents'])} counted {len(d['rows'])} times."} for d in dups]
    sections.append(_section("duplicates", "The same bank line counted twice", f_dup,
                             "No bank line is counted twice." if not f_dup
                             else f"{len(f_dup)} lines counted more than once "
                                  f"({_rands(sum(f['cents'] for f in f_dup))} extra).", problem=bool(f_dup)))

    # transfers
    trs = transfers(lines, acct_of)
    inflating = [t for t in trs if (t["booked_as"][1] in INCOME_CODES) or (t["booked_as"][0] not in (None, "transfer"))]
    f_tr = [{"kind": "transfer", "severity": "problem", "rows": [t["out"], t["in"]], "cents": t["cents"],
             "text": (f"{t['date']}: {_rands(t['cents'])} out of {t['from']} and into {t['to']} — booked as "
                      f"{t['booked_as'][0] or 'nothing'} / {t['booked_as'][1] or 'nothing'}.")} for t in inflating]
    sections.append(_section("transfers", "Money moved between the business's own accounts", f_tr,
                             "No transfers between own accounts are counted as income or costs." if not f_tr
                             else f"{len(f_tr)} transfers between own accounts are counted as income and costs "
                                  f"({_rands(sum(t['cents'] for t in inflating))} each way).", problem=bool(f_tr)))

    live = [ln for ln in lines if _live(ln)]
    in_transfer = {i for t in trs for i in (t["out"], t["in"])}

    # categories
    self_cat = [ln for ln in live if (ln.get("categorized_by") in (None, "ai", "default", "asked"))
                and ln["id"] not in in_transfer]
    by_code: Dict[str, List[int]] = defaultdict(list)
    for ln in self_cat:
        by_code[ln.get("account_code") or "(none)"].append(int(ln["amount_cents"]))
    f_cat = [{"kind": "self_categorised", "severity": "attention", "code": code, "count": len(v), "cents": sum(v),
              "text": f"{len(v)} lines Vula put under {code} itself ({_rands(sum(v))}) — the owner to confirm.",
              "rows": [ln["id"] for ln in self_cat if (ln.get("account_code") or "(none)") == code
                       and int(ln["amount_cents"]) >= REVIEW_THRESHOLD_CENTS]}
             for code, v in sorted(by_code.items(), key=lambda kv: -sum(kv[1]))]
    drawings = [ln for ln in live if ln.get("account_code") == "owner_drawings"]
    if drawings:
        f_cat.append({"kind": "owner_drawings", "severity": "attention", "count": len(drawings),
                      "cents": sum(int(ln["amount_cents"]) for ln in drawings), "rows": [ln["id"] for ln in drawings],
                      "text": (f"{len(drawings)} lines booked as the owner's drawings "
                               f"({_rands(sum(int(ln['amount_cents']) for ln in drawings))}) — confirm each is personal.")})
    labour_nw = [ln for ln in live if ln.get("account_code") == "casual_labour" and not ln.get("worker_id")]
    if labour_nw:
        f_cat.append({"kind": "labour_no_worker", "severity": "attention", "count": len(labour_nw),
                      "cents": sum(int(ln["amount_cents"]) for ln in labour_nw), "rows": [ln["id"] for ln in labour_nw],
                      "text": (f"{len(labour_nw)} casual-labour payments ({_rands(sum(int(ln['amount_cents']) for ln in labour_nw))})"
                               " not tied to a worker — the accountant will ask who was paid.")})
    sections.append(_section("categories", "Categories to confirm", f_cat,
                             f"{len(self_cat)} lines were categorised by Vula itself, not the owner."))

    # documents behind transactions
    no_doc_out = [ln for ln in live if ln.get("direction") == "out" and ln["id"] not in in_transfer
                  and int(ln["amount_cents"]) >= DOC_THRESHOLD_CENTS and ln.get("account_code") not in NO_DOC_CODES
                  and not (ln.get("matched_expense_id") or ln.get("matched_invoice_id") or ln.get("worker_id"))]
    no_inv_in = [ln for ln in live if ln.get("direction") == "in" and ln["id"] not in in_transfer
                 and ln.get("account_code") in INCOME_CODES
                 and not (ln.get("matched_invoice_id") or ln.get("matched_order_id"))]
    f_doc = []
    if no_doc_out:
        f_doc.append({"kind": "payment_no_document", "severity": "problem", "count": len(no_doc_out),
                      "cents": sum(int(ln["amount_cents"]) for ln in no_doc_out), "rows": [ln["id"] for ln in no_doc_out],
                      "text": (f"{len(no_doc_out)} payments over {_rands(DOC_THRESHOLD_CENTS)} "
                               f"({_rands(sum(int(ln['amount_cents']) for ln in no_doc_out))}) have no bill, receipt or worker record.")})
    if no_inv_in:
        f_doc.append({"kind": "receipt_no_invoice", "severity": "attention", "count": len(no_inv_in),
                      "cents": sum(int(ln["amount_cents"]) for ln in no_inv_in), "rows": [ln["id"] for ln in no_inv_in],
                      "text": (f"{len(no_inv_in)} receipts booked as income "
                               f"({_rands(sum(int(ln['amount_cents']) for ln in no_inv_in))}) have no invoice in Vula behind them.")})
    sections.append(_section("documents", "Documents behind the money", f_doc,
                             "Every payment and receipt has its document." if not f_doc
                             else "Some money has no document behind it.", problem=bool(no_doc_out)))

    # paperwork
    sections.append(_paperwork(tenant_id))
    # dates
    sections.append(_dates(tenant_id, lines, today))
    # vat
    sections.append(_vat(tenant_id, live, in_transfer, today))
    sections.append(_pop_lines(tenant_id, lines))
    # the documents behind the money (19 Nov 2026, Phase 0b)
    if documents:
        try:
            from vula.commerce import doc_audit
            sections.extend(doc_audit.sections(tenant_id, check_files=check_files))
        except Exception as exc:  # noqa: BLE001 — the bank audit stands on its own
            log.warning("books audit: document checks failed for %s: %s", tenant_id, exc)
            sections.append(_section("doc_figures", "Documents whose figures don't match the file", [],
                                     "The documents couldn't be checked just now — run the check again."))

    problems = sum(1 for s in sections if s["status"] == "problem")
    return {"tenant_id": tenant_id, "generated_at": datetime.now(timezone.utc).isoformat(),
            "kind": "books_audit", "version": 1,
            "summary": {"bank_lines": len(lines), "live_lines": len(live), "statements": len(stmts),
                        "accounts": len(accts), "sections_with_problems": problems,
                        "clean": problems == 0 and all(s["status"] == "ok" for s in sections)},
            "accounts": [{k: (str(v) if isinstance(v, date) else v) for k, v in a.items()} for a in accts],
            "sections": sections}


def _paperwork(tenant_id: str) -> Dict[str, Any]:
    f: List[Dict[str, Any]] = []
    try:
        inv = _rows("commerce_invoices", "id,direction,doc_type,status,total_cents", tenant_id)
        exp = _rows("commerce_expenses", "id,status,amount_cents,receipt_doc_id,receipt_url", tenant_id)
    except Exception as exc:  # noqa: BLE001
        log.debug("paperwork check skipped: %s", exc)
        return _section("paperwork", "Bills, invoices and expenses still open", [], "Couldn't read bills and invoices.")
    for direction, label in (("inbound", "supplier bills"), ("outbound", "customer invoices")):
        drafts = [r for r in inv if r.get("direction") == direction and r.get("doc_type") == "invoice"
                  and r.get("status") == "draft"]
        if drafts:
            f.append({"kind": f"draft_{direction}", "severity": "attention", "count": len(drafts),
                      "cents": sum(int(r.get("total_cents") or 0) for r in drafts), "rows": [r["id"] for r in drafts],
                      "text": (f"{len(drafts)} {label} still in draft "
                               f"({_rands(sum(int(r.get('total_cents') or 0) for r in drafts))}) — record, cancel or mark duplicate.")})
    nodoc = [r for r in exp if r.get("status") not in ("cancelled",) and not (r.get("receipt_doc_id") or r.get("receipt_url"))]
    if nodoc:
        f.append({"kind": "expense_no_document", "severity": "attention", "count": len(nodoc),
                  "cents": sum(int(r.get("amount_cents") or 0) for r in nodoc), "rows": [r["id"] for r in nodoc],
                  "text": (f"{len(nodoc)} expenses ({_rands(sum(int(r.get('amount_cents') or 0) for r in nodoc))}) "
                           "have no receipt or invoice attached.")})
    return _section("paperwork", "Bills, invoices and expenses still open", f,
                    "No open paperwork." if not f else f"{len(f)} kinds of open paperwork.")


def _dates(tenant_id: str, lines: List[Dict[str, Any]], today: date) -> Dict[str, Any]:
    dates = [d for d in (_d(ln.get("txn_date")) for ln in lines) if d]
    start = (min(dates) - timedelta(days=366)) if dates else today - timedelta(days=3 * 366)
    f: List[Dict[str, Any]] = []
    try:
        je = _rows("journal_entries", "id,entry_date,source_type,source_id", tenant_id)
    except Exception:  # noqa: BLE001
        je = []
    odd = [r for r in je if r.get("source_type") != "opening_balance"
           and ((_d(r.get("entry_date")) or today) < start
                or (_d(r.get("entry_date")) or today) > today + timedelta(days=31))]
    if odd:
        f.append({"kind": "ledger_date", "severity": "problem", "count": len(odd), "rows": [r["id"] for r in odd],
                  "text": (f"{len(odd)} ledger entries are dated outside the trading period ("
                           + ", ".join(sorted({str(r.get('entry_date')) for r in odd})[:6]) + ") — most likely a misread document date.")})
    return _section("dates", "Dates outside the trading period", f,
                    "All ledger dates fall in the trading period." if not f else f"{len(odd)} ledger entries have odd dates.",
                    problem=bool(f))


def _vat(tenant_id: str, live: List[Dict[str, Any]], in_transfer: set, today: date) -> Dict[str, Any]:
    try:
        st = (_client().table("commerce_invoice_settings").select("vat_registered").eq("tenant_id", tenant_id)
              .limit(1).execute().data or [{}])[0]
        registered = bool(st.get("vat_registered"))
    except Exception:  # noqa: BLE001
        registered = None
    income = sorted(((_d(ln.get("txn_date")), int(ln["amount_cents"])) for ln in live
                     if ln.get("direction") == "in" and ln.get("account_code") in INCOME_CODES
                     and ln["id"] not in in_transfer and _d(ln.get("txn_date"))), key=lambda x: x[0])
    peak, peak_end, j, run = 0, None, 0, 0
    for i, (d, c) in enumerate(income):
        run += c
        while income[j][0] <= d - timedelta(days=365):
            run -= income[j][1]
            j += 1
        if run > peak:
            peak, peak_end = run, d
    f: List[Dict[str, Any]] = []
    if registered is False and peak > VAT_THRESHOLD_CENTS:
        f.append({"kind": "vat_threshold", "severity": "problem", "cents": peak,
                  "text": (f"Not VAT registered, but money in booked as income over 12 months to {peak_end} is "
                           f"{_rands(peak)} — above the R1,000,000 compulsory registration line (VAT Act s23). "
                           "Some of it may not be taxable supplies (loans, deposits held, transfers this audit "
                           "couldn't pair): a question for the business and its accountant, not something Vula decides.")})
    head = (f"{'Registered' if registered else 'Not registered' if registered is False else 'Registration unknown'}; "
            f"highest 12-month income from the bank: {_rands(peak)}.")
    return _section("vat", "VAT registration", f, head, problem=bool(f))


def _pop_lines(tenant_id: str, lines: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Rows made from proofs of payment sent on WhatsApp (bank_rec.stage_pop_for_review). They are not
    lines of a bank statement and are never counted (ledger_events.is_pop) — listed so the business sees
    what they were, and one recorded as money IN that the business or one of its people paid is named
    (20 Nov 2026, DIGG: two of the owner's own payments to R Engelbrecht staged as income)."""
    pops = [ln for ln in lines if (ln.get("source_file") or "") == "whatsapp_pop"]
    if not pops:
        return _section("pop_lines", "Proofs of payment sent on WhatsApp", [],
                        "No bank lines were made from proofs of payment.")
    from vula.commerce import bank_rec
    from vula.commerce.service import is_own_name
    try:
        docs = _rows("vula_filed_documents", "id,category,fields,status", tenant_id)
    except Exception:  # noqa: BLE001
        docs = []
    pop_docs = [d for d in docs if d.get("category") == "Proof of Payment"]
    own = bank_rec._own_names_now(tenant_id)
    f: List[Dict[str, Any]] = []
    for ln in sorted(pops, key=lambda r: str(r.get("txn_date"))):
        cents = int(ln.get("amount_cents") or 0)
        doc = next((d for d in pop_docs if int((d.get("fields") or {}).get("amount_cents") or 0) == cents
                    and str((d.get("fields") or {}).get("date") or "")[:10] == str(ln.get("txn_date"))[:10]), None)
        payer = str(((doc or {}).get("fields") or {}).get("payer") or "").strip()
        member = bank_rec._team_member_paying(tenant_id, payer) if payer else None
        by_us = bool(payer) and (member or is_own_name(payer, own))
        wrong_way = ln.get("direction") == "in" and by_us
        was = ("set aside" if ln.get("match_status") == "ignored" else
               f"booked as {ln.get('account_code') or 'nothing yet'}")
        text = (f"{ln.get('txn_date')}: {ln.get('description') or 'proof of payment'}, {_rands(cents)} "
                f"{'in' if ln.get('direction') == 'in' else 'out'} ({was}) — a screenshot, not a bank line: "
                "no longer counted; the bank statement's own line is.")
        if wrong_way:
            text += (f" It was recorded as money IN, but {member or payer} paid it"
                     + (" personally — a question for the accountant (the owner's loan or a staff claim)."
                        if member else "."))
        f.append({"kind": "pop_line", "severity": "problem" if wrong_way else "attention", "rows": [ln["id"]],
                  "doc": (doc or {}).get("id"), "payer": member or payer or None, "cents": cents, "text": text})
    wrong = [x for x in f if x["severity"] == "problem"]
    return _section("pop_lines", "Proofs of payment sent on WhatsApp", f,
                    f"{len(f)} bank lines were made from proofs of payment; none is counted any more."
                    + (f" {len(wrong)} of them were recorded as money in although the business or one of its people "
                       "paid." if wrong else ""), problem=bool(wrong))


def text_report(rep: Dict[str, Any]) -> str:
    """The report as plain text (the CLI and a WhatsApp/email summary)."""
    out = [f"Books audit — {rep['tenant_id']} — {rep['generated_at'][:16]} UTC",
           f"{rep['summary']['bank_lines']} bank lines ({rep['summary']['live_lines']} live) from "
           f"{rep['summary']['statements']} files, {rep['summary']['accounts']} bank accounts.", ""]
    for s in rep["sections"]:
        mark = {"ok": "OK  ", "attention": "LOOK", "problem": "FIX "}[s["status"]]
        out.append(f"[{mark}] {s['title']}: {s['headline']}")
        for fi in s["findings"][:12]:
            out.append(f"       - {fi['text']}")
        if len(s["findings"]) > 12:
            out.append(f"       … and {len(s['findings']) - 12} more")
    return "\n".join(out)
