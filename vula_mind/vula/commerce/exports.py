"""vula/commerce/exports.py — the books as CSV files an accountant imports (the general ledger and the
bank lines). Used by the download routes (vula/api/commerce.py) and the year-end pack
(vula/commerce/year_end_pack.py), so both hand out exactly the same file.

Account numbers are shown by their last 4 digits only; a private account's lines and the rows made from
a proof of payment (never a bank line, ledger_events.is_pop) are left out. Reads only; integer cents.
"""
from __future__ import annotations

import csv
import io
from typing import Optional, Tuple

JOURNAL_CSV = ["Date", "Entry", "Description", "Account code", "Account", "Debit (R)", "Credit (R)",
               "Source", "Source id", "Document", "Bank line", "Bank account", "Reverses", "View"]
BANK_CSV = ["Date", "Account", "Description", "Money in (R)", "Money out (R)", "Balance (R)", "Category",
            "Status", "Filed by", "Reference", "Project", "Statement file"]


def _client():
    from vula.commerce import service
    return service._client()


def own_numbers(tenant_id: str) -> list:
    """The business's own bank account numbers, longest first (to mask them wherever text carries one)."""
    try:
        rows = (_client().table("commerce_bank_accounts").select("account_number").eq("tenant_id", tenant_id)
                .execute().data or [])
    except Exception:  # noqa: BLE001
        return []
    nums = {"".join(ch for ch in str(r.get("account_number") or "") if ch.isdigit()) for r in rows}
    return sorted((n for n in nums if len(n) >= 6), key=len, reverse=True)


def mask_own(text, numbers) -> str:
    """Text from a bank line or a file name with the business's own account numbers cut to their last
    4 digits (a statement file name or a transfer's description often carries the whole number)."""
    out = str(text or "")
    for n in numbers:
        out = out.replace(n, "…" + n[-4:])
    return out


def _file(name, numbers) -> str:
    from vula.commerce.statement_files import mask_name
    return mask_name(mask_own(name, numbers))


def ledger_csv(tenant_id: str, since: Optional[str] = None, until: Optional[str] = None,
               view: Optional[str] = None) -> Tuple[str, str]:
    """(the general ledger as CSV, its file name). A business on the one book gets one row per journal
    line (debit/credit, with its source document and bank line); otherwise one row per bank line."""
    from vula.commerce import accounting, ledger_v2
    rows = accounting.general_ledger(tenant_id, since, until, view)
    nums = own_numbers(tenant_id)
    buf = io.StringIO()
    w = csv.writer(buf)
    if ledger_v2.is_on(tenant_id):
        w.writerow(JOURNAL_CSV)
        for r in rows:
            w.writerow([r["date"], f"{r['entry_id']} v{r['version']}", mask_own(r["description"], nums),
                        r["account_code"],
                        r["account"], f"{r['debit_cents'] / 100:.2f}", f"{r['credit_cents'] / 100:.2f}",
                        r.get("source_type") or "", r.get("source_id") or "", r.get("doc_id") or "",
                        r.get("bank_txn_id") or "", r.get("bank_account") or "", r.get("reverses_entry_id") or "",
                        r.get("view") or ""])
        return buf.getvalue(), f"ledger-{tenant_id}-{view or 'accrual'}.csv"
    w.writerow(["Date", "Description", "Account", "Direction", "Amount (R)", "VAT (R)", "Reference", "Source"])
    for r in rows:
        w.writerow([r.get("date"), mask_own(r.get("description"), nums), r.get("account"), r.get("direction"),
                    f"{(r.get('amount_cents') or 0)/100:.2f}", f"{(r.get('vat_cents') or 0)/100:.2f}",
                    mask_own(r.get("reference"), nums), _file(r.get("source"), nums)])
    return buf.getvalue(), f"ledger-{tenant_id}.csv"


def bank_lines(tenant_id: str, account_id: Optional[str] = None, since: Optional[str] = None,
               until: Optional[str] = None) -> list:
    """Every bank line (one account, or all) the business may hand out, oldest first."""
    db = _client()
    accounts = {a["account_number"]: a for a in (db.table("commerce_bank_accounts")
                .select("id,account_number,name,private_to").eq("tenant_id", tenant_id).execute().data or [])
                if a.get("account_number")}
    private = {n for n, a in accounts.items() if a.get("private_to")}
    rows, start = [], 0
    while True:
        q = db.table("commerce_bank_transactions").select(
            "txn_date,description,direction,amount_cents,balance_cents,account_code,match_status,"
            "categorized_by,reference,bank_account,project,source_file").eq("tenant_id", tenant_id)
        if account_id:
            num = next((n for n, a in accounts.items() if a["id"] == account_id), None)
            if not num:
                return []
            q = q.eq("bank_account", num)
        if since:
            q = q.gte("txn_date", since[:10])
        if until:
            q = q.lte("txn_date", until[:10])
        page = q.order("txn_date").order("id").range(start, start + 999).execute().data or []
        rows += page
        if len(page) < 1000:
            break
        start += 1000
    return [r for r in rows if r.get("bank_account") not in private
            and (r.get("source_file") or "") != "whatsapp_pop"]


def bank_lines_csv(tenant_id: str, account_id: Optional[str] = None, since: Optional[str] = None,
                   until: Optional[str] = None) -> Tuple[str, str]:
    """(every bank line as CSV — set-aside lines included and marked — , its file name)."""
    nums = own_numbers(tenant_id)
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(BANK_CSV)
    for r in bank_lines(tenant_id, account_id, since, until):
        amt = (r.get("amount_cents") or 0) / 100
        acct = r.get("bank_account") or ""
        w.writerow([r.get("txn_date"), f"…{acct[-4:]}" if acct else "",
                    mask_own(r.get("description"), nums), f"{amt:.2f}" if r.get("direction") == "in" else "",
                    f"{amt:.2f}" if r.get("direction") == "out" else "",
                    f"{r['balance_cents'] / 100:.2f}" if r.get("balance_cents") is not None else "",
                    r.get("account_code") or "",
                    "set aside" if r.get("match_status") == "ignored" else (r.get("match_status") or ""),
                    r.get("categorized_by") or "", mask_own(r.get("reference"), nums), r.get("project") or "",
                    _file(r.get("source_file"), nums)])
    return buf.getvalue(), f"bank-lines-{tenant_id}{'-' + account_id[:8] if account_id else ''}.csv"
