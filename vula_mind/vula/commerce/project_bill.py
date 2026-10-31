"""
vula/commerce/project_bill.py — a project's bill (final account) as a spreadsheet and a PDF.

31 Oct 2026 (Ian): the bookkeeper's project-bill task. A final account for the client was built by
hand from the bank, the filed invoices and the business's own reconciled sheet: budget against
actual against paid per trade, what the client paid suppliers directly, what they paid the
business, and the balance due. Everything comes from the project tracker (project_tracker.position:
the same figures the project page and the client link show — no second set of books), with the
project's decisions (decisions.py) as the notes that explain the figures.

    balance due = total cost (+ the fee on a cost-plus job)
                  − paid directly by the client − still to pay directly − paid to the business

The spreadsheet carries formulas, so the owner can adjust it. Before a link is handed out the
saved file is read back: its formulas are evaluated from the file itself and must give the same
figures as the tracker, to the cent — otherwise nothing is sent. All money is integer cents.
The file goes to the owner only (a private, signed link); it reaches the client only when the
owner forwards it.
"""
from __future__ import annotations

import html
import io
import logging
import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

log = logging.getLogger(__name__)

_HEAD = ("Trade / Section", "Budget (R)", "Actual (R)", "Paid to date (R)", "Outstanding (R)",
         "Variance: budget − actual (R)", "Notes")
_FIRST_ROW = 6          # first line of the table (rows 1–4: title block, 5: headings)
_MONEY = '#,##0.00;(#,##0.00);"-"'


def _key(s: Optional[str]) -> str:
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


def _r(cents: int) -> str:
    return f"R{cents / 100:,.2f}" if cents >= 0 else f"(R{-cents / 100:,.2f})"


# ── the figures ────────────────────────────────────────────────────────────

def lines(pos: Dict[str, Any]) -> List[Dict[str, Any]]:
    """The bill's lines, in cents. With the business's own reconciled sheet on file (BOQ sections
    carrying actual and paid), its sections are the lines. Otherwise: the budget's sections, the
    business's cost per trade from the bank (paid — it left the bank), and what the client paid
    or still has to pay suppliers directly."""
    sections = pos.get("sections") or []
    if pos.get("sheet"):
        return [{"section": s["section"], "budget": int(s.get("budget_cents") or 0),
                 "actual": int(s.get("actual_cents") or 0), "paid": int(s.get("paid_cents") or 0)}
                for s in sections]
    out: Dict[str, Dict[str, Any]] = {}
    for s in sections:
        out.setdefault(_key(s["section"]), {"section": s["section"], "budget": 0, "actual": 0, "paid": 0})
        out[_key(s["section"])]["budget"] += int(s.get("budget_cents") or 0)
    trades = 0
    for t in pos.get("trades") or []:
        row = out.setdefault(_key(t["trade"]), {"section": t["trade"], "budget": 0, "actual": 0, "paid": 0})
        row["actual"] += int(t["cents"])
        row["paid"] += int(t["cents"])
        trades += int(t["cents"])
    rows = list(out.values())
    other = int(pos.get("business_cost_cents") or 0) - trades
    if other:
        rows.append({"section": "Other project costs (not yet put on a trade)", "budget": 0,
                     "actual": other, "paid": other})
    unitemised = int(pos.get("paid_directly_suppliers_cents") or 0) + int(pos.get("paid_directly_slips_cents") or 0)
    if unitemised:
        rows.append({"section": "Supplier invoices and slips the client paid", "budget": 0,
                     "actual": unitemised, "paid": unitemised})
    for p in pos.get("direct_payments") or []:
        paid = p.get("status") == "paid"
        rows.append({"section": f"Paid by the client directly: {p.get('supplier') or 'supplier'}", "budget": 0,
                     "actual": int(p["amount_cents"]), "paid": int(p["amount_cents"]) if paid else 0})
    return rows


def figures(pos: Dict[str, Any]) -> Dict[str, Any]:
    """The bill's totals and the balance due, in cents, from the tracker's position."""
    rows = lines(pos)
    actual = sum(r["actual"] for r in rows)
    budget = sum(r["budget"] for r in rows)
    fee = int(pos.get("fee_cents") or 0) if float(pos.get("fee_pct") or 0) > 0 else 0
    total = actual + fee
    direct = int(pos.get("paid_directly_cents") or 0)
    to_pay = int(pos.get("to_pay_directly_cents") or 0)
    received = int(pos.get("received_cents") or 0)
    return {"lines": rows, "budget": budget, "actual": actual, "paid": sum(r["paid"] for r in rows),
            "fee": fee, "fee_pct": float(pos.get("fee_pct") or 0), "total": total,
            "paid_directly": direct, "to_pay_directly": to_pay, "received": received,
            "balance": total - direct - to_pay - received, "over_budget": actual - budget if budget else None}


