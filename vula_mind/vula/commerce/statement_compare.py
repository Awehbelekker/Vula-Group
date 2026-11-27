"""vula/commerce/statement_compare.py — an official bank statement against what Vula holds.

17 Nov 2026 (Ian, DIGG): DIGG's bank lines came from weekly and monthly FNB e-statements, some read
with the decimal point in the wrong place, some of the right copies set aside (books_audit.py). For
audit-grade books DIGG supplies the official FNB statement of every account; this compares each one,
line by line, with Vula's rows for that period and proposes what to change. Nothing changes until
an owner or manager confirms (`apply`), and what they confirmed is kept (vula_statement_checks).

Reading: the deterministic FNB reader only (vula/ingestion/fnb_statement.py), accepted only when
every running balance reconciles to the cent — never a model. An unreadable statement proposes
nothing.

Matching each official line, strongest first:
  1. date + direction + amount + balance after
  2. date + direction + amount, sharing a word of the description
  3. a misread: same date and direction, sharing a word, amount or balance ×10 / ×100 apart
Then, per official line: on a live row → fine (tagged with the account number); only on a set-aside
row → restore it; on a live misread → set the misread aside and add the right line; nowhere → add.
A live row of THIS account's statements in the period that the official statement doesn't have is
proposed set aside — or for a person's look when it's tied to an invoice, bill, order or worker.
Rows in other statement files (another account's) are left alone.
"""
from __future__ import annotations

import logging
import re
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

log = logging.getLogger(__name__)

ROW_COLS = ("id,source_file,txn_date,description,amount_cents,direction,balance_cents,match_status,"
            "bank_account,matched_invoice_id,matched_expense_id,matched_order_id,worker_id")
OWN_FILE_SHARE = 0.5        # a file is this account's when half its live rows in the period are on it
OWN_FILE_MIN = 2            # …and at least two (another account's lines can't match exactly)
UNREADABLE = ("This statement couldn't be read to the cent (its running balances don't add up as read), "
              "so nothing is proposed. Send the PDF exactly as the bank issued it.")


def _client():
    from vula.commerce.service import _client as c
    return c()


def _rands(cents: int) -> str:
    return f"{'-' if cents < 0 else ''}R{abs(cents) / 100:,.2f}"


def _words(text: str) -> set:
    return {w for w in re.findall(r"[a-z]{3,}", (text or "").lower())
            if w not in {"fnb", "app", "payment", "pos", "purchase", "the", "and", "account", "transfer"}}


def _tenfold(a: Optional[int], b: Optional[int]) -> bool:
    if not a or not b or a == b:
        return False
    hi, lo = max(abs(a), abs(b)), min(abs(a), abs(b))
    return hi in (10 * lo, 100 * lo)


def read(pdf_path: Path, password: Optional[str] = None) -> Tuple[str, Optional[Dict[str, Any]]]:
    from vula.ingestion import fnb_statement
    text = fnb_statement.pdf_text(pdf_path, password)
    return text, fnb_statement.parse(text)


def _rows_in(tenant_id: str, first: str, last: str) -> List[Dict[str, Any]]:
    out, lo, page = [], 0, 1000
    while True:
        got = (_client().table("commerce_bank_transactions").select(ROW_COLS).eq("tenant_id", tenant_id)
               .gte("txn_date", first).lte("txn_date", last).order("id").range(lo, lo + page - 1)
               .execute().data or [])
        out.extend(got)
        if len(got) < page:
            return out
        lo += page


def _linked(row: Dict[str, Any]) -> bool:
    return bool(row.get("matched_invoice_id") or row.get("matched_expense_id")
                or row.get("matched_order_id") or row.get("worker_id"))


