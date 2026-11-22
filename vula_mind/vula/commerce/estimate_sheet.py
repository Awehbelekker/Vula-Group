"""
vula/commerce/estimate_sheet.py — an estimate (vula/commerce/estimate.py) as an Excel breakdown.

Trade costing, phase 3 (14 Nov 2026). One row per build-up line: the item, the component, how
much per unit and the waste, the quantity, the unit price, where the price came from and its
date, and the cost as a live formula (quantity × unit price, rounded to cents). Item and grand
totals are SUM formulas. The saved file is re-read (xlsx_check.mismatches) and must match the
estimate to the cent before it is sent.
"""
from __future__ import annotations

import io
from typing import Any, Dict, Tuple

from vula.commerce.xlsx_check import safe_text

HEAD = ["Item", "Component", "Kind", "Per unit", "Waste %", "Quantity", "Unit", "Unit price (R)",
        "Cost (R)", "Price from", "Price date"]
_BASIS = {"paid": "What you paid", "quoted": "Your quotes/BOQs", "market": "Market"}


def workbook(business: str, est: Dict[str, Any]) -> Tuple[bytes, Dict[str, int]]:
    """(xlsx bytes, {cell: cents} to check after saving)."""
    from openpyxl import Workbook
    from openpyxl.styles import Font
    wb = Workbook()
    ws = wb.active
    ws.title = "Estimate"
    safe_text(ws.cell(row=1, column=1), f"{business} — estimate")
    ws.cell(row=1, column=1).font = Font(bold=True, size=13)
    safe_text(ws.cell(row=2, column=1), est["title"])
    from vula.commerce.estimate import vat_text
    if vat_text(est):    # which VAT basis every price and total is on (16 Nov 2026)
        safe_text(ws.cell(row=3, column=1), vat_text(est))
    for c, h in enumerate(HEAD, 1):
        ws.cell(row=4, column=c, value=h).font = Font(bold=True)
    want: Dict[str, int] = {}
    r = 5
    item_totals = []
    for it in est["items"]:
        if it.get("status") != "priced":
            safe_text(ws.cell(row=r, column=1), it["item"])
            safe_text(ws.cell(row=r, column=2), it.get("message") or "Not priced")
            r += 2
            continue
        first = r
        for ln in it["lines"]:
            safe_text(ws.cell(row=r, column=1), f"{it['item']} ({it['quantity']} {it['unit']})")
            safe_text(ws.cell(row=r, column=2), ln["label"])
            ws.cell(row=r, column=3, value=ln["kind"])
            ws.cell(row=r, column=4, value=float(ln["per_unit"]))
            ws.cell(row=r, column=5, value=float(ln["waste_pct"]))
            ws.cell(row=r, column=6, value=float(ln["quantity"]))
            safe_text(ws.cell(row=r, column=7), ln["unit"])
            if ln.get("priced"):
                ws.cell(row=r, column=8, value=ln["unit_price_cents"] / 100).number_format = "#,##0.00"
                ws.cell(row=r, column=9, value=f"=ROUND(F{r}*H{r},2)").number_format = "#,##0.00"
                safe_text(ws.cell(row=r, column=10), f"{_BASIS.get(ln['source'], ln['source'])}: {ln.get('where') or ''}"
                          + (" (VAT basis unknown)" if ln.get("vat_unknown") else ""))
                ws.cell(row=r, column=11, value=ln.get("date") or "")
                want[f"I{r}"] = int(ln["cost_cents"])
            else:
                safe_text(ws.cell(row=r, column=10), "Not priced — no price on file or checked online")
            r += 1
        ws.cell(row=r, column=8, value="Item total").font = Font(bold=True)
        ws.cell(row=r, column=9, value=f"=SUM(I{first}:I{r - 1})").number_format = "#,##0.00"
        ws.cell(row=r, column=9).font = Font(bold=True)
        want[f"I{r}"] = int(it["total_cents"])
        item_totals.append(f"I{r}")
        r += 2
    ws.cell(row=r, column=8, value="Total").font = Font(bold=True)
    ws.cell(row=r, column=9, value="=" + "+".join(item_totals) if item_totals else 0).number_format = "#,##0.00"
    ws.cell(row=r, column=9).font = Font(bold=True)
    want[f"I{r}"] = int(est["total_cents"])
    r += 2
    notes = []
    for it in est["items"]:
        if it.get("needs_figure"):
            notes.append(f"{it['item']} — needs a figure: {', '.join(it['needs_figure'])}")
        if it.get("not_in_buildup"):
            notes.append(f"{it['item']} — not in the build-up: {', '.join(it['not_in_buildup'])}")
        c = it.get("compare")
        if c:
            notes.append(f"{it['item']} — your own rate {c['rate_cents'] / 100:,.2f}/{it['unit']} ({c['where']}) "
                         f"→ {c['total_cents'] / 100:,.2f}")
    if est.get("exclusions"):
        notes.append("Not included, as asked: " + ", ".join(est["exclusions"]))
    notes.append(f"Labour priced per day is converted at {est.get('hours_per_day', 8)} hours a day.")
    for n in notes:
        safe_text(ws.cell(row=r, column=1), n)
        r += 1
    for col, width in zip("ABCDEFGHIJK", (34, 30, 10, 10, 9, 11, 8, 14, 14, 50, 12)):
        ws.column_dimensions[col].width = width
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue(), want