def _notes(rows: List[Dict[str, Any]], decisions: List[Dict[str, Any]]) -> Dict[int, str]:
    """A decision goes on the line whose name it mentions (newest first)."""
    out: Dict[int, List[str]] = {}
    for d in decisions:
        text = f"{d.get('subject') or ''} {d.get('decision') or ''}".lower()
        for i, r in enumerate(rows):
            words = [w for w in re.findall(r"[a-z]{4,}", r["section"].lower()) if w not in ("paid", "client", "directly")]
            if words and any(w in text for w in words):
                out.setdefault(i, []).append((d.get("decision") or "").strip())
                break
    return {i: "; ".join(v) for i, v in out.items()}


# ── the spreadsheet ────────────────────────────────────────────────────────

def workbook(business: str, project: str, f: Dict[str, Any], decisions: List[Dict[str, Any]],
             as_of: str) -> Tuple[bytes, Dict[str, str]]:
    """The xlsx, and the cells holding each figure (for the read-back)."""
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Border, Font, PatternFill, Side

    def font(**k):
        return Font(**{"name": "Arial", "size": 9, **k})
    fill = {name: PatternFill("solid", start_color=c) for name, c in
            {"head": "D9D9D9", "budget": "EDEDED", "paid": "E2F0D9", "owed": "F8CBAD", "total": "D9D9D9"}.items()}
    thin = Border(top=Side(style="thin"), bottom=Side(style="thin"))

    wb = Workbook()
    ws = wb.active
    ws.title = "Project bill"
    ws["A1"], ws["A2"], ws["A3"] = business, f"Project bill — {project}", f"As at {as_of}"
    ws["A1"].font, ws["A2"].font, ws["A3"].font = font(size=12, bold=True), font(size=11, bold=True), font()
    for c, h in enumerate(_HEAD, start=1):
        cell = ws.cell(row=5, column=c, value=h)
        cell.font, cell.fill, cell.alignment = font(bold=True), fill["head"], Alignment(wrap_text=True)
    notes = _notes(f["lines"], decisions)
    r = _FIRST_ROW
    for i, ln in enumerate(f["lines"]):
        ws.cell(row=r, column=1, value=ln["section"]).font = font()
        for c, v in ((2, ln["budget"]), (3, ln["actual"]), (4, ln["paid"])):
            ws.cell(row=r, column=c, value=v / 100)
        ws.cell(row=r, column=5, value=f"=C{r}-D{r}")
        ws.cell(row=r, column=6, value=f"=B{r}-C{r}")
        ws.cell(row=r, column=7, value=notes.get(i, "")).font = font(italic=True)
        r += 1
    last = r - 1
    total_row = r
    ws.cell(row=r, column=1, value="TOTAL").font = font(bold=True)
    for c in "BCDEF":
        ws[f"{c}{r}"] = f"=SUM({c}{_FIRST_ROW}:{c}{last})" if last >= _FIRST_ROW else 0
    for c in range(1, 8):
        ws.cell(row=r, column=c).fill, ws.cell(row=r, column=c).border = fill["total"], thin
    for row in ws.iter_rows(min_row=_FIRST_ROW, max_row=total_row, min_col=2, max_col=6):
        for cell in row:
            cell.number_format = _MONEY
            cell.font = font(bold=cell.row == total_row)
    for row in range(_FIRST_ROW, total_row):
        ws[f"B{row}"].fill, ws[f"D{row}"].fill, ws[f"E{row}"].fill = fill["budget"], fill["paid"], fill["owed"]

    r = total_row + 2
    ws.cell(row=r, column=1, value="SUMMARY").font = font(bold=True)
    cells: Dict[str, str] = {"actual": f"C{total_row}", "budget": f"B{total_row}", "paid": f"D{total_row}"}

    def line(label, value, key, bold=False):
        nonlocal r
        r += 1
        ws.cell(row=r, column=1, value=label).font = font(bold=bold)
        cell = ws.cell(row=r, column=3, value=value)
        cell.number_format, cell.font = _MONEY, font(bold=bold)
        cells[key] = f"C{r}"
    line("Total project cost (actual)", f"=C{total_row}", "cost")
    if f["fee"]:
        line(f"Fee ({f['fee_pct']:g}% on our cost)", f["fee"] / 100, "fee")
    line("Total", f"={cells['cost']}" + (f"+{cells['fee']}" if f["fee"] else ""), "total", bold=True)
    line("Less: paid directly to suppliers by the client", f["paid_directly"] / 100, "paid_directly")
    line("Less: still to pay directly by the client", f["to_pay_directly"] / 100, "to_pay_directly")
    line(f"Less: paid to {business}", f["received"] / 100, "received")
    line("BALANCE DUE", f"={cells['total']}-{cells['paid_directly']}-{cells['to_pay_directly']}-{cells['received']}",
         "balance", bold=True)
    ws[cells["balance"]].border = thin
    if f["budget"]:
        r += 1
        line("Original budget", f"=B{total_row}", "budget_line")
        line("Over / (under) budget", f"=C{total_row}-B{total_row}", "over_budget")

    if decisions:
        r += 2
        ws.cell(row=r, column=1, value="DECISIONS BEHIND THESE FIGURES").font = font(bold=True)
        for d in decisions:
            r += 1
            ws.cell(row=r, column=1, value=(d.get("created_at") or "")[:10]).font = font()
            ws.cell(row=r, column=2, value=(d.get("decision") or "").strip()).font = font()
    r += 2
    ws.cell(row=r, column=1, value="Grey: budget · green: paid · coral: still outstanding. "
                                   "Figures from the business's books; formulas recalculate if you edit them.").font = font(italic=True)
    for col, w in zip("ABCDEFG", (44, 15, 15, 15, 15, 18, 60)):
        ws.column_dimensions[col].width = w
    ws.freeze_panes = f"B{_FIRST_ROW}"
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue(), cells