def match(official: List[Dict[str, Any]], rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Pair every official line with Vula's rows (see the module docstring). Pure: no database."""
    live = lambda r: r.get("match_status") != "ignored"  # noqa: E731
    used: set = set()
    paired: Dict[int, Tuple[Dict[str, Any], str]] = {}

    def take(i: int, cands: Iterable[Dict[str, Any]], how: str) -> None:
        cands = [r for r in cands if r["id"] not in used]
        if not cands:
            return
        best = sorted(cands, key=lambda r: (not live(r),))[0]       # a live row first
        used.add(best["id"])
        paired[i] = (best, how)

    by4: Dict[Tuple, List[Dict[str, Any]]] = defaultdict(list)
    by3: Dict[Tuple, List[Dict[str, Any]]] = defaultdict(list)
    by_day: Dict[Tuple, List[Dict[str, Any]]] = defaultdict(list)
    for r in rows:
        d = str(r.get("txn_date"))[:10]
        by4[(d, r.get("direction"), int(r.get("amount_cents") or 0), r.get("balance_cents"))].append(r)
        by3[(d, r.get("direction"), int(r.get("amount_cents") or 0))].append(r)
        by_day[(d, r.get("direction"))].append(r)
    for i, t in enumerate(official):
        take(i, by4.get((t["date"], t["direction"], t["amount_cents"], t["balance_cents"]), []), "exact")
    for i, t in enumerate(official):
        if i not in paired:
            w = _words(t["description"])
            take(i, [r for r in by3.get((t["date"], t["direction"], t["amount_cents"]), [])
                     if w & _words(r.get("description"))], "amount_and_words")
    for i, t in enumerate(official):
        if i not in paired:
            w = _words(t["description"])
            take(i, [r for r in by_day.get((t["date"], t["direction"]), [])
                     if (w & _words(r.get("description")) or not w)
                     and (_tenfold(int(r.get("amount_cents") or 0), t["amount_cents"])
                          or (int(r.get("amount_cents") or 0) == t["amount_cents"]
                              and _tenfold(r.get("balance_cents"), t["balance_cents"])))], "misread")
    return {"paired": paired, "used": used}


def proposals(official: List[Dict[str, Any]], rows: List[Dict[str, Any]], account: Optional[str],
              filename: str) -> Dict[str, Any]:
    """The actions to propose, and the counts behind them. Pure: no database."""
    m = match(official, rows)
    paired, used = m["paired"], m["used"]
    actions: List[Dict[str, Any]] = []
    n = 0

    def act(kind: str, **kw: Any) -> None:
        nonlocal n
        n += 1
        actions.append({"id": f"a{n}", "kind": kind, **kw})

    ok = 0
    tag_ids: List[str] = []
    for i, t in enumerate(official):
        line = {k: t[k] for k in ("date", "description", "amount_cents", "direction", "balance_cents")}
        hit = paired.get(i)
        if hit is None:
            act("add", line=line, reason="On the official statement, not in Vula.")
            continue
        row, how = hit
        is_live = row.get("match_status") != "ignored"
        if how == "misread":
            if is_live:
                act("set_aside", row_id=row["id"], cents=int(row.get("amount_cents") or 0),
                    reason=(f"Misread copy: the official statement shows {_rands(t['amount_cents'])} "
                            f"(balance {_rands(t['balance_cents'])}), Vula has {_rands(int(row.get('amount_cents') or 0))}."))
            act("add", line=line, reason="The right amount from the official statement (the misread copy is set aside).")
            continue
        if not is_live:
            act("restore", row_id=row["id"], cents=t["amount_cents"], line=line,
                reason="On the official statement but set aside in Vula.")
            tag_ids.append(row["id"])
            continue
        ok += 1
        if account and row.get("bank_account") != account:
            tag_ids.append(row["id"])

    # this account's own statement files in the period: most of their live rows are on it
    live_by_file: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for r in rows:
        if r.get("match_status") != "ignored":
            live_by_file[r.get("source_file") or ""].append(r)
    own = set()
    for f, rs in live_by_file.items():
        hit = sum(1 for r in rs if r["id"] in used)
        if f != filename and rs and hit >= OWN_FILE_MIN and hit / len(rs) >= OWN_FILE_SHARE:
            own.add(f)
    other = 0
    for f, rs in live_by_file.items():
        for r in rs:
            if r["id"] in used:
                continue
            if f in own:
                if _linked(r):
                    act("review", row_id=r["id"], cents=int(r.get("amount_cents") or 0),
                        reason=("Not on the official statement, but tied to an invoice, bill, order or worker — "
                                "a person should look before it is set aside."))
                else:
                    act("set_aside", row_id=r["id"], cents=int(r.get("amount_cents") or 0),
                        reason="Not on the official statement for this account and period.")
            else:
                other += 1
    if account and tag_ids:
        act("tag", row_ids=sorted(set(tag_ids)), account=account,
            reason=f"These lines are from account …{account[-4:]}.")
    counts = defaultdict(int)
    for a in actions:
        counts[a["kind"]] += 1
    return {"actions": actions,
            "summary": {"official_lines": len(official), "already_right": ok, "restore": counts["restore"],
                        "add": counts["add"], "set_aside": counts["set_aside"], "review": counts["review"],
                        "tagged": len(set(tag_ids)), "own_files": sorted(own),
                        "other_accounts_rows_left_alone": other}}


def compare(tenant_id: str, *, filename: str, text: Optional[str] = None, pdf_path: Optional[Path] = None,
            created_by: Optional[str] = None, save: bool = True) -> Dict[str, Any]:
    """Read an official statement and propose changes. Writes only the vula_statement_checks row."""
    from vula.commerce import bank_accounts
    from vula.ingestion import fnb_statement
    if text is None:
        if pdf_path is None:
            return {"error": "Send the statement PDF."}
        from vula.commerce.bank_rec import get_statement_password
        try:
            text = fnb_statement.pdf_text(Path(pdf_path), get_statement_password(tenant_id))
        except Exception as exc:  # noqa: BLE001
            return {"error": f"The PDF couldn't be opened ({type(exc).__name__}). If it has a password, "
                             "save the ID-number password under Bank › Settings first."}
    parsed = fnb_statement.parse(text)
    if not parsed or not parsed.get("transactions"):
        return {"error": UNREADABLE}
    official = parsed["transactions"]
    first, last = parsed["period"]
    account = bank_accounts.account_number(text)
    rows = _rows_in(tenant_id, first, last)
    prop = proposals(official, rows, account, filename)
    net = parsed["closing_cents"] - parsed["opening_cents"]
    out = {"filename": filename, "account": f"…{account[-4:]}" if account else None,
           "bank": bank_accounts.bank_name(text), "period": [first, last],
           "opening_cents": parsed["opening_cents"], "closing_cents": parsed["closing_cents"],
           "official_net_cents": net, **prop}
    if save:
        try:
            got = (_client().table("vula_statement_checks").insert({
                "tenant_id": tenant_id, "filename": filename, "account_number": account, "bank": out["bank"],
                "period_from": first, "period_to": last, "opening_cents": parsed["opening_cents"],
                "closing_cents": parsed["closing_cents"], "actions": prop["actions"],
                "summary": {**prop["summary"], "official_net_cents": net}, "status": "proposed",
                "created_by": created_by}).execute().data or [])
            out["check_id"] = got[0]["id"] if got else None
        except Exception as exc:  # noqa: BLE001
            log.warning("statement check not saved for %s: %s", tenant_id, exc)
            out["check_id"] = None
    return out


def apply(tenant_id: str, check_id: str, *, person: str, full_access: bool,
          accept: Optional[List[str]] = None, actor: Optional[dict] = None) -> Dict[str, Any]:
    """Do the confirmed actions of one comparison (all, or the ids in `accept`), read them back and
    keep the record. Owner or manager only. Never posts to the ledger."""
    if not full_access:
        return {"error": "Only an owner or manager can change the bank lines."}
    db = _client()
    rows = (db.table("vula_statement_checks").select("*").eq("tenant_id", tenant_id).eq("id", check_id)
            .limit(1).execute().data or [])
    if not rows:
        return {"error": "That comparison isn't there."}
    check = rows[0]
    if check.get("status") != "proposed":
        return {"error": f"That comparison is already {check.get('status')}."}
    chosen = [a for a in (check.get("actions") or []) if accept is None or a["id"] in set(accept)]
    tbl = "commerce_bank_transactions"
    done, skipped, touched, tagged = [], [], [], []
    for a in chosen:
        kind = a["kind"]
        try:
            if kind == "restore":
                db.table(tbl).update({"match_status": "unmatched", "excluded_reason": None}) \
                    .eq("tenant_id", tenant_id).eq("id", a["row_id"]).execute()
                touched.append((a["row_id"], "unmatched"))
            elif kind == "set_aside":
                db.table(tbl).update({"match_status": "ignored", "excluded_reason": a["reason"][:300]}) \
                    .eq("tenant_id", tenant_id).eq("id", a["row_id"]).execute()
                touched.append((a["row_id"], "ignored"))
            elif kind == "add":
                ln = a["line"]
                same = (db.table(tbl).select("id").eq("tenant_id", tenant_id).eq("txn_date", ln["date"])
                        .eq("amount_cents", ln["amount_cents"]).eq("description", ln["description"])
                        .limit(1).execute().data or [])
                patch = {"match_status": "unmatched", "excluded_reason": None}
                if check.get("account_number"):
                    patch["bank_account"] = check["account_number"]
                if same:
                    db.table(tbl).update(patch).eq("tenant_id", tenant_id).eq("id", same[0]["id"]).execute()
                    touched.append((same[0]["id"], "unmatched"))
                else:
                    ins = db.table(tbl).insert({
                        "tenant_id": tenant_id, "txn_date": ln["date"], "description": ln["description"],
                        "amount_cents": ln["amount_cents"], "direction": ln["direction"],
                        "balance_cents": ln["balance_cents"], "reference": None, "source_file": check["filename"],
                        **patch}).execute().data or []
                    if ins:
                        touched.append((ins[0]["id"], "unmatched"))
            elif kind == "tag":
                for rid in a["row_ids"]:
                    db.table(tbl).update({"bank_account": a["account"]}).eq("tenant_id", tenant_id) \
                        .eq("id", rid).execute()
                    tagged.append(rid)
            elif kind == "review":
                skipped.append({"id": a["id"], "why": "needs a person"})
                continue
            done.append(a["id"])
        except Exception as exc:  # noqa: BLE001
            skipped.append({"id": a["id"], "why": type(exc).__name__})
    # read back
    wrong = []
    for rid, want in touched:
        got = (db.table(tbl).select("match_status").eq("tenant_id", tenant_id).eq("id", rid).limit(1)
               .execute().data or [{}])[0]
        if got.get("match_status") != want:
            wrong.append(rid)
    record = {"done": done, "skipped": skipped, "not_saved": wrong}
    try:     # books_v2: the lines changed here say something new in the books (like any bank edit)
        from vula.commerce import ledger_events
        if ledger_events.on(tenant_id):
            ledger_events.sync_bank_lines(tenant_id, [rid for rid, _ in touched] + tagged)
    except Exception as exc:  # noqa: BLE001
        log.warning("statement check %s: lines not re-posted to the books: %s", check_id, exc)
    db.table("vula_statement_checks").update({
        "status": "applied", "applied_by": person, "applied_at": datetime.now(timezone.utc).isoformat(),
        "applied": record}).eq("tenant_id", tenant_id).eq("id", check_id).execute()
    try:
        from vula.api.merchant_audit import audit
        audit(tenant_id, actor, "statement_check_applied", check_id=check_id, file=check["filename"],
              done=len(done), skipped=len(skipped), not_saved=len(wrong))
    except Exception:  # noqa: BLE001
        pass
    if wrong:
        return {"ok": False, **record, "error": f"{len(wrong)} changes didn't save — open the comparison and try again."}
    return {"ok": True, **record}


def text_summary(out: Dict[str, Any]) -> str:
    if out.get("error"):
        return out["error"]
    s = out["summary"]
    return (f"{out['filename']}: account {out.get('account') or '(number not printed)'}, {out['period'][0]} to "
            f"{out['period'][1]}, opening {_rands(out['opening_cents'])}, closing {_rands(out['closing_cents'])}.\n"
            f"{s['official_lines']} lines on the official statement: {s['already_right']} already right in Vula, "
            f"{s['restore']} to restore, {s['add']} to add, {s['set_aside']} to set aside, {s['review']} for a "
            f"person's look; {s['tagged']} to tag with the account; {s['other_accounts_rows_left_alone']} rows of "
            "other accounts left alone.")


# ── 25 Nov 2026: check every waiting statement again and apply them in one go ──────────────
# DIGG uploaded 37 official statements on 17–20 Nov and applied 2: each comparison has to be opened
# and applied on its own, several are the same period uploaded twice, and by now some of their actions
# are out of date (a line since restored or tied). `apply_all` re-reads each kept official statement,
# compares it again with the rows as they are now, and applies only what needs no person — restore,
# add, set aside, tag; anything tied to an invoice, bill, order or worker stays for review. A
# statement that ends before the business's books start (DIGG's Oct–Dec 2025 …7663) is left alone.

SAFE_KINDS = ("restore", "add", "set_aside", "tag")


def _kept_statement(tenant_id: str, check: Dict[str, Any], docs: List[Dict[str, Any]],
                    accounts: Dict[str, str]) -> Optional[Dict[str, Any]]:
    """The kept official statement behind a comparison: by its file name, else by account and period
    (the same bytes uploaded again keep the first copy's name)."""
    for d in docs:
        if d.get("filename") == check.get("filename") and d.get("file_url"):
            return d
    for d in docs:
        f = d.get("fields") or {}
        if (accounts.get(str(f.get("bank_account_id"))) == check.get("account_number")
                and str(f.get("period_from") or "")[:10] == str(check.get("period_from") or "")[:10]
                and str(f.get("period_to") or "")[:10] == str(check.get("period_to") or "")[:10]
                and d.get("file_url")):
            return d
    return None


def apply_all(tenant_id: str, *, person: str, full_access: bool, actor: Optional[dict] = None,
              preview: bool = True, fetch: Optional[Any] = None) -> Dict[str, Any]:
    """Every waiting comparison, checked again against the rows as they are now: what each would do
    (`preview`, writes nothing), or done — the safe actions applied, the old comparison closed."""
    import tempfile
    from vula import db_pages
    from vula.commerce import ledger_v2, statement_files
    from vula.commerce.management_pack import first_trading_day
    if not full_access:
        return {"error": "Only an owner or manager can change the bank lines."}
    db = _client()
    waiting = db_pages.all_rows(lambda: db.table("vula_statement_checks")
                                .select("id,filename,account_number,period_from,period_to,created_at,status")
                                .eq("tenant_id", tenant_id).eq("status", "proposed"))
    docs = [d for d in (db.table("vula_filed_documents").select("id,filename,fields,file_url,status")
                        .eq("tenant_id", tenant_id).eq("category", statement_files.CATEGORY).execute().data or [])
            if (d.get("fields") or {}).get("official") and d.get("status") != "duplicate"]
    accounts = {str(a["id"]): a.get("account_number") for a in (db.table("commerce_bank_accounts")
                .select("id,account_number").eq("tenant_id", tenant_id).execute().data or [])}
    first = first_trading_day(tenant_id, ledger_v2.is_on(tenant_id))
    if fetch is None:
        from vula.storage_links import fetch_sync as fetch
    groups: Dict[tuple, List[Dict[str, Any]]] = defaultdict(list)
    for c in waiting:
        groups[(c.get("account_number") or "", str(c.get("period_from"))[:10], str(c.get("period_to"))[:10])].append(c)
    out: List[Dict[str, Any]] = []
    totals = defaultdict(int)
    for (acct, frm, to), checks in sorted(groups.items(), key=lambda kv: (kv[0][1], kv[0][0])):
        checks.sort(key=lambda c: str(c.get("created_at") or ""), reverse=True)
        item: Dict[str, Any] = {"account": f"…{acct[-4:]}" if acct else None, "from": frm, "to": to,
                                "copies": len(checks), "check_ids": [c["id"] for c in checks]}
        out.append(item)
        if first and to < first.isoformat():
            item.update(status="left", why=f"Before the books start ({first.isoformat()}) — left for a person.")
            continue
        doc = next((d for d in (_kept_statement(tenant_id, c, docs, accounts) for c in checks) if d), None)
        if not doc:
            item.update(status="left", why="The statement file isn't kept — upload it again to check it.")
            continue
        try:
            data = fetch(doc["file_url"])
        except Exception as exc:  # noqa: BLE001
            item.update(status="left", why=f"The statement file couldn't be read ({type(exc).__name__}).")
            continue
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "statement.pdf"
            path.write_bytes(data)
            res = compare(tenant_id, filename=checks[0]["filename"], pdf_path=path,
                          created_by=person, save=not preview)
        if res.get("error"):
            item.update(status="left", why=res["error"])
            continue
        safe = [a for a in res["actions"] if a["kind"] in SAFE_KINDS]
        s = res["summary"]
        counts = {"already_right": s["already_right"], "restore": s["restore"], "add": s["add"],
                  "set_aside": s["set_aside"], "tagged": s["tagged"], "review": s["review"]}
        item.update(counts=counts)
        for k, v in counts.items():
            totals[k] += v
        if preview:
            item["status"] = "would_apply" if safe else "nothing_to_do"
            continue
        applied = apply(tenant_id, res["check_id"], person=person, full_access=True,
                        accept=[a["id"] for a in safe], actor=actor) if res.get("check_id") else {"ok": False}
        item["status"] = "applied" if applied.get("ok") else "failed"
        if not applied.get("ok"):
            item["why"] = applied.get("error") or "The changes weren't saved."
            continue
        now = datetime.now(timezone.utc).isoformat()
        for c in checks:            # the older comparisons of this statement: done by the fresh one
            db.table("vula_statement_checks").update({
                "status": "discarded", "applied_by": person, "applied_at": now,
                "applied": {"replaced_by": res["check_id"], "why": "checked again and applied"}}) \
                .eq("tenant_id", tenant_id).eq("id", c["id"]).eq("status", "proposed").execute()
    result = {"preview": preview, "statements": out, "totals": dict(totals),
              "applied": sum(1 for i in out if i.get("status") == "applied"),
              "left": sum(1 for i in out if i.get("status") == "left"),
              "failed": sum(1 for i in out if i.get("status") == "failed")}
    if not preview:
        try:
            from vula.api.merchant_audit import audit
            audit(tenant_id, actor, "statement_checks_applied_all", applied=result["applied"], left=result["left"],
                  failed=result["failed"], totals=result["totals"])
        except Exception:  # noqa: BLE001
            pass
    return result
