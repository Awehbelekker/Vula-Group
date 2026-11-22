"""vula/commerce/year_end_pack.py — one financial year, as an accountant compiles the annual financial
statements from it (22 Nov 2026; Ian: the accountants Enkardia and Company Partners have DIGG's
read-only logins, and the next step was "the year-end pack").

`build_workbook` gives one Excel file for the year (March to February by default, the business's own
year end where it set one):

  Cover                — the business, the year, where the figures come from, VAT, how far the books
                         check has got (agreed / signed off, what's still open) and the statements on file
  Trial balance        — as at year end (the one book only)
  Profit and loss      — one column per month and the year
  Balance sheet        — as at year end (the one book only)
  General ledger       — the year, line by line
  VAT                  — by month
  Aged debtors         — what customers owed at year end, by how far past due (finances.aged_as_at)
  Aged creditors       — what the business owed its suppliers at year end
  Bank reconciliation  — per account at year end
  Statements on file   — per account and month
  Open items           — what the books review still has open, as at today
  Documents            — every money document of the year, in the accountant's folders

`build_pack` gives the same workbook in a zip with the general ledger and bank lines as CSV, the
financial statements PDF (the one book only) and every document of the year in its folder.

One source per business, never mixed: a business on the one book (ledger_v2.is_on) is read from its
journal (accrual view, with a cash-view P&L beside it); any other business's P&L, VAT and ledger come
from its bank lines — what the accountant's workspace shows — and the trial balance and balance sheet
say plainly they aren't available yet, instead of showing the older cash-only journal, which holds a
fraction of the money.

Every amount in the workbook is read back from the saved file to the cent (xlsx_check.mismatches, one
sheet at a time, formulas evaluated), and the zip is reopened and checked, before anything is handed
out. Account numbers appear by their last 4 digits only. Reads only; integer cents.
"""
from __future__ import annotations

import io
import logging
import zipfile
from datetime import date, datetime, timezone
from typing import Any, Callable, Dict, List, Optional, Tuple

from openpyxl import Workbook
from openpyxl.styles import Font
from openpyxl.utils import get_column_letter

from vula.commerce import finance_folders as ff
from vula.commerce import statement_files
from vula.commerce.xlsx import _BOLD, _money, _table
from vula.commerce.xlsx_check import mismatches, safe_text

log = logging.getLogger(__name__)

NOT_ON_BOOK = ("Not available until the books are on the one book — the opening balances at the start of the "
               "year and the books check sign-off are still needed. Until then the profit and loss, VAT and "
               "general ledger in this pack are built from the bank statements.")
_MONTHS = "Jan Feb Mar Apr May Jun Jul Aug Sep Oct Nov Dec".split()
OPEN_LABELS = {
    "restore": "Bank lines set aside by mistake", "duplicates": "Bank lines in the books twice",
    "transfers": "Moves between the business's own accounts", "to_categorise": "Bank lines not yet categorised",
    "refile": "Bank lines filed by an old rule", "bill_payments": "Payments that pay a supplier's bill",
    "in_transit": "Payments with no bank line (in transit)", "staff_claims": "Staff claims not paid back",
    "drafts": "Draft invoices and bills", "odd_dates": "Entries with odd dates",
    "no_account": "Bank lines on no account number", "certificates": "Payment certificates to tie up",
    "doc_figures": "Documents whose figures aren't in the file", "not_booked": "Supplier invoices on file not in the books",
    "our_invoices": "The business's own invoices filed among bills", "sales_not_in_invoices": "Sales invoices on file not in Invoices",
    "supplier_statements": "Suppliers' statements that don't agree", "pops": "Proofs of payment to link to bills",
    "bill_mismatch": "Bills at a different amount from their document", "doc_duplicates": "Documents on file twice",
    "bill_duplicates": "Bills in the books twice", "suppliers": "Supplier records to tidy",
}


def _client():
    from vula.commerce import service
    return service._client()


def _year_end_month(tenant_id: str) -> int:
    from vula.commerce import books_reports
    try:
        m = int(books_reports.settings(tenant_id).get("year_end_month") or 2)
        return m if 1 <= m <= 12 else 2
    except (TypeError, ValueError):
        return 2


def _today() -> date:
    return date.today()


def year(tenant_id: str, year_start: Optional[str] = None, today: Optional[date] = None) -> Dict[str, Any]:
    """The financial year asked for: its first and last day, the last day there are figures for (today,
    for a year still running) and whether it has ended. ValueError for a year that hasn't started."""
    today = today or _today()
    yem = _year_end_month(tenant_id)
    start, end = ff.year_bounds(year_start or statement_files.year_start(today, yem).isoformat(), yem)
    if start > today:
        raise ValueError("that financial year hasn't started yet")
    until = min(end, today)
    return {"from": start.isoformat(), "to": end.isoformat(), "until": until.isoformat(), "ended": end < today,
            "label": f"{start.year}-{str(end.year)[2:]}"}


