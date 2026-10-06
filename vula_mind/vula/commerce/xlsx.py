"""vula/commerce/xlsx.py

Renders filed supplier invoices as a real .xlsx workbook, so an explicit "in excel" /
"spreadsheet" request is answered with a file an accountant can work with, not only text.
Pure functions returning bytes (the vula/commerce/pdf.py::render_invoice_pdf shape), styled like
vula/takeoff/boq_export.py.

2026-09-27 rebuild, after the workbook Ian received on 2026-09-26: its title was his question,
the "Document" column was raw filenames and a WhatsApp media id, dates were text, there was no
invoice number, supplier, VAT or line items, and no Materials sheet even though materials were
asked for. The layout is now:

  Summary     who/what period, invoice count, refunds, excl-VAT / VAT / total, documents with no
              amount on file; for an all-suppliers export, spend per supplier (biggest first)
  Invoices    one row per document: date, invoice no., supplier, type, excl VAT, VAT, total
  Line items  every extracted line: date, invoice no., supplier, description, qty, unit, total
  Materials   the same item merged across invoices (quantity, spend, invoices, avg unit price)

Money is integer cents until it is written; refunds are negative so every SUM row equals the
server-computed total the text reply quotes.
"""
from __future__ import annotations

import io
from datetime import date
from typing import Any, Dict, Iterable, List, Optional

import openpyxl
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

_HEADER_FILL = PatternFill("solid", fgColor="FF2C5545")
# The business's brand colour for header rows (2026-09-29) — per call, so two tenants' exports
# rendering at once never share one. Unset → Vula green.
from contextvars import ContextVar
_fill_var: ContextVar = ContextVar("xlsx_header_fill", default=None)


def _fill_for(accent: Optional[str]):
    import re as _re
    a = (accent or "").lstrip("#")
    if _re.fullmatch(r"[0-9a-fA-F]{6}", a):
        r, g, b = (int(a[i:i + 2], 16) for i in (0, 2, 4))
        if (0.299 * r + 0.587 * g + 0.114 * b) / 255 <= 0.62:   # white header text must read
            return PatternFill("solid", fgColor="FF" + a.upper())
    return None
_HEADER_FONT = Font(bold=True, color="FFFFFFFF")
_BOLD = Font(bold=True)
_MUTED = Font(italic=True, color="FF6B6B6B")
_MONEY_FMT = 'R #,##0.00;[Red]-R #,##0.00'
_DATE_FMT = "yyyy-mm-dd"


def _rand(cents: Optional[int]) -> Optional[float]:
    return None if cents is None else round(int(cents) / 100, 2)


def _as_date(s: str) -> Any:
    try:
        return date.fromisoformat((s or "")[:10])
    except ValueError:
        return s or None


def _table(ws: Any, header_row: int, headers: List[str], widths: List[int]) -> None:
    for col, text in enumerate(headers, start=1):
        cell = ws.cell(row=header_row, column=col, value=text)
        cell.font = _HEADER_FONT
        cell.fill = _fill_var.get() or _HEADER_FILL
        cell.alignment = Alignment(horizontal="left", vertical="center")
    for col, width in enumerate(widths, start=1):
        ws.column_dimensions[get_column_letter(col)].width = width
    ws.freeze_panes = ws.cell(row=header_row + 1, column=1)


def _finish_table(ws: Any, header_row: int, last_row: int, ncols: int) -> None:
    if last_row > header_row:
        ws.auto_filter.ref = f"A{header_row}:{get_column_letter(ncols)}{last_row}"


def _money(cell: Any, cents: Optional[int]) -> None:
    cell.value = _rand(cents)
    cell.number_format = _MONEY_FMT


def _sum_row(ws: Any, row: int, label_col: int, money_cols: Iterable[int], first: int, last: int) -> None:
    ws.cell(row=row, column=label_col, value="Total").font = _BOLD
    for col in money_cols:
        letter = get_column_letter(col)
        cell = ws.cell(row=row, column=col, value=f"=SUM({letter}{first}:{letter}{last})" if last >= first else 0)
        cell.number_format = _MONEY_FMT
        cell.font = _BOLD