_TOKEN = re.compile(r"\s*(SUM\(([A-Z]+)(\d+):([A-Z]+)(\d+)\)|[A-Z]+\d+|\d+(?:\.\d+)?|[+-])")


def evaluate(ws, ref: str, _depth: int = 0) -> float:
    """The value of a cell in a workbook this module wrote: a number, or a formula of cell
    references, SUM(range), + and −. Read back from the saved file, not from the figures."""
    if _depth > 50:
        raise ValueError("formula loop")
    v = ws[ref].value
    if v is None:
        return 0.0
    if not isinstance(v, str):
        return float(v)
    if not v.startswith("="):
        raise ValueError(f"{ref} is not a number")
    expr, pos, total, sign = v[1:], 0, 0.0, 1
    while pos < len(expr):
        m = _TOKEN.match(expr, pos)
        if not m:
            raise ValueError(f"{ref}: can't read {expr[pos:]!r}")
        tok, pos = m.group(1), m.end()
        if tok in "+-":
            sign = 1 if tok == "+" else -1
            continue
        if tok.startswith("SUM("):
            col, a, b = m.group(2), int(m.group(3)), int(m.group(5))
            val = sum(evaluate(ws, f"{col}{i}", _depth + 1) for i in range(a, b + 1))
        elif tok[0].isdigit():
            val = float(tok)
        else:
            val = evaluate(ws, tok, _depth + 1)
        total += sign * val
    return total


def read_back(data: bytes, cells: Dict[str, str], f: Dict[str, Any]) -> List[str]:
    """Each figure whose cell, evaluated from the saved file, isn't the tracker's figure."""
    from openpyxl import load_workbook
    ws = load_workbook(io.BytesIO(data)).active
    want = {"actual": f["actual"], "budget": f["budget"], "paid": f["paid"], "total": f["total"],
            "balance": f["balance"]}
    if f["over_budget"] is not None and "over_budget" in cells:
        want["over_budget"] = f["over_budget"]
    out = []
    for k, cents in want.items():
        got = int(round(evaluate(ws, cells[k]) * 100))
        if got != cents:
            out.append(f"{k}: file {got} vs books {cents}")
    return out


# ── the PDF ────────────────────────────────────────────────────────────────