def _months(start: str, until: str) -> List[Tuple[str, str, str]]:
    """(first day, last day, label) of every month from the start to `until`."""
    s, u = date.fromisoformat(start), date.fromisoformat(until)
    out, y, m = [], s.year, s.month
    while (y, m) <= (u.year, u.month):
        nxt = date(y + 1, 1, 1) if m == 12 else date(y, m + 1, 1)
        last = min(date.fromordinal(nxt.toordinal() - 1), u)
        out.append((date(y, m, 1).isoformat(), last.isoformat(), f"{_MONTHS[m - 1]} {y}"))
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return out


# ── a sheet that remembers every amount it wrote ─────────────────────────────

class _Sheet:
    def __init__(self, wb: Workbook, title: str):
        self.ws = wb.create_sheet(title)
        self.title = title
        self.row = 1
        self.want: Dict[str, int] = {}

    def text(self, col: int, value: Any, bold: bool = False) -> None:
        c = self.ws.cell(self.row, col)
        safe_text(c, value if value is not None else "")
        if bold:
            c.font = _BOLD

    def money(self, col: int, cents: Optional[int], bold: bool = False) -> str:
        ref = f"{get_column_letter(col)}{self.row}"
        if cents is None:
            return ref
        _money(self.ws[ref], int(cents))
        self.want[ref] = int(cents)
        if bold:
            self.ws[ref].font = _BOLD
        return ref

    def formula(self, col: int, expr: str, cents: int, bold: bool = True) -> str:
        """A formula cell, read back against what the books say it must come to."""
        ref = f"{get_column_letter(col)}{self.row}"
        c = self.ws[ref]
        c.value = expr
        c.number_format = 'R #,##0.00;[Red]-R #,##0.00'
        if bold:
            c.font = _BOLD
        self.want[ref] = int(cents)
        return ref

    def heading(self, text: str, sub: Optional[str] = None) -> None:
        safe_text(self.ws.cell(self.row, 1), text)
        self.ws.cell(self.row, 1).font = Font(bold=True, size=13)
        self.row += 1
        if sub:
            self.text(1, sub)
            self.row += 1
        self.row += 1

    def note(self, text: str) -> None:
        self.text(1, text)
        self.row += 1

    def header(self, headers: List[str], widths: List[int]) -> int:
        _table(self.ws, self.row, headers, widths)
        self.row += 1
        return self.row

    def sum_col(self, col: int, first: int, last: int, cents: int) -> str:
        letter = get_column_letter(col)
        return self.formula(col, f"=SUM({letter}{first}:{letter}{last})" if last >= first else "=0", cents)


def _sub(y: Dict[str, Any], what: str) -> str:
    tail = "" if y["ended"] else f" — the year hasn't ended: figures to {y['until']}"
    return f"{what}{tail}"


# ── the sheets ───────────────────────────────────────────────────────────────