def render_invoices_xlsx(rows: List[Dict[str, Any]], title: str, *,
                         materials: Optional[List[Dict[str, Any]]] = None,
                         by_supplier: bool = False, accent: Optional[str] = None) -> Optional[bytes]:
    token = _fill_var.set(_fill_for(accent))
    try:
        return _render_invoices_xlsx(rows, title, materials=materials, by_supplier=by_supplier)
    finally:
        _fill_var.reset(token)


def _render_invoices_xlsx(rows: List[Dict[str, Any]], title: str, *,
                          materials: Optional[List[Dict[str, Any]]] = None,
                          by_supplier: bool = False) -> Optional[bytes]:
    """`rows` are service._export_row dicts (date, ref, party, is_refund, total_cents,
    vat_cents, lines, ...). `title` is the supplier's name or "All suppliers" — never the
    owner's question. `by_supplier` adds a spend-per-supplier table to the Summary sheet.
    None when there is nothing to export."""
    if not rows:
        return None
    rows = sorted(rows, key=lambda r: (r.get("date") or "", r.get("ref") or ""), reverse=True)
    priced = [r for r in rows if r.get("total_cents") is not None]
    total = sum(r["total_cents"] for r in priced)
    vat = sum(r.get("vat_cents") or 0 for r in priced)
    refunds = [r for r in priced if r.get("is_refund")]
    dates = sorted(r["date"] for r in rows if r.get("date"))

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Summary"
    ws.cell(row=1, column=1, value=title).font = Font(bold=True, size=14)
    facts = [
        ("Period", f"{dates[0]} to {dates[-1]}" if dates else "—"),
        ("Documents", len(rows)),
        ("Documents with an amount", len(priced)),
        ("Refunds / credit notes", len(refunds)),
        ("Refunded", _rand(-sum(r["total_cents"] for r in refunds)) if refunds else 0),
        ("Total excl. VAT", _rand(total - vat)),
        ("VAT", _rand(vat)),
        ("Total spend (incl. VAT, after refunds)", _rand(total)),
        ("Generated", date.today().isoformat()),
    ]
    for i, (k, v) in enumerate(facts, start=3):
        ws.cell(row=i, column=1, value=k).font = _BOLD
        c = ws.cell(row=i, column=2, value=v)
        if isinstance(v, float) or k in ("Refunded",):
            c.number_format = _MONEY_FMT
    note_row = 3 + len(facts)
    missing = len(rows) - len(priced)
    if missing:
        ws.cell(row=note_row, column=1,
                value=f"{missing} document{'s have' if missing != 1 else ' has'} no amount on file "
                      "and {} not in these totals — see the Note column on the Invoices sheet."
                      .format("are" if missing != 1 else "is")).font = _MUTED
        note_row += 1
    ws.column_dimensions["A"].width = 40
    ws.column_dimensions["B"].width = 24
    if by_supplier:
        groups: Dict[str, Dict[str, Any]] = {}
        for r in rows:
            g = groups.setdefault(r.get("party") or "Unknown supplier", {"n": 0, "cents": 0, "missing": 0})
            g["n"] += 1
            if r.get("total_cents") is None:
                g["missing"] += 1
            else:
                g["cents"] += r["total_cents"]
        start = note_row + 1
        _table(ws, start, ["Supplier", "Documents", "Total spend", "Share", "No amount on file"],
               [40, 24, 16, 10, 18])
        ws.freeze_panes = None
        line = start
        for name, g in sorted(groups.items(), key=lambda kv: kv[1]["cents"], reverse=True):
            line += 1
            ws.cell(row=line, column=1, value=name)
            ws.cell(row=line, column=2, value=g["n"])
            _money(ws.cell(row=line, column=3), g["cents"])
            share = ws.cell(row=line, column=4, value=(g["cents"] / total) if total else None)
            share.number_format = "0.0%"
            ws.cell(row=line, column=5, value=g["missing"] or None)
        _sum_row(ws, line + 1, 1, [3], start + 1, line)
        _finish_table(ws, start, line, 5)

    inv = wb.create_sheet("Invoices")
    heads = ["Date", "Invoice no.", "Supplier", "Type", "Excl. VAT", "VAT", "Total", "Lines", "Note"]
    _table(inv, 1, heads, [12, 20, 34, 10, 14, 12, 14, 7, 34])
    row = 1
    for r in rows:
        row += 1
        d = inv.cell(row=row, column=1, value=_as_date(r.get("date") or ""))
        d.number_format = _DATE_FMT
        inv.cell(row=row, column=2, value=r.get("ref"))
        inv.cell(row=row, column=3, value=r.get("party"))
        inv.cell(row=row, column=4, value="Refund" if r.get("is_refund") else "Invoice")
        t, v = r.get("total_cents"), r.get("vat_cents")
        _money(inv.cell(row=row, column=5), (t - v) if t is not None and v is not None else None)
        _money(inv.cell(row=row, column=6), v)
        _money(inv.cell(row=row, column=7), t)
        inv.cell(row=row, column=8, value=len(r.get("lines") or []) or None)
        notes = []
        if t is None:
            notes.append("No amount on file")
        if r.get("photo"):
            notes.append("WhatsApp photo")
        if r.get("party_inferred"):
            notes.append("Supplier read from the document summary")
        inv.cell(row=row, column=9, value="; ".join(notes) or None)
    _sum_row(inv, row + 1, 4, [5, 6, 7], 2, row)
    _finish_table(inv, 1, row, len(heads))

    li = wb.create_sheet("Line items")
    heads = ["Date", "Invoice no.", "Supplier", "Description", "Qty", "Unit price", "Line total"]
    _table(li, 1, heads, [12, 20, 30, 44, 8, 13, 14])
    row = 1
    for r in rows:
        for ln in r.get("lines") or []:
            row += 1
            d = li.cell(row=row, column=1, value=_as_date(r.get("date") or ""))
            d.number_format = _DATE_FMT
            li.cell(row=row, column=2, value=r.get("ref"))
            li.cell(row=row, column=3, value=r.get("party"))
            li.cell(row=row, column=4, value=ln.get("description"))
            q = ln.get("quantity")
            li.cell(row=row, column=5, value=(int(q) if q is not None and float(q).is_integer() else q))
            _money(li.cell(row=row, column=6), ln.get("unit_cents"))
            _money(li.cell(row=row, column=7), ln.get("total_cents"))
    if row == 1:
        li.cell(row=2, column=1, value="No line items were extracted from these documents.").font = _MUTED
    else:
        _sum_row(li, row + 1, 6, [7], 2, row)
        _finish_table(li, 1, row, len(heads))

    if materials:
        ms = wb.create_sheet("Materials")
        heads = ["Description", "Total qty", "Spend", "Invoices", "Avg unit price", "Check"]
        _table(ms, 1, heads, [44, 10, 14, 9, 14, 34])
        row = 1
        for it in materials:
            row += 1
            ms.cell(row=row, column=1, value=it.get("description"))
            qty = it.get("quantity")
            ms.cell(row=row, column=2, value=qty)
            _money(ms.cell(row=row, column=3), it.get("spend_cents"))
            ms.cell(row=row, column=4, value=it.get("documents"))
            unit = (it["spend_cents"] / qty) if qty and it.get("spend_cents") is not None else None
            _money(ms.cell(row=row, column=5), int(round(unit)) if unit is not None else None)
            checks = []
            if it.get("unit_price_varies"):
                checks.append("Unit price varies a lot — check the quantity")
            if it.get("quantity_incomplete"):
                checks.append("Some lines have no quantity")
            if it.get("spend_incomplete"):
                checks.append("Some lines have no price")
            ms.cell(row=row, column=6, value="; ".join(checks) or None)
        _sum_row(ms, row + 1, 2, [3], 2, row)
        _finish_table(ms, 1, row, len(heads))

    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def render_supplier_history_xlsx(result: Dict[str, Any], supplier: str = "",
                                 accent: Optional[str] = None) -> Optional[bytes]:
    """A find_filed_document result for one supplier as .xlsx bytes. Uses the result's private
    `_export_rows` (every fetched document) when present, else rebuilds rows from the listed
    matches. None when the result isn't a complete filed-document answer."""
    if result.get("status") != "found" or "total_amount_cents" not in result:
        return None
    rows = result.get("_export_rows")
    if rows is None:
        from vula.commerce.service import _doc_ref, _canonical_party
        rows = []
        for m in result.get("matches") or []:
            amt = m.get("amount")
            cents = int(round(float(amt) * 100)) if amt is not None else None
            if cents is not None and m.get("is_refund"):
                cents = -abs(cents)
            rows.append({"date": (m.get("filed_at") or "")[:10], "ref": _doc_ref(m.get("filename") or ""),
                         "party": _canonical_party(m.get("party") or "") or supplier,
                         "is_refund": bool(m.get("is_refund")), "total_cents": cents,
                         "vat_cents": None, "lines": []})
    name = supplier or result.get("resolved_supplier") or "Supplier"
    return render_invoices_xlsx(rows, name, materials=result.get("_materials_all") or result.get("materials"),
                                accent=accent)