def render_html(business: str, project: str, f: Dict[str, Any], decisions: List[Dict[str, Any]], as_of: str) -> str:
    e = html.escape
    notes = _notes(f["lines"], decisions)
    body = "".join(
        f"<tr><td>{e(ln['section'])}</td><td class=n>{_r(ln['budget'])}</td><td class=n>{_r(ln['actual'])}</td>"
        f"<td class=n>{_r(ln['paid'])}</td><td class=n>{_r(ln['actual'] - ln['paid'])}</td>"
        f"<td class=n>{_r(ln['budget'] - ln['actual'])}</td><td class=t>{e(notes.get(i, ''))}</td></tr>"
        for i, ln in enumerate(f["lines"]))
    tot = (f"<tr class=tot><td>TOTAL</td><td class=n>{_r(f['budget'])}</td><td class=n>{_r(f['actual'])}</td>"
           f"<td class=n>{_r(f['paid'])}</td><td class=n>{_r(f['actual'] - f['paid'])}</td>"
           f"<td class=n>{_r(f['budget'] - f['actual'])}</td><td></td></tr>")
    summ = [("Total project cost (actual)", f["actual"])]
    if f["fee"]:
        summ.append((f"Fee ({f['fee_pct']:g}% on our cost)", f["fee"]))
    summ += [("<b>Total</b>", f["total"]), ("Less: paid directly to suppliers by the client", f["paid_directly"]),
             ("Less: still to pay directly by the client", f["to_pay_directly"]),
             (f"Less: paid to {e(business)}", f["received"]), ("<b>BALANCE DUE</b>", f["balance"])]
    srows = "".join(f"<tr><td>{k}</td><td class=n>{'<b>' + _r(v) + '</b>' if k.startswith('<b>') else _r(v)}</td></tr>"
                    for k, v in summ)
    dec = "".join(f"<li>{e((d.get('created_at') or '')[:10])} — {e((d.get('decision') or '').strip())}</li>"
                  for d in decisions)
    return f"""<!doctype html><html><head><meta charset="utf-8"><style>
@page {{ size: A4 landscape; margin: 14mm; }}
body {{ font: 9pt Arial, sans-serif; color: #222; }}
h1 {{ font-size: 13pt; margin: 0; }} h2 {{ font-size: 11pt; margin: 2px 0 8px; }}
table {{ border-collapse: collapse; width: 100%; margin-bottom: 12px; }}
th, td {{ border-bottom: 1px solid #ccc; padding: 3px 5px; text-align: left; vertical-align: top; }}
th {{ background: #d9d9d9; }} td.n {{ text-align: right; white-space: nowrap; }}
td.t {{ font-style: italic; }} tr.tot td {{ font-weight: bold; background: #ededed; }}
.sum {{ width: 50%; }}
</style></head><body>
<h1>{e(business)}</h1><h2>Project bill — {e(project)}</h2><p>As at {e(as_of)}</p>
<table><tr>{''.join(f'<th>{e(h)}</th>' for h in _HEAD)}</tr>{body}{tot}</table>
<table class=sum>{srows}</table>
{f'<h2>Decisions behind these figures</h2><ul>{dec}</ul>' if dec else ''}
</body></html>"""


def pdf(business: str, project: str, f: Dict[str, Any], decisions: List[Dict[str, Any]], as_of: str) -> bytes:
    from weasyprint import HTML
    return HTML(string=render_html(business, project, f, decisions, as_of)).write_pdf()


# ── the tool ───────────────────────────────────────────────────────────────

def summary(project: str, f: Dict[str, Any]) -> str:
    out = [f"📒 *{project}* — project bill", f"Total project cost: {_r(f['actual'])}"]
    if f["fee"]:
        out.append(f"Fee ({f['fee_pct']:g}%): {_r(f['fee'])}")
    if f["budget"]:
        out.append(f"Budget: {_r(f['budget'])} · over/(under): {_r(f['over_budget'])}")
    out += [f"Paid directly by the client: {_r(f['paid_directly'])}",
            f"Still to pay directly: {_r(f['to_pay_directly'])}",
            f"Paid to you: {_r(f['received'])}",
            f"*Balance due: {_r(f['balance'])}*"]
    return "\n".join(out)


def build(tenant_id: str, project: str, business: str) -> Dict[str, Any]:
    """Build both files from the books, read the spreadsheet back, store them privately and
    return the summary with signed links. Raises RuntimeError when the read-back disagrees."""
    from vula.commerce import decisions as dec
    from vula.commerce import project_tracker as pt
    pos = pt.position(tenant_id, project)
    name = pos.get("project") or project
    f = figures(pos)
    decs = list(reversed(dec.for_project(tenant_id, name, limit=40)))          # oldest first
    as_of = pos.get("as_of") or datetime.now(timezone.utc).date().isoformat()
    data, cells = workbook(business, name, f, decs, as_of)
    wrong = read_back(data, cells, f)
    if wrong:
        log.error("project bill read-back failed for %s/%s: %s", tenant_id, name, wrong)
        raise RuntimeError("the spreadsheet didn't add up to the books")
    from vula.api.whatsapp import _upload_to_storage
    from vula.storage_links import SHARE_TTL, signed
    stem = f"{tenant_id}/projects/{_key(name) or 'project'}/project_bill_{as_of}"
    xurl = _upload_to_storage("documents", stem + ".xlsx", data,
                              "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    purl = None
    try:
        purl = _upload_to_storage("documents", stem + ".pdf", pdf(business, name, f, decs, as_of), "application/pdf")
    except Exception as exc:  # noqa: BLE001 — the spreadsheet still goes out
        log.warning("project bill PDF failed for %s/%s: %s", tenant_id, name, exc)
    links = {k: signed(u, SHARE_TTL) for k, u in (("xlsx", xurl), ("pdf", purl)) if u}
    return {"project": name, "figures": f, "links": links, "decisions": len(decs)}