def _cover(wb, ctx) -> _Sheet:
    sh = _Sheet(wb, "Cover")
    y = ctx["year"]
    sh.heading(f"{ctx['business'] or 'Business'} — year-end pack {y['label']}",
               f"Financial year {y['from']} to {y['to']}" + ("" if y["ended"] else f" (not ended: figures to {y['until']})"))
    rows: List[Tuple[str, Any]] = [
        ("Where the figures come from", "The one book (journal), accrual view" if ctx["one_book"]
         else "The bank statements (the books are not on the one book yet)"),
        ("VAT registered", "Yes" if ctx["vat_registered"] else "No"),
    ]
    st = ctx["settings"]
    if ctx["one_book"]:
        rows += [("Accrual from", st.get("accrual_from") or ""),
                 ("VAT basis", "Payments basis" if (st.get("vat_basis") or "invoice") == "payments" else "Invoice basis"),
                 ("Books locked up to", st.get("lock_date") or "not locked")]
    audit = ctx.get("audit")
    if audit:
        rows += [("Books check last run", str(audit.get("created_at") or "")[:16].replace("T", " ")),
                 ("Agreed by the business", f"{audit.get('agreed_by')} on {str(audit.get('agreed_at'))[:10]}"
                  if audit.get("agreed_by") else "Not yet"),
                 ("The business's note", audit.get("agreed_note") or ""),
                 ("Signed off by Vula", f"{audit.get('signed_off_by')} on {str(audit.get('signed_off_at'))[:10]}"
                  if audit.get("signed_off_by") else "Not yet")]
    else:
        rows.append(("Books check", "Not run yet"))
    rows += [("Generated", datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")),
             ("Generated by", ctx.get("viewer") or "")]
    for label, value in rows:
        sh.text(1, label, bold=True)
        sh.text(2, value)
        sh.row += 1
    sh.row += 1
    open_now = {k: v for k, v in (ctx.get("open") or {}).items() if v}
    sh.text(1, "Still open in the books review (as at today)", bold=True)
    sh.row += 1
    if not ctx.get("audit"):
        sh.note("Run the books check (Money › Books review) to list what's still open.")
    elif not open_now:
        sh.note("Nothing open.")
    for k, v in open_now.items():
        sh.text(1, OPEN_LABELS.get(k, k.replace("_", " ").capitalize()))
        sh.ws.cell(sh.row, 2, int(v))
        sh.row += 1
    sh.row += 1
    sh.text(1, "Bank statements not on file", bold=True)
    sh.row += 1
    accounts = (ctx.get("coverage") or {}).get("accounts") or []
    if not accounts:
        sh.note("No bank accounts on file.")
    for a in accounts:
        sh.text(1, a["account"])
        sh.text(2, ", ".join(a["missing"]) if a["missing"] else "Every month on file")
        sh.row += 1
    no_acc = (ctx.get("coverage") or {}).get("lines_on_no_account") or 0
    if no_acc:
        sh.text(1, "Bank lines on no account number")
        sh.ws.cell(sh.row, 2, int(no_acc))
        sh.row += 1
    sh.ws.column_dimensions["A"].width = 44
    sh.ws.column_dimensions["B"].width = 70
    return sh


def _trial_balance(wb, ctx) -> _Sheet:
    from vula.commerce import books_reports
    sh = _Sheet(wb, "Trial balance")
    y = ctx["year"]
    sh.heading("Trial balance", _sub(y, f"As at {y['until']}, accrual view"))
    if not ctx["one_book"]:
        sh.note(NOT_ON_BOOK)
        return sh
    tb = books_reports.trial_balance(ctx["tenant_id"], None, y["until"], "accrual")
    first = sh.header(["Code", "Account", "Type", "Debit (R)", "Credit (R)"], [18, 40, 12, 18, 18])
    dr_total = cr_total = 0
    for a in tb["accounts"]:
        net = int(a["debit_cents"]) - int(a["credit_cents"])
        if not net:
            continue
        sh.text(1, a["code"])
        sh.text(2, a["name"])
        sh.text(3, a["type"])
        if net > 0:
            sh.money(4, net)
            dr_total += net
        else:
            sh.money(5, -net)
            cr_total += -net
        sh.row += 1
    last = sh.row - 1
    sh.text(2, "Total", bold=True)
    d = sh.sum_col(4, first, last, dr_total)
    c = sh.sum_col(5, first, last, cr_total)
    sh.row += 1
    sh.text(2, "Difference (must be 0)", bold=True)
    sh.formula(4, f"={d}-{c}", dr_total - cr_total)
    ctx["checks"]["trial_balance_balances"] = dr_total == cr_total
    return sh


def _pnl_block(sh: _Sheet, ctx, basis: Optional[str], label: str) -> Dict[str, int]:
    """One P&L table, a column per month and the year: rows per account, totals as formulas."""
    from vula.commerce import accounting
    tid, months = ctx["tenant_id"], ctx["months"]
    per_month = [accounting.pnl(tid, a, b, basis) for a, b, _l in months]
    year_pnl = accounting.pnl(tid, ctx["year"]["from"], ctx["year"]["until"], basis)
    sh.text(1, label, bold=True)
    sh.row += 1
    ncol = len(months) + 2
    sh.header(["Account"] + [m[2] for m in months] + ["Year"], [36] + [14] * len(months) + [16])
    totals = {}
    for kind, title in (("income", "Income"), ("expenses", "Expenses")):
        names: List[str] = []
        for p in per_month + [year_pnl]:
            for r in p[kind]:
                if r["account"] not in names:
                    names.append(r["account"])
        sh.text(1, title, bold=True)
        sh.row += 1
        first = sh.row
        col_totals = [0] * len(months)
        for name in names:
            sh.text(1, name)
            row_total = 0
            for i, p in enumerate(per_month):
                cents = sum(int(r["amount_cents"]) for r in p[kind] if r["account"] == name)
                if cents:
                    sh.money(2 + i, cents)
                col_totals[i] += cents
                row_total += cents
            year_cents = sum(int(r["amount_cents"]) for r in year_pnl[kind] if r["account"] == name)
            if year_cents != row_total:
                raise RuntimeError(f"the months of {name} don't add up to the year")
            sh.formula(ncol, f"=SUM(B{sh.row}:{get_column_letter(ncol - 1)}{sh.row})", row_total, bold=False)
            sh.row += 1
        last = sh.row - 1
        sh.text(1, f"Total {title.lower()}", bold=True)
        for i in range(len(months)):
            sh.sum_col(2 + i, first, last, col_totals[i])
        key = "total_income_cents" if kind == "income" else "total_expense_cents"
        sh.sum_col(ncol, first, last, int(year_pnl[key]))
        totals[kind] = (sh.row, col_totals, int(year_pnl[key]))
        sh.row += 2
    sh.text(1, "Net profit (loss)", bold=True)
    (ir, ic, iy), (er, ec, ey) = totals["income"], totals["expenses"]
    for i in range(len(months)):
        L = get_column_letter(2 + i)
        sh.formula(2 + i, f"={L}{ir}-{L}{er}", ic[i] - ec[i])
    L = get_column_letter(ncol)
    sh.formula(ncol, f"={L}{ir}-{L}{er}", iy - ey)
    if iy - ey != int(year_pnl["net_profit_cents"]):
        raise RuntimeError("the profit doesn't add up")
    sh.row += 2
    return {"income": iy, "expenses": ey, "net": iy - ey}


def _pnl(wb, ctx) -> _Sheet:
    sh = _Sheet(wb, "Profit and loss")
    y = ctx["year"]
    if ctx["one_book"]:
        sh.heading("Profit and loss", _sub(y, f"{y['from']} to {y['to']}, from the books"))
        ctx["figures"]["pnl"] = _pnl_block(sh, ctx, "accrual", "Accrual view (sales when invoiced, costs when billed)")
        ctx["figures"]["pnl_cash"] = _pnl_block(sh, ctx, "cash", "Cash view (as the money moved)")
    else:
        sh.heading("Profit and loss", _sub(y, f"{y['from']} to {y['to']}, from the bank statements"))
        ctx["figures"]["pnl"] = _pnl_block(sh, ctx, None, "From the bank lines, by category")
    return sh


def _balance_sheet(wb, ctx) -> _Sheet:
    from vula.commerce import reports
    sh = _Sheet(wb, "Balance sheet")
    y = ctx["year"]
    sh.heading("Balance sheet", _sub(y, f"As at {y['until']}, accrual view"))
    if not ctx["one_book"]:
        sh.note(NOT_ON_BOOK)
        return sh
    b = reports.balance_sheet(ctx["tenant_id"], y["until"], "accrual")
    sh.header(["Account", "Amount (R)"], [44, 18])
    refs = {}
    for key, title, total_key in (("assets", "Assets", "total_assets_cents"),
                                  ("liabilities", "Liabilities", "total_liabilities_cents"),
                                  ("equity", "Equity", "total_equity_cents")):
        sh.text(1, title, bold=True)
        sh.row += 1
        first = sh.row
        for r in b[key]:
            sh.text(1, r["name"])
            sh.money(2, r["cents"])
            sh.row += 1
        if key == "equity":
            sh.text(1, "Profit kept in the business (retained earnings)")
            sh.money(2, b["retained_earnings_cents"])
            sh.row += 1
        sh.text(1, f"Total {title.lower()}", bold=True)
        refs[key] = sh.sum_col(2, first, sh.row - 1, int(b[total_key]))
        sh.row += 2
    sh.text(1, "Assets less liabilities and equity (must be 0)", bold=True)
    sh.formula(2, f"={refs['assets']}-{refs['liabilities']}-{refs['equity']}",
               int(b["total_assets_cents"]) - int(b["total_liabilities_cents"]) - int(b["total_equity_cents"]))
    ctx["checks"]["balance_sheet_balances"] = bool(b.get("balanced"))
    return sh


def _ledger(wb, ctx) -> _Sheet:
    from vula.commerce import accounting
    from vula.commerce.exports import mask_own, own_numbers
    sh = _Sheet(wb, "General ledger")
    y = ctx["year"]
    nums = own_numbers(ctx["tenant_id"])
    rows = accounting.general_ledger(ctx["tenant_id"], y["from"], y["until"], "accrual" if ctx["one_book"] else None)
    if ctx["one_book"]:
        sh.heading("General ledger", _sub(y, "Every journal line of the year, accrual view (a line in both views says 'both')"))
        first = sh.header(["Date", "Entry", "Description", "Account code", "Account", "Debit (R)", "Credit (R)",
                           "Source", "Document", "Bank account", "View"], [12, 14, 44, 18, 30, 16, 16, 18, 14, 18, 10])
        dr = cr = 0
        for r in rows:
            for col, v in enumerate((r["date"], f"{r['entry_id']} v{r['version']}", mask_own(r["description"], nums),
                                     r["account_code"], r["account"]), 1):
                sh.text(col, v)
            sh.money(6, r["debit_cents"])
            sh.money(7, r["credit_cents"])
            for col, v in ((8, r.get("source_type")), (9, r.get("doc_id")), (10, r.get("bank_account")),
                           (11, r.get("view"))):
                sh.text(col, v or "")
            dr += r["debit_cents"]
            cr += r["credit_cents"]
            sh.row += 1
        last = sh.row - 1
        sh.text(5, "Total", bold=True)
        sh.sum_col(6, first, last, dr)
        sh.sum_col(7, first, last, cr)
    else:
        sh.heading("General ledger", _sub(y, "Every bank line of the year in the books, by category"))
        first = sh.header(["Date", "Description", "Account", "Money in (R)", "Money out (R)", "VAT (R)", "Reference",
                           "Statement file"], [12, 50, 30, 16, 16, 14, 24, 30])
        tin = tout = tvat = 0
        for r in rows:
            sh.text(1, r.get("date"))
            sh.text(2, mask_own(r.get("description"), nums))
            sh.text(3, r.get("account") or "")
            amt = int(r.get("amount_cents") or 0)
            if r.get("direction") == "in":
                sh.money(4, amt)
                tin += amt
            else:
                sh.money(5, amt)
                tout += amt
            vat = int(r.get("vat_cents") or 0)
            if vat:
                sh.money(6, vat)
                tvat += vat
            sh.text(7, mask_own(r.get("reference"), nums))
            sh.text(8, statement_files.mask_name(mask_own(r.get("source"), nums)))
            sh.row += 1
        last = sh.row - 1
        sh.text(3, "Total", bold=True)
        sh.sum_col(4, first, last, tin)
        sh.sum_col(5, first, last, tout)
        sh.sum_col(6, first, last, tvat)
    return sh


def _vat(wb, ctx) -> _Sheet:
    from vula.commerce import accounting
    sh = _Sheet(wb, "VAT")
    y = ctx["year"]
    tid = ctx["tenant_id"]
    sh.heading("VAT", _sub(y, f"{y['from']} to {y['to']}"))
    if not ctx["vat_registered"]:
        sh.note("The business is not set as VAT registered in Vula, so no VAT is worked out. If it registers, "
                "the date it was registered from decides which of this year's sales carry VAT.")
        return sh
    year_vat = accounting.vat_return(tid, y["from"], y["until"])
    if ctx["one_book"]:
        months = {m["month"]: m for m in year_vat.get("by_month") or []}
        per = [(label, months.get(a[:7], {}).get("output_cents", 0), months.get(a[:7], {}).get("input_cents", 0))
               for a, _b, label in ctx["months"]]
        sh.note(year_vat.get("note") or "")
    else:
        per = []
        for a, b, label in ctx["months"]:
            v = accounting.vat_return(tid, a, b)
            per.append((label, int(v.get("output_vat_cents") or 0), int(v.get("input_vat_cents") or 0)))
        sh.note("From the bank lines (cash basis). The VAT return itself is the accountant's to prepare.")
    sh.row += 1
    first = sh.header(["Month", "VAT on sales (R)", "VAT on costs (R)", "Net to SARS (R)"], [16, 18, 18, 18])
    to = ti = 0
    for label, out, inp in per:
        sh.text(1, label)
        sh.money(2, out)
        sh.money(3, inp)
        sh.formula(4, f"=B{sh.row}-C{sh.row}", out - inp, bold=False)
        to += out
        ti += inp
        sh.row += 1
    last = sh.row - 1
    sh.text(1, "Year", bold=True)
    sh.sum_col(2, first, last, int(year_vat.get("output_vat_cents") or 0))
    sh.sum_col(3, first, last, int(year_vat.get("input_vat_cents") or 0))
    sh.sum_col(4, first, last, int(year_vat.get("output_vat_cents") or 0) - int(year_vat.get("input_vat_cents") or 0))
    if (to, ti) != (int(year_vat.get("output_vat_cents") or 0), int(year_vat.get("input_vat_cents") or 0)):
        raise RuntimeError("the months' VAT doesn't add up to the year")
    return sh


def _aged(wb, ctx, direction: str) -> _Sheet:
    from vula.commerce import finances
    title = "Aged debtors" if direction == "outbound" else "Aged creditors"
    sh = _Sheet(wb, title)
    y = ctx["year"]
    a = finances.aged_as_at(ctx["tenant_id"], y["until"], direction)
    who = "customers owed the business" if direction == "outbound" else "the business owed its suppliers"
    sh.heading(title, _sub(y, f"What {who} at {y['until']}, by how far past due"))
    buckets = [("current", "Not yet due"), ("days_1_30", "1–30 days"), ("days_31_60", "31–60 days"),
               ("days_61_90", "61–90 days"), ("days_90_plus", "Over 90 days")]
    first = sh.header(["Past due", "Owed (R)"], [28, 18])
    for k, label in buckets:
        sh.text(1, label)
        sh.money(2, a["aging"][k])
        sh.row += 1
    sh.text(1, "Total", bold=True)
    sh.sum_col(2, first, sh.row - 1, a["total_cents"])
    sh.row += 1
    if "control_cents" in a:
        sh.text(1, "In the books (control account)")
        sh.money(2, a["control_cents"])
        sh.row += 1
        sh.text(1, "Ties to the documents" if a["ties"] else "Does NOT tie to the documents — see the books review",
                bold=True)
        sh.row += 1
    sh.row += 1
    name = "Customer" if direction == "outbound" else "Supplier"
    first = sh.header(["Number", name, "Issued", "Due", "Days past due", "Total (R)", "Owed (R)"],
                      [20, 36, 12, 12, 14, 16, 16])
    for it in a["items"]:
        sh.text(1, it["number"])
        sh.text(2, it["name"])
        sh.text(3, it["issue_date"] or "")
        sh.text(4, it["due_date"] or "")
        if it["days_over"] is not None:
            sh.ws.cell(sh.row, 5, it["days_over"])
        sh.money(6, it["total_cents"])
        sh.money(7, it["owed_cents"])
        sh.row += 1
    sh.text(2, "Total", bold=True)
    sh.sum_col(7, first, sh.row - 1, a["total_cents"])
    ctx["figures"][direction] = a["total_cents"]
    return sh


def _bank_rows(tenant_id: str) -> List[Dict[str, Any]]:
    from vula.commerce.ledger import _all_pages
    db = _client()
    private = {a.get("account_number") for a in (db.table("commerce_bank_accounts")
               .select("account_number,private_to").eq("tenant_id", tenant_id).execute().data or [])
               if a.get("private_to")}
    rows = _all_pages(lambda: db.table("commerce_bank_transactions")
                      .select("id,txn_date,direction,amount_cents,balance_cents,match_status,bank_account,source_file")
                      .eq("tenant_id", tenant_id).order("id"))
    return [r for r in rows if not (r.get("bank_account") and r.get("bank_account") in private)]


def _recon(wb, ctx) -> _Sheet:
    from vula.commerce import bank_recon, books_reports, books_rebuild
    sh = _Sheet(wb, "Bank reconciliation")
    y = ctx["year"]
    tid = ctx["tenant_id"]
    rows = _bank_rows(tid)
    try:
        ids = {a["account_number"]: str(a["id"]) for a in (_client().table("commerce_bank_accounts")
               .select("id,account_number").eq("tenant_id", tid).execute().data or []) if a.get("account_number")}
    except Exception:  # noqa: BLE001
        ids = {}
    labels = books_reports.bank_labels(tid)
    if ctx["one_book"]:
        live, _every = books_rebuild.live_journal(tid)
        rec = bank_recon.as_at(rows, live, y["until"], ids)
        sh.heading("Bank reconciliation", _sub(y, f"At {y['until']}: the last statement balance against bank in the books"))
        sh.header(["Account", "Lines", "From", "Until", "Statement balance (R)", "In the books (R)",
                           "Difference (R)"], [30, 10, 12, 12, 20, 20, 18])
        for a in rec["accounts"]:
            sh.text(1, labels.get(str(a.get("bank_account_id")), a["account"]) if a.get("bank_account_id") else a["account"])
            sh.ws.cell(sh.row, 2, a["lines"])
            sh.text(3, a["from"])
            sh.text(4, a["until"])
            s = sh.money(5, a["statement_cents"])
            b = sh.money(6, a["books_cents"])
            if a["difference_cents"] is not None:
                sh.formula(7, f"={b}-{s}", a["difference_cents"])
            sh.row += 1
    else:
        rec = bank_recon.statement_check(rows, y["from"], y["until"])
        sh.heading("Bank reconciliation", _sub(y, f"{y['from']} to {y['until']}: do the statement lines in the books add up?"))
        sh.note("Opening balance + money in − money out must equal the last statement balance; a break is a line "
                "whose balance isn't the one before it plus its own amount (a line missing, set aside by mistake or misread).")
        sh.row += 1
        sh.header(["Account", "Lines", "From", "Until", "Opening (R)", "Money in (R)", "Money out (R)",
                           "Worked out (R)", "Statement balance (R)", "Difference (R)", "Breaks"],
                          [30, 10, 12, 12, 16, 16, 16, 16, 20, 16, 10])
        for a in rec["accounts"]:
            num = a["number"]
            sh.text(1, labels.get(ids.get(num, ""), a["account"]) if ids.get(num) else a["account"])
            sh.ws.cell(sh.row, 2, a["lines"])
            sh.text(3, a["from"])
            sh.text(4, a["until"])
            o = sh.money(5, a["opening_cents"])
            i = sh.money(6, a["in_cents"])
            out = sh.money(7, a["out_cents"])
            if a["books_cents"] is not None:
                w = sh.formula(8, f"={o}+{i}-{out}", a["books_cents"], bold=False)
                s = sh.money(9, a["statement_cents"])
                if a["difference_cents"] is not None:
                    sh.formula(10, f"={w}-{s}", a["difference_cents"])
            if a["breaks"] is not None:
                sh.ws.cell(sh.row, 11, a["breaks"])
            sh.row += 1
    if not rec["accounts"]:
        sh.note("No bank lines on an account number for this year.")
    un = rec.get("unassigned") or {}
    if un.get("lines"):
        sh.row += 1
        sh.text(1, f"{un['lines']} bank lines are on no account number, so they can't be reconciled per account "
                   "(the official statements tag them):", bold=True)
        sh.row += 1
        sh.text(1, "Money in")
        sh.money(2, un["in_cents"])
        sh.row += 1
        sh.text(1, "Money out")
        sh.money(2, un["out_cents"])
        sh.row += 1
    ctx["checks"]["bank_reconciles"] = bool(rec.get("all_reconcile"))
    return sh


def _coverage(wb, ctx) -> _Sheet:
    sh = _Sheet(wb, "Statements on file")
    y = ctx["year"]
    sh.heading("Bank statements on file", f"Per account and month, {y['from']} to {y['until']}")
    cov = ctx.get("coverage") or {}
    sh.header(["Account", "Month", "Statement on file", "Official", "Bank lines"], [30, 12, 18, 10, 12])
    for a in cov.get("accounts") or []:
        for m in a["months"]:
            sh.text(1, a["account"])
            sh.text(2, m["month"])
            sh.text(3, "Yes" if m["statement"] else "MISSING")
            sh.text(4, "Yes" if m["official"] else "")
            sh.ws.cell(sh.row, 5, m["lines"])
            sh.row += 1
    return sh


def _open_items(wb, ctx) -> _Sheet:
    sh = _Sheet(wb, "Open items")
    sh.heading("Still open in the books review", "As at today, not the year end — each is fixed on the Books review screen")
    rev = ctx.get("review") or {}
    if not rev.get("report"):
        sh.note("The books check hasn't been run yet (Money › Books review › Run the check).")
        return sh
    sh.text(1, "Payments with no bank line (in transit)", bold=True)
    sh.row += 1
    first = sh.header(["Kind", "Document", "Date", "Still open (R)"], [12, 50, 12, 18])
    total = 0
    for d in rev.get("in_transit") or []:
        sh.text(1, d.get("kind"))
        sh.text(2, d.get("label") or "")
        sh.text(3, d.get("date") or "")
        sh.money(4, int(d.get("open_cents") or 0))
        total += int(d.get("open_cents") or 0)
        sh.row += 1
    sh.text(2, "Total", bold=True)
    sh.sum_col(4, first, sh.row - 1, total)
    sh.row += 2
    sh.text(1, "Staff claims not paid back", bold=True)
    sh.row += 1
    first = sh.header(["Date", "What", "Paid by", "Amount (R)"], [12, 50, 24, 18])
    total = 0
    for c in rev.get("staff_claims") or []:
        sh.text(1, str(c.get("date") or "")[:10])
        sh.text(2, c.get("description") or c.get("supplier") or "")
        sh.text(3, c.get("paid_by_name") or "")
        sh.money(4, int(c.get("amount_cents") or 0))
        total += int(c.get("amount_cents") or 0)
        sh.row += 1
    sh.text(2, "Total", bold=True)
    sh.sum_col(4, first, sh.row - 1, total)
    sh.row += 2
    sh.text(1, "Draft invoices and bills (not in the books)", bold=True)
    sh.row += 1
    first = sh.header(["Number", "Sale or bill", "Who", "Date", "Total (R)"], [20, 12, 36, 12, 18])
    total = 0
    for d in rev.get("drafts") or []:
        sh.text(1, d.get("invoice_number") or "")
        sh.text(2, "Bill" if d.get("direction") == "inbound" else "Sale")
        sh.text(3, d.get("supplier") or d.get("customer_name") or "")
        sh.text(4, str(d.get("issue_date") or "")[:10])
        sh.money(5, int(d.get("total_cents") or 0))
        total += int(d.get("total_cents") or 0)
        sh.row += 1
    sh.text(3, "Total", bold=True)
    sh.sum_col(5, first, sh.row - 1, total)
    return sh


def _documents_sheet(wb, ctx, rows: List[Dict[str, Any]], in_zip: bool) -> _Sheet:
    sh = _Sheet(wb, "Documents")
    y = ctx["year"]
    sh.heading("The year's documents", f"Every money document dated {y['from']} to {y['to']}, in the accountant's folders"
               + (" (the files are in this zip)" if in_zip else " (download the pack with documents for the files)"))
    heads = ["Folder", "Group", "Date", "Document", "Amount (R)"] + (["File in this zip"] if in_zip else [])
    first = sh.header(heads, [34, 30, 12, 50, 16] + ([60] if in_zip else []))
    total = 0
    for r in rows:
        for col, v in enumerate((r["folder"], r["group"], r["date"], r["label"]), 1):
            sh.text(col, v)
        if r["amount_cents"] is not None:
            sh.money(5, r["amount_cents"])
            total += int(r["amount_cents"])
        if in_zip:
            sh.text(6, r["path"] or "(file could not be fetched)")
        sh.row += 1
    sh.text(4, "Total of the amounts above", bold=True)
    sh.sum_col(5, first, sh.row - 1, total)
    return sh


# ── putting it together ──────────────────────────────────────────────────────

def _context(tenant_id: str, year_start: Optional[str], viewer: Optional[str]) -> Dict[str, Any]:
    from vula.commerce import accounting, books_reports, books_review, ledger_v2
    y = year(tenant_id, year_start)
    ctx: Dict[str, Any] = {"tenant_id": tenant_id, "year": y, "viewer": viewer, "business": ff._business(tenant_id),
                           "one_book": ledger_v2.is_on(tenant_id), "vat_registered": accounting.is_vat_registered(tenant_id),
                           "settings": books_reports.settings(tenant_id), "months": _months(y["from"], y["until"]),
                           "checks": {}, "figures": {}}
    try:
        ctx["audit"] = books_review.latest(tenant_id)
    except Exception:  # noqa: BLE001
        ctx["audit"] = None
    try:
        ctx["review"] = books_review.review(tenant_id) if ctx["audit"] else {}
    except Exception as exc:  # noqa: BLE001 — the pack still comes; the open items say they couldn't be read
        log.warning("year-end pack: books review not read for %s: %s", tenant_id, exc)
        ctx["review"] = {}
    ctx["open"] = (ctx["review"] or {}).get("open") or {}
    ctx["coverage"] = statement_files.coverage(tenant_id, date.fromisoformat(y["from"]), date.fromisoformat(y["until"]))
    return ctx


def _workbook(ctx: Dict[str, Any], doc_rows: List[Dict[str, Any]], in_zip: bool) -> Tuple[bytes, Dict[str, Dict[str, int]]]:
    wb = Workbook()
    wb.remove(wb.active)
    sheets = [_cover(wb, ctx), _trial_balance(wb, ctx), _pnl(wb, ctx), _balance_sheet(wb, ctx), _ledger(wb, ctx),
              _vat(wb, ctx), _aged(wb, ctx, "outbound"), _aged(wb, ctx, "inbound"), _recon(wb, ctx),
              _coverage(wb, ctx), _open_items(wb, ctx), _documents_sheet(wb, ctx, doc_rows, in_zip)]
    buf = io.BytesIO()
    wb.save(buf)
    data = buf.getvalue()
    want = {s.title: s.want for s in sheets}
    for title, cells in want.items():                 # read back: every amount, every total, to the cent
        bad = mismatches(data, cells, sheet=title)
        if bad:
            raise RuntimeError(f"the {title} sheet didn't read back: {bad[:3]}")
    return data, want


def _root(ctx: Dict[str, Any]) -> str:
    from vula.commerce import project_file as pf
    return pf._clean(f"{ctx['business'] or 'Business'} - year-end pack {ctx['year']['label']}", 80)


def build_workbook(tenant_id: str, year_start: Optional[str] = None,
                   viewer: Optional[str] = None) -> Tuple[bytes, Dict[str, Any]]:
    """The year-end workbook alone. ValueError for a year that hasn't started; RuntimeError when it
    doesn't read back."""
    ctx = _context(tenant_id, year_start, viewer)
    tree = ff.organise(tenant_id, ctx["year"]["from"])
    rows = ff.document_rows(tree, _root(ctx))
    data, _want = _workbook(ctx, rows, in_zip=False)
    return data, _manifest(ctx, data, documents=len(rows))


def build_pack(tenant_id: str, year_start: Optional[str] = None, viewer: Optional[str] = None,
               fetch: Optional[Callable[[str], bytes]] = None) -> Tuple[bytes, Dict[str, Any]]:
    """The workbook, the ledger and bank lines as CSV, the statements PDF (the one book only) and every
    document of the year, in one zip — read back before it's handed out."""
    from vula.commerce import exports
    ctx = _context(tenant_id, year_start, viewer)
    y = ctx["year"]
    root = _root(ctx)
    tree = ff.organise(tenant_id, y["from"])
    rows = ff.document_rows(tree, f"{root}/Documents")
    got, failed = ff.fetch_files(rows, fetch)
    book, _want = _workbook(ctx, rows, in_zip=True)
    files: Dict[str, bytes] = {f"{root}/Year-end workbook.xlsx": book}
    ledger, _n = exports.ledger_csv(tenant_id, y["from"], y["until"], "accrual" if ctx["one_book"] else None)
    files[f"{root}/General ledger.csv"] = ledger.encode("utf-8")
    bank, _n = exports.bank_lines_csv(tenant_id, None, y["from"], y["until"])
    files[f"{root}/Bank lines.csv"] = bank.encode("utf-8")
    if ctx["one_book"]:
        try:
            from vula.commerce import reports
            d = reports.build_all(tenant_id, y["from"], y["until"], "accrual")
            files[f"{root}/Financial statements.pdf"] = reports.render_pdf(ctx["business"] or tenant_id, d)
        except Exception as exc:  # noqa: BLE001 — the workbook carries the same figures
            log.warning("year-end pack: statements PDF not built for %s: %s", tenant_id, exc)
    for r, data, _e in got:
        if data is not None:
            files[r["path"]] = data
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for path, data in files.items():
            z.writestr(path, data)
    out = buf.getvalue()
    if len(out) > ff.MAX_ZIP_BYTES:
        raise RuntimeError("the pack is too big to download in one go — download the workbook and the folders apart")
    with zipfile.ZipFile(io.BytesIO(out)) as z:        # read back: every file there and intact
        if set(z.namelist()) != set(files) or z.testzip() is not None:
            raise RuntimeError("the pack didn't read back complete")
    m = _manifest(ctx, out, documents=len(rows))
    m.update({"files": len(files), "files_failed": [statement_files.mask_name(p) for p in failed]})
    return out, m


def _manifest(ctx: Dict[str, Any], data: bytes, documents: int) -> Dict[str, Any]:
    y = ctx["year"]
    return {"from": y["from"], "to": y["to"], "until": y["until"], "ended": y["ended"], "label": y["label"],
            "source": "journal" if ctx["one_book"] else "bank", "documents": documents, "bytes": len(data),
            "checks": ctx["checks"], "files_failed": []}


def filename(manifest: Dict[str, Any], kind: str) -> str:
    return f"Year-end_pack_{manifest['label']}.{kind}"