def render_supplier_history_pdf(result: Dict[str, Any], supplier: str = "",
                                business: str = "") -> Optional[bytes]:
    """The same supplier history as a shareable PDF: a written summary (period, invoices vs
    refunds, excl-VAT / VAT / total, spend by month), every invoice (refunds negative; an invoice
    whose extracted lines don't add up to its total is flagged) and every material. Figures are
    the rows' own integer cents; every total is summed here, never by a model. None when the
    result isn't a complete filed-document answer."""
    import html as _h
    if result.get("status") != "found" or "total_amount_cents" not in result:
        return None
    from weasyprint import HTML
    rows = result.get("_export_rows")
    if rows is None:
        rows = [{"date": (m.get("filed_at") or "")[:10], "ref": m.get("filename") or "",
                 "is_refund": bool(m.get("is_refund")),
                 "total_cents": (int(round(float(m["amount"]) * 100)) * (-1 if m.get("is_refund") else 1))
                 if m.get("amount") is not None else None} for m in (result.get("matches") or [])]
    # refunds always count negative, whether or not the rows were signed already
    rows = [dict(r, total_cents=(-abs(r["total_cents"]) if r.get("is_refund") else r["total_cents"])
                 if r.get("total_cents") is not None else None,
                 vat_cents=(-abs(r["vat_cents"]) if r.get("is_refund") else r["vat_cents"])
                 if r.get("vat_cents") is not None else None) for r in rows]
    rows = sorted(rows, key=lambda r: (r.get("date") or "", str(r.get("ref") or "")), reverse=True)
    mats = result.get("_materials_all") or result.get("materials") or []
    e = _h.escape

    def money(c):
        return "—" if c is None else f"{'−' if c < 0 else ''}R{abs(c) / 100:,.2f}"
    priced = [r for r in rows if r.get("total_cents") is not None]
    total = sum(r["total_cents"] for r in priced)
    refunds = [r for r in priced if r.get("is_refund")]
    vat_known = [r for r in priced if r.get("vat_cents") is not None]
    vat = sum(r["vat_cents"] for r in vat_known) if len(vat_known) == len(priced) else None
    dates = sorted(r["date"] for r in rows if r.get("date"))
    months: Dict[str, list] = {}
    for r in priced:
        if r.get("date"):
            m = months.setdefault(r["date"][:7], [0, 0])
            m[0] += r["total_cents"]
            m[1] += 1

    def short(r):
        lines = [ln.get("total_cents") for ln in (r.get("lines") or [])]
        if not lines or any(c is None for c in lines) or r.get("total_cents") is None:
            return None
        gap = abs(r["total_cents"]) - abs(sum(lines))
        return gap if gap > 100 else None          # more than R1 not accounted for by the lines
    flagged = [(r, short(r)) for r in rows if short(r)]
    name = e(supplier or result.get("resolved_supplier") or "Supplier")
    summ = [("Period", f"{dates[0]} to {dates[-1]}" if dates else "—"),
            ("Documents", f"{len(priced) - len(refunds)} invoices, {len(refunds)} refunds"
             + (f", {len(rows) - len(priced)} with no amount" if len(rows) > len(priced) else "")),
            ("Refunds", money(sum(r["total_cents"] for r in refunds)) if refunds else "none")]
    if vat is not None:
        summ += [("Excl VAT", money(total - vat)), ("VAT", money(vat))]
    summ.append(("Total spend", money(total)))
    srows = "".join(f"<tr><td>{e(k)}</td><td class=n>{e(v)}</td></tr>" for k, v in summ)
    mrows = "".join(f"<tr><td>{k}</td><td class=n>{v[1]}</td><td class=n>{money(v[0])}</td></tr>"
                    for k, v in sorted(months.items()))
    inv = "".join(
        f"<tr><td>{e(r.get('date') or '')}</td><td>{e(str(r.get('ref') or ''))}"
        f"{' (refund)' if r.get('is_refund') else ''}{' ⚠' if short(r) else ''}</td>"
        f"<td class=n>{money(r.get('vat_cents'))}</td><td class=n>{money(r.get('total_cents'))}</td></tr>"
        for r in rows)
    mat = "".join(f"<tr><td>{e(str(m.get('description') or ''))}</td><td class=n>"
                  f"{m.get('quantity') if m.get('quantity') is not None else ''}</td>"
                  f"<td class=n>{money(m.get('spend_cents'))}</td></tr>" for m in mats)
    note = ""
    if flagged:
        note = ("<p class=w>⚠ The line items read from " + ", ".join(
            f"{e(str(r.get('ref')))} ({money(g)} short)" for r, g in flagged)
            + " add up to less than the invoice total — a line was missed when the invoice was "
              "read. The invoice totals above are correct; the materials list is short by that much.</p>")
    doc = f"""<html><head><meta charset="utf-8"><style>
body{{font-family:sans-serif;font-size:10px;color:#222}} h1{{font-size:16px;margin:0}}
h2{{font-size:12px;margin-top:16px;border-bottom:1px solid #ccc;page-break-after:avoid;break-after:avoid}} table{{width:100%;border-collapse:collapse}}
td,th{{padding:2px 4px;text-align:left}} .n{{text-align:right}} .t td{{font-weight:bold;border-top:1px solid #999}}
.w{{color:#8a5a00;font-size:9px}} .s{{width:50%}}
</style></head><body><h1>{name} — account summary</h1>
<p>{e(business)} · invoices and materials as filed · refunds shown negative</p>
<h2>Summary</h2><table class=s>{srows}</table>
{"<h2>By month</h2><table class=s><tr><th>Month</th><th class=n>Docs</th><th class=n>Spend</th></tr>" + mrows + "</table>" if len(months) > 1 else ""}
<h2>Every invoice ({len(rows)})</h2><table><tr><th>Date</th><th>Invoice</th><th class=n>VAT</th><th class=n>Total</th></tr>{inv}
<tr class=t><td></td><td>Total</td><td class=n>{money(vat)}</td><td class=n>{money(total)}</td></tr></table>
{note}
{"<h2 style='page-break-before:always'>Every material (" + str(len(mats)) + ", biggest spend first)</h2><table><tr><th>Item</th><th class=n>Qty</th><th class=n>Spend</th></tr>" + mat + "</table>" if mat else ""}
</body></html>"""
    return HTML(string=doc).write_pdf()
