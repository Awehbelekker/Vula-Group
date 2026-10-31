"""
vula/commerce/forms.py — forms the team fills in, read back and booked only when the owner says yes.

31 Oct 2026 (Ian: item 2 — "all four forms; read back, staged for approval"). Vula makes a quote /
order form, a site checklist, a timesheet or a stock count as a spreadsheet: the business's logo and
details, the cells to fill in shaded yellow (everything else locked against accidents), dropdowns
from the business's real lists (products and prices, workers and rates, projects, Done / Not done /
N/A), and the sums built in.

When the file comes back on WhatsApp, it is recognised by the id on its hidden "_vula" sheet AND
the business it was made for (migration 239 `vula_forms`). Only the yellow cells are read — the
file's own formulas are never trusted; code works the figures out again from the lists the form was
made with. Every value is checked; a name not on the list, a number that isn't one, a date that isn't
real is listed as a problem, never guessed. What would be booked is staged and shown to the owner
with Confirm / Cancel; only Confirm books it, once, and the result is read back:
  quote          → a draft quote (sending it stays the normal previewed step)
  stock count    → a stock count, applied (stock_counts.py — the same path as a scanned count)
  timesheet      → time worked and pay due per worker (vula_timesheet_entries) — not a cost: the
                   cost is the bank payment, so nothing is counted twice
  site checklist → a checklist card on the project's board
"""
from __future__ import annotations

import io
import logging
import re
import uuid
from datetime import date, datetime, timezone
from decimal import ROUND_HALF_UP, Decimal
from typing import Any, Dict, List, Optional, Tuple

log = logging.getLogger(__name__)

TABLE = "vula_forms"
TIMESHEET = "vula_timesheet_entries"
MARK = "_vula"
TYPES: Dict[str, Tuple[str, Optional[str]]] = {          # form -> (title, dashboard access it needs)
    "quote": ("Quote / order form", "invoices"),
    "site_checklist": ("Site checklist", None),
    "timesheet": ("Timesheet", "finances"),
    "stock_count": ("Stock count", "products"),
}
STATUSES = ["Done", "Not done", "N/A"]
DEFAULT_CHECKLIST = ["Site safe and tidy", "Materials delivered and checked", "Work matches the drawings",
                     "Snags noted", "Photos taken", "Client walk-through done"]
QUOTE_ROWS, TIMESHEET_ROWS, CHECKLIST_EXTRA, MAX_PRODUCTS = 20, 40, 5, 500
HEAD = 8                                                  # the table's heading row; data from row 9
_ID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")


class FormError(ValueError):
    """The form can't be made or booked; the message says why, in plain words."""


def _db():
    from vula.commerce import service
    return service._client()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _r(cents: int) -> str:
    return f"R{cents / 100:,.2f}" if cents >= 0 else f"−R{-cents / 100:,.2f}"


def _cents(rands: Decimal) -> int:
    return int((rands * 100).quantize(Decimal(1), rounding=ROUND_HALF_UP))


def _key(s: Any) -> str:
    return re.sub(r"\s+", " ", str(s or "")).strip().lower()


# ── the business's lists, as at the time the form is made ───────────────────

async def _products(tenant_id: str) -> List[Dict[str, Any]]:
    from vula.commerce import service
    rows = await service.list_products(tenant_id, in_stock_only=False, include_archived=False)
    out, seen = [], set()
    for p in sorted(rows or [], key=lambda p: _key(p.get("name"))):
        if not p.get("name") or _key(p["name"]) in seen:
            continue
        seen.add(_key(p["name"]))
        out.append({"id": p["id"], "name": str(p["name"]).strip(), "sku": p.get("sku") or "",
                    "price_cents": int(p.get("price_cents") or 0), "stock": p.get("stock_quantity")})
    return out[:MAX_PRODUCTS]


def _workers(tenant_id: str) -> List[Dict[str, Any]]:
    try:
        rows = (_db().table("commerce_workers").select("id,name,rate_cents,rate_period")
                .eq("tenant_id", tenant_id).eq("active", True).execute().data or [])
    except Exception as exc:  # noqa: BLE001
        log.debug("workers list unreadable: %s", exc)
        rows = []
    return [{"id": w["id"], "name": str(w["name"]).strip(), "rate_cents": int(w.get("rate_cents") or 0),
             "unit": "week" if (w.get("rate_period") or "daily") == "weekly" else "day"}
            for w in sorted(rows, key=lambda w: _key(w.get("name"))) if w.get("name")]


def _projects(tenant_id: str) -> List[str]:
    try:
        from vula.commerce.service import registered_projects
        return sorted({str(p["name"]).strip() for p in registered_projects(tenant_id) if p.get("name")}, key=_key)
    except Exception as exc:  # noqa: BLE001
        log.debug("projects list unreadable: %s", exc)
        return []


def _last_checklist(tenant_id: str) -> List[str]:
    """The items of this business's last site checklist, so the next one starts from its own list."""
    try:
        rows = (_db().table(TABLE).select("spec").eq("tenant_id", tenant_id).eq("form_type", "site_checklist")
                .order("created_at", desc=True).limit(1).execute().data or [])
        return list(((rows[0].get("spec") or {}).get("items") or [])) if rows else []
    except Exception:  # noqa: BLE001
        return []


# ── making a form ────────────────────────────────────────────────────────────

async def make(tenant_id: str, form_type: str, *, project: Optional[str] = None,
               items: Optional[List[str]] = None, by: Optional[str] = None,
               today: Optional[date] = None) -> Dict[str, Any]:
    """Build the form from the books, record it, and return its bytes and file name."""
    import asyncio

    from vula.commerce import boq_template
    if form_type not in TYPES:
        raise FormError("I can make a quote / order form, a site checklist, a timesheet or a stock count.")
    today = today or date.today()
    spec: Dict[str, Any] = {"type": form_type}
    projects = await asyncio.to_thread(_projects, tenant_id)
    spec["projects"] = projects
    if project:
        from vula.commerce.service import canonical_project
        project = canonical_project(tenant_id, project) or project
        if projects and _key(project) not in {_key(p) for p in projects}:
            raise FormError(f"I can't find a project called '{project}'. Which project?")
    if form_type in ("quote", "stock_count"):
        spec["products"] = await _products(tenant_id)
        if form_type == "stock_count":
            spec["products"] = [p for p in spec["products"] if p.get("stock") is not None]
            if not spec["products"]:
                raise FormError("None of your products has a stock count to check yet.")
    if form_type == "timesheet":
        spec["workers"] = await asyncio.to_thread(_workers, tenant_id)
    if form_type == "site_checklist":
        if not project:
            raise FormError("Which project is the site checklist for?")
        given = [re.sub(r"\s+", " ", str(i)).strip()[:120] for i in (items or []) if str(i).strip()]
        spec["items"] = (given or await asyncio.to_thread(_last_checklist, tenant_id) or DEFAULT_CHECKLIST)[:60]
    b = await asyncio.to_thread(boq_template.brand, tenant_id)
    logo = await asyncio.to_thread(boq_template._logo_bytes, b.get("logo_url"))
    spec["vat_registered"] = bool(b.get("vat_registered"))
    form_id = str(uuid.uuid4())
    data, layout = workbook(form_type, form_id, b, logo, spec, project, today)
    spec["layout"] = layout
    row = {"id": form_id, "tenant_id": tenant_id, "form_type": form_type, "project": project, "spec": spec,
           "status": "sent", "created_by": by or None, "created_at": _now()}
    _db().table(TABLE).insert(row).execute()
    back = (_db().table(TABLE).select("id").eq("tenant_id", tenant_id).eq("id", form_id).limit(1).execute().data or [])
    if not back:
        raise RuntimeError("the form was not saved")
    title = TYPES[form_type][0]
    stem = re.sub(r"[^A-Za-z0-9]+", "_", f"{title} {project or ''}").strip("_")
    return {"form_id": form_id, "bytes": data, "title": title,
            "filename": f"{stem}_{today.isoformat()}.xlsx", "project": project}


def workbook(form_type: str, form_id: str, b: Dict[str, Any], logo: Optional[bytes], spec: Dict[str, Any],
             project: Optional[str], today: date) -> Tuple[bytes, Dict[str, Any]]:
    """The form's xlsx and its layout (which cells are filled in, row by row)."""
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Border, Font, PatternFill, Protection, Side
    from openpyxl.worksheet.datavalidation import DataValidation

    from vula.commerce.xlsx_check import safe_text
    accent = (b.get("accent") or "2C5545")
    head_fill = PatternFill("solid", fgColor="FF" + accent)
    fill_in = PatternFill("solid", fgColor="FFFFF2CC")
    thin = Side(style="thin", color="FFBFBFBF")
    box = Border(left=thin, right=thin, top=thin, bottom=thin)
    F = lambda **k: Font(**{"name": "Arial", "size": 10, **k})  # noqa: E731

    wb = Workbook()
    ws = wb.active
    ws.title = TYPES[form_type][0].split(" /")[0][:31]
    lists = wb.create_sheet("Lists")
    mark = wb.create_sheet(MARK)
    mark["A1"], mark["A2"] = "Vula form — please don't change this sheet", form_id
    mark.sheet_state = lists.sheet_state = "hidden"

    def put_list(col: str, title: str, values: List[Any]) -> Optional[str]:
        if not values:
            return None
        lists[f"{col}1"] = title
        for i, v in enumerate(values, start=2):
            safe_text(lists[f"{col}{i}"], v) if isinstance(v, str) else lists.__setitem__(f"{col}{i}", v)
        return f"=Lists!${col}$2:${col}${len(values) + 1}"

    def dropdown(ref: Optional[str], cells: str) -> None:
        if not ref:
            return
        dv = DataValidation(type="list", formula1=ref, allow_blank=True, showErrorMessage=True,
                            errorTitle="Pick from the list", error="Please pick a value from the list.")
        ws.add_data_validation(dv)
        dv.add(cells)

    def input_cell(ref: str, fmt: Optional[str] = None) -> None:
        c = ws[ref]
        c.fill, c.border, c.protection = fill_in, box, Protection(locked=False)
        c.font = F()
        if fmt:
            c.number_format = fmt

    def label(ref: str, text: str, bold: bool = True) -> None:
        safe_text(ws[ref], text)
        ws[ref].font = F(bold=bold)

    # the header: who, what, how
    safe_text(ws["A1"], b.get("display_name") or "")
    ws["A1"].font = F(bold=True, size=14)
    details = " · ".join(x for x in (b.get("legal_name") if b.get("legal_name") != b.get("display_name") else "",
                                     f"Reg {b['reg_no']}" if b.get("reg_no") else "",
                                     f"VAT {b['vat_number']}" if b.get("vat_number") else "",
                                     b.get("address") or "") if x)
    safe_text(ws["A2"], details)
    ws["A2"].font = F(size=8, color="FF6B6B6B")
    safe_text(ws["A3"], TYPES[form_type][0] + (f" — {project}" if project else ""))
    ws["A3"].font = F(bold=True, size=12)
    ws["A4"] = "Fill in the yellow cells, then send this file back on WhatsApp. Nothing is booked until the owner confirms."
    ws["A4"].font = F(italic=True, size=8, color="FF6B6B6B")
    if logo:
        try:
            from openpyxl.drawing.image import Image as XLImage
            img = XLImage(io.BytesIO(logo))
            img.width, img.height = int(img.width * 56 / img.height), 56
            ws.add_image(img, "F1")
        except Exception as exc:  # noqa: BLE001 — the form goes without it
            log.info("form logo not placed: %s", exc)

    first = HEAD + 1
    layout: Dict[str, Any] = {"first": first}

    def heads(cols: List[Tuple[str, int]]) -> None:
        for i, (text, width) in enumerate(cols):
            c = ws.cell(row=HEAD, column=i + 1, value=text)
            c.font, c.fill, c.alignment = F(bold=True, color="FFFFFFFF"), head_fill, Alignment(wrap_text=True)
            ws.column_dimensions[chr(65 + i)].width = width

    money = 'R #,##0.00;[Red]-R #,##0.00'
    if form_type == "quote":
        label("A5", "Customer")
        input_cell("B5")
        label("A6", "Date")
        input_cell("B6", "yyyy-mm-dd")
        ws["B6"] = today
        layout.update(customer="B5", date="B6")
        if spec.get("projects"):
            label("C5", "Project")
            input_cell("D5")
            dropdown(put_list("F", "Projects", spec["projects"]), "D5")
            if project:
                ws["D5"] = project
            layout["project"] = "D5"
        heads([("Item", 40), ("Qty", 10), ("Unit price (R)", 16), ("Line total (R)", 16)])
        last = first + QUOTE_ROWS - 1
        prods = spec.get("products") or []
        ref = put_list("A", "Products", [p["name"] for p in prods])
        put_list("B", "Prices", [p["price_cents"] / 100 for p in prods])
        n = len(prods) + 1
        for r in range(first, last + 1):
            input_cell(f"A{r}")
            input_cell(f"B{r}", "#,##0.###")
            if prods:
                ws[f"C{r}"] = f'=IF(A{r}="","",IFERROR(INDEX(Lists!$B$2:$B${n},MATCH(A{r},Lists!$A$2:$A${n},0)),""))'
                ws[f"C{r}"].number_format = money
            else:
                input_cell(f"C{r}", money)
            ws[f"D{r}"] = f'=IF(OR(B{r}="",C{r}=""),"",ROUND(B{r}*C{r},2))'
            ws[f"D{r}"].number_format = money
        dropdown(ref, f"A{first}:A{last}")
        label(f"C{last + 2}", "Subtotal")
        ws[f"D{last + 2}"] = f"=SUM(D{first}:D{last})"
        ws[f"D{last + 2}"].number_format, ws[f"D{last + 2}"].font = money, F(bold=True)
        ws[f"A{last + 3}"] = ("VAT and the final total are worked out on the quote itself." if spec.get("vat_registered")
                              else "Prices as on your price list.")
        ws[f"A{last + 3}"].font = F(italic=True, size=8)
        layout.update(last=last, item="A", qty="B", price="C", priced=bool(prods))
    elif form_type == "site_checklist":
        label("A5", "Project")
        input_cell("B5")
        dropdown(put_list("F", "Projects", spec.get("projects") or []), "B5")
        ws["B5"] = project
        label("A6", "Date")
        input_cell("B6", "yyyy-mm-dd")
        ws["B6"] = today
        label("C6", "Checked by")
        input_cell("D6")
        layout.update(project="B5", date="B6", checked_by="D6")
        heads([("Item", 46), ("Status", 14), ("Notes", 46)])
        st = put_list("G", "Status", STATUSES)
        items = spec.get("items") or []
        last = first + len(items) + CHECKLIST_EXTRA - 1
        for i, r in enumerate(range(first, last + 1)):
            if i < len(items):
                safe_text(ws[f"A{r}"], items[i])
                ws[f"A{r}"].font = F()
            else:
                input_cell(f"A{r}")
            input_cell(f"B{r}")
            input_cell(f"C{r}")
        dropdown(st, f"B{first}:B{last}")
        layout.update(last=last, item="A", status="B", notes="C", fixed=len(items))
    elif form_type == "timesheet":
        label("A5", "Filled in by")
        input_cell("B5")
        label("A6", "Week starting")
        input_cell("B6", "yyyy-mm-dd")
        layout.update(by="B5", week="B6")
        heads([("Date", 13), ("Worker", 26), ("Project", 24), ("Days (weeks if weekly)", 14),
               ("Rate (R)", 13), ("Per", 8), ("Total (R)", 15)])
        last = first + TIMESHEET_ROWS - 1
        ws_ = spec.get("workers") or []
        wref = put_list("C", "Workers", [w["name"] for w in ws_])
        put_list("D", "Rates", [w["rate_cents"] / 100 for w in ws_])
        put_list("E", "Per", [w["unit"] for w in ws_])
        pref = put_list("F", "Projects", spec.get("projects") or [])
        n = len(ws_) + 1
        for r in range(first, last + 1):
            input_cell(f"A{r}", "yyyy-mm-dd")
            input_cell(f"B{r}")
            input_cell(f"C{r}")
            input_cell(f"D{r}", "#,##0.##")
            if ws_:
                ws[f"E{r}"] = f'=IF(B{r}="","",IFERROR(INDEX(Lists!$D$2:$D${n},MATCH(B{r},Lists!$C$2:$C${n},0)),""))'
                ws[f"F{r}"] = f'=IF(B{r}="","",IFERROR(INDEX(Lists!$E$2:$E${n},MATCH(B{r},Lists!$C$2:$C${n},0)),""))'
            else:
                input_cell(f"E{r}", money)
            ws[f"E{r}"].number_format = money
            ws[f"G{r}"] = f'=IF(OR(D{r}="",E{r}=""),"",ROUND(D{r}*E{r},2))'
            ws[f"G{r}"].number_format = money
        dropdown(wref, f"B{first}:B{last}")
        dropdown(pref, f"C{first}:C{last}")
        label(f"F{last + 2}", "Total")
        ws[f"G{last + 2}"] = f"=SUM(G{first}:G{last})"
        ws[f"G{last + 2}"].number_format, ws[f"G{last + 2}"].font = money, F(bold=True)
        layout.update(last=last, date="A", worker="B", project_col="C", qty="D", rate="E", listed=bool(ws_))
    else:                                                   # stock_count
        label("A5", "Counted by")
        input_cell("B5")
        label("A6", "Date")
        input_cell("B6", "yyyy-mm-dd")
        ws["B6"] = today
        layout.update(by="B5", date="B6")
        heads([("SKU", 14), ("Product", 40), ("In the system", 14), ("Counted", 12), ("Difference", 12)])
        prods = spec.get("products") or []
        last = first + len(prods) - 1
        for i, p in enumerate(prods):
            r = first + i
            safe_text(ws[f"A{r}"], p.get("sku") or "")
            safe_text(ws[f"B{r}"], p["name"])
            ws[f"C{r}"] = p.get("stock")
            input_cell(f"D{r}", "#,##0.###")
            ws[f"E{r}"] = f'=IF(D{r}="","",D{r}-C{r})'
        layout.update(last=last, counted="D", ids=[p["id"] for p in prods])
    ws.freeze_panes = f"A{first}"
    ws.protection.sheet = True                    # locked cells can't be typed over by accident; no password
    ws.protection.formatColumns = ws.protection.formatRows = False
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue(), layout


# ── reading a returned form ──────────────────────────────────────────────────

def form_id_of(path) -> Optional[str]:
    """The id on a Vula form's hidden sheet, or None for any other spreadsheet."""
    from openpyxl import load_workbook
    try:
        wb = load_workbook(path, read_only=True)
        if MARK not in wb.sheetnames:
            return None
        v = str(wb[MARK]["A2"].value or "").strip().lower()
        return v if _ID_RE.match(v) else None
    except Exception as exc:  # noqa: BLE001 — not a form we can read: filed as a normal document
        log.debug("form check skipped: %s", exc)
        return None


def _text(v: Any) -> str:
    return re.sub(r"\s+", " ", str(v)).strip() if v is not None else ""


def _num(v: Any) -> Optional[Decimal]:
    if v is None or v == "":
        return None
    if isinstance(v, bool):
        raise ValueError
    if isinstance(v, (int, float)):
        return Decimal(str(v))
    s = re.sub(r"[\sR,]", "", str(v))
    return Decimal(s)


def _date(v: Any) -> Optional[str]:
    if v in (None, ""):
        return None
    if isinstance(v, datetime):
        return v.date().isoformat()
    if isinstance(v, date):
        return v.isoformat()
    s = str(v).strip()
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%d %b %Y", "%d %B %Y"):
        try:
            return datetime.strptime(s, fmt).date().isoformat()
        except ValueError:
            continue
    raise ValueError


def _find(name: str, options: List[Dict[str, Any]], field: str = "name") -> Optional[Dict[str, Any]]:
    k = _key(name)
    return next((o for o in options if _key(o.get(field)) == k), None)


def read(tenant_id: str, form: Dict[str, Any], path) -> Dict[str, Any]:
    """What a returned form says, checked: {lines, problems, fatal, header, ...}. Read from the
    yellow cells only; every figure worked out again here."""
    from openpyxl import load_workbook
    wb = load_workbook(path, data_only=False)
    ws = next((wb[n] for n in wb.sheetnames if n not in ("Lists", MARK)), wb.active)
    spec, lay, kind = form.get("spec") or {}, (form.get("spec") or {}).get("layout") or {}, form["form_type"]
    first, last = lay.get("first", HEAD + 1), lay.get("last", HEAD)
    out: Dict[str, Any] = {"lines": [], "problems": [], "fatal": None, "header": {}}
    projects = spec.get("projects") or []

    def cell(ref: str) -> Any:
        v = ws[ref].value
        return None if isinstance(v, str) and v.startswith("=") else v

    def project_of(v: Any, row: str) -> Optional[str]:
        t = _text(v)
        if not t:
            return None
        hit = next((p for p in projects if _key(p) == _key(t)), None)
        if not hit:
            out["problems"].append(f"{row}: project “{t}” isn't one of your projects")
        return hit

    if kind == "quote":
        customer = _text(cell(lay["customer"]))
        if not customer:
            out["fatal"] = "The customer's name is missing (cell B5)."
        try:
            out["header"]["date"] = _date(cell(lay["date"])) or date.today().isoformat()
        except ValueError:
            out["problems"].append("The date isn't a real date — today's date is used.")
            out["header"]["date"] = date.today().isoformat()
        out["header"]["customer"] = customer
        if lay.get("project"):
            out["header"]["project"] = project_of(cell(lay["project"]), "Project")
        prods = spec.get("products") or []
        for r in range(first, last + 1):
            item, qty = _text(cell(f"A{r}")), cell(f"B{r}")
            if not item and qty in (None, ""):
                continue
            where = f"Row {r - first + 1}"
            try:
                q = _num(qty)
            except Exception:  # noqa: BLE001
                q = None
            if not q or q <= 0:
                out["problems"].append(f"{where}: the quantity for “{item or '?'}” isn't a number above 0")
                continue
            if lay.get("priced"):
                p = _find(item, prods)
                if not p:
                    out["problems"].append(f"{where}: “{item}” isn't on your product list")
                    continue
                price = p["price_cents"]
                line = {"description": p["name"], "quantity": float(q), "unit_price_cents": price, "product_id": p["id"]}
            else:
                try:
                    pr = _num(cell(f"C{r}"))
                except Exception:  # noqa: BLE001
                    pr = None
                if not item or not pr or pr <= 0:
                    out["problems"].append(f"{where}: needs an item and a price above 0")
                    continue
                price = _cents(pr)
                line = {"description": item[:200], "quantity": float(q), "unit_price_cents": price}
            line["total_cents"] = _cents(q * Decimal(price) / 100)
            out["lines"].append(line)
    elif kind == "site_checklist":
        out["header"]["project"] = project_of(cell(lay["project"]), "Project") or (form.get("project") if not projects else None)
        if not out["header"]["project"]:
            out["fatal"] = "The project (cell B5) isn't one of your projects."
        try:
            out["header"]["date"] = _date(cell(lay["date"])) or date.today().isoformat()
        except ValueError:
            out["header"]["date"] = date.today().isoformat()
        out["header"]["checked_by"] = _text(cell(lay["checked_by"]))
        for r in range(first, last + 1):
            item, status, notes = _text(cell(f"A{r}")), _text(cell(f"B{r}")), _text(cell(f"C{r}"))
            if not item:
                continue
            st = next((s for s in STATUSES if _key(s) == _key(status)), None)
            if status and not st:
                out["problems"].append(f"“{item}”: status “{status}” isn't Done, Not done or N/A")
            out["lines"].append({"item": item[:120], "status": st or "", "notes": notes[:300]})
    elif kind == "timesheet":
        workers = spec.get("workers") or []
        out["header"]["by"] = _text(cell(lay["by"]))
        for r in range(first, last + 1):
            d, w, pj, qty = cell(f"A{r}"), _text(cell(f"B{r}")), cell(f"C{r}"), cell(f"D{r}")
            if not any(_text(x) for x in (d, w, pj, qty)):
                continue
            where = f"Row {r - first + 1}"
            try:
                day = _date(d)
            except ValueError:
                day = None
            if not day:
                out["problems"].append(f"{where}: the date for {w or 'this row'} isn't a real date")
                continue
            try:
                q = _num(qty)
            except Exception:  # noqa: BLE001
                q = None
            if not q or q <= 0 or q > 31:
                out["problems"].append(f"{where}: the days for {w or 'this row'} aren't a number from 0 to 31")
                continue
            proj = project_of(pj, where) if _text(pj) else None
            if _text(pj) and not proj:
                continue
            if lay.get("listed"):
                wk = _find(w, workers)
                if not wk:
                    out["problems"].append(f"{where}: “{w}” isn't one of your workers")
                    continue
                rate, unit, wid, name = wk["rate_cents"], wk["unit"], wk["id"], wk["name"]
            else:
                try:
                    pr = _num(cell(f"E{r}"))
                except Exception:  # noqa: BLE001
                    pr = None
                if not w or not pr or pr <= 0:
                    out["problems"].append(f"{where}: needs a worker and a rate above 0")
                    continue
                rate, unit, wid, name = _cents(pr), "day", None, w[:80]
            out["lines"].append({"date": day, "worker_id": wid, "worker": name, "project": proj,
                                 "quantity": float(q), "unit": unit, "rate_cents": rate,
                                 "total_cents": _cents(q * Decimal(rate) / 100)})
    else:                                                   # stock_count
        ids = lay.get("ids") or []
        names = {p["id"]: p for p in spec.get("products") or []}
        out["header"]["by"] = _text(cell(lay["by"]))
        for i, pid in enumerate(ids):
            r = first + i
            v = cell(f"D{r}")
            if v in (None, ""):
                continue
            p = names.get(pid) or {}
            try:
                q = _num(v)
            except Exception:  # noqa: BLE001
                q = None
            if q is None or q < 0:
                out["problems"].append(f"{p.get('name') or 'A product'}: the count “{v}” isn't a number of 0 or more")
                continue
            out["lines"].append({"product_id": pid, "name": p.get("name") or "", "counted": float(q)})
        if not out["lines"] and not out["problems"]:
            out["fatal"] = "Nothing was counted — fill in the Counted column."
    if not out["lines"] and not out["fatal"]:
        out["fatal"] = "There's nothing to book in it — the yellow cells are empty or every row has a problem."
    return out


async def stage(tenant_id: str, path, phone: str) -> Optional[Dict[str, Any]]:
    """A returned form of THIS business: read, checked and kept as staged, with the preview text.
    None when the file isn't one of this business's forms (it is then filed as a normal document)."""
    import asyncio
    fid = await asyncio.to_thread(form_id_of, path)
    if not fid:
        return None
    rows = (_db().table(TABLE).select("*").eq("tenant_id", tenant_id).eq("id", fid).limit(1).execute().data or [])
    if not rows:
        return None
    form = rows[0]
    title = TYPES[form["form_type"]][0]
    if form["status"] == "booked":
        return {"form": form, "bookable": False,
                "text": f"That {title.lower()} was already booked on {str(form.get('booked_at') or '')[:10]} — "
                        "nothing has been booked twice. Ask me for a fresh form for new figures."}
    if form["status"] == "cancelled":
        return {"form": form, "bookable": False, "text": f"That {title.lower()} was cancelled. Ask me for a fresh one."}
    got = await asyncio.to_thread(read, tenant_id, form, path)
    if form["form_type"] == "stock_count" and got["lines"]:
        current = {p["id"]: p for p in await _products(tenant_id)}
        for ln in got["lines"]:
            held = (current.get(ln["product_id"]) or {}).get("stock")
            ln["system"] = float(held) if held is not None else None
            ln["difference"] = round(ln["counted"] - float(held or 0), 3)
    if got["fatal"]:
        return {"form": form, "bookable": False,
                "text": f"📋 I read your {title.lower()}, but can't book it yet: {got['fatal']}"
                        + ("\n\nAlso: " + "; ".join(got["problems"][:8]) if got["problems"] else "")
                        + "\n\nFix it and send the file again."}
    (_db().table(TABLE).update({"staged": got, "status": "returned", "returned_by": phone, "returned_at": _now()})
     .eq("tenant_id", tenant_id).eq("id", fid).in_("status", ["sent", "returned"]).execute())
    return {"form": form, "bookable": True, "staged": got, "text": preview_text(form, got)}


def preview_text(form: Dict[str, Any], got: Dict[str, Any]) -> str:
    kind, h, lines = form["form_type"], got["header"], got["lines"]
    title = TYPES[kind][0]
    out = [f"📋 *{title}*" + (f" — {h.get('project') or form.get('project')}" if (h.get("project") or form.get("project")) else "")
           + " came back. Here's what I'd book:"]
    if kind == "quote":
        out.append(f"Draft quote for *{h['customer']}*, {h['date']}:")
        out += [f"• {ln['quantity']:g} × {ln['description']} @ {_r(ln['unit_price_cents'])} = {_r(ln['total_cents'])}"
                for ln in lines[:15]]
        out.append(f"Subtotal {_r(sum(ln['total_cents'] for ln in lines))} (VAT is worked out on the quote)")
    elif kind == "site_checklist":
        done = [ln for ln in lines if ln["status"] == "Done"]
        todo = [ln for ln in lines if ln["status"] == "Not done"]
        blank = [ln for ln in lines if not ln["status"]]
        out.append(f"{len(done)} done, {len(todo)} not done, {sum(1 for ln in lines if ln['status'] == 'N/A')} N/A"
                   + (f", {len(blank)} not checked" if blank else "") + (f" — checked by {h['checked_by']}" if h.get("checked_by") else "") + ".")
        out += [f"• Not done: {ln['item']}" + (f" ({ln['notes']})" if ln["notes"] else "") for ln in todo[:15]]
        out.append("It goes on the project's board as a checklist.")
    elif kind == "timesheet":
        per: Dict[str, List[int]] = {}
        for ln in lines:
            t = per.setdefault(ln["worker"], [0, 0])
            t[0] += 1
            t[1] += ln["total_cents"]
        out += [f"• {w}: {n} day(s)/row(s), {_r(c)}" for w, (n, c) in per.items()]
        out.append(f"Pay due on this timesheet: {_r(sum(ln['total_cents'] for ln in lines))} "
                   "(a record of time worked — not booked as a cost; the cost is the payment).")
    else:
        moved = [ln for ln in lines if ln.get("difference")]
        out.append(f"{len(lines)} product(s) counted, {len(moved)} different from the system:")
        out += [f"• {ln['name']}: system {ln['system'] if ln['system'] is not None else '—'}, counted {ln['counted']:g} "
                f"({ln['difference']:+g})" for ln in moved[:15]]
        out.append("Confirming sets each counted product's stock to the count.")
    if got["problems"]:
        out.append("\nLeft out (fix and resend to include them): " + "; ".join(got["problems"][:8]))
    return "\n".join(out)


# ── booking, on the owner's Confirm ──────────────────────────────────────────

async def book(tenant_id: str, form_id: str, by: Optional[str] = None) -> str:
    """Book a staged form, once, and read it back. The status flips returned → booked first, so
    two taps can't book it twice; if booking fails it goes back to returned."""
    rows = (_db().table(TABLE).select("*").eq("tenant_id", tenant_id).eq("id", form_id).limit(1).execute().data or [])
    if not rows:
        raise FormError("I can't find that form.")
    form = rows[0]
    if form["status"] == "booked":
        raise FormError("That form was already booked — nothing was booked twice.")
    if form["status"] != "returned" or not form.get("staged"):
        raise FormError("That form hasn't come back filled in yet.")
    flipped = (_db().table(TABLE).update({"status": "booked", "booked_by": by or None, "booked_at": _now()})
               .eq("tenant_id", tenant_id).eq("id", form_id).eq("status", "returned").execute().data or [])
    if not flipped:
        raise FormError("That form was already booked — nothing was booked twice.")
    try:
        return await _book(tenant_id, form, form["staged"], by)
    except Exception:
        (_db().table(TABLE).update({"status": "returned", "booked_by": None, "booked_at": None})
         .eq("tenant_id", tenant_id).eq("id", form_id).execute())
        raise


async def _book(tenant_id: str, form: Dict[str, Any], got: Dict[str, Any], by: Optional[str]) -> str:
    kind, h, lines, fid = form["form_type"], got["header"], got["lines"], form["id"]
    if kind == "quote":
        from vula.commerce import service
        inv = await service.create_invoice(tenant_id, {
            "doc_type": "quote", "customer_name": h["customer"], "issue_date": h.get("date"),
            "project": h.get("project") or form.get("project"),
            "line_items": [{k: ln[k] for k in ("description", "quantity", "unit_price_cents")} for ln in lines],
            "notes": "From the quote / order form."})
        back = (_db().table("commerce_invoices").select("invoice_number,total_cents,status").eq("tenant_id", tenant_id)
                .eq("id", inv["id"]).limit(1).execute().data or [])
        if not back:
            raise RuntimeError("the quote was not saved")
        q = back[0]
        return (f"✅ Draft quote *{q['invoice_number']}* for {h['customer']}: {_r(int(q['total_cents']))} "
                f"(VAT worked out). Nothing has gone to the customer — say “send {q['invoice_number']}” when it's right.")
    if kind == "stock_count":
        from vula.commerce import stock_counts
        count = await stock_counts.create_count(tenant_id, note="From the stock count form", actor=by)
        await stock_counts.record_scans(tenant_id, count["id"], [
            {"scan_id": f"form:{fid}:{ln['product_id']}", "product_id": ln["product_id"], "set": ln["counted"]}
            for ln in lines], actor=by)
        res = await stock_counts.apply(tenant_id, count["id"], actor=by)
        failed = len(res.get("failed") or [])
        return (f"✅ Stock count applied: {res.get('applied', 0)} product(s) set to the count"
                + (f"; {failed} couldn't be changed — check them in Products." if failed else "."))
    if kind == "timesheet":
        rows = [{"tenant_id": tenant_id, "form_id": fid, "worker_id": ln["worker_id"], "worker_name": ln["worker"],
                 "project": ln["project"], "work_date": ln["date"], "quantity": ln["quantity"], "unit": ln["unit"],
                 "rate_cents": ln["rate_cents"], "total_cents": ln["total_cents"], "created_by": by or None,
                 "created_at": _now()} for ln in lines]
        _db().table(TIMESHEET).insert(rows).execute()
        back = (_db().table(TIMESHEET).select("id,total_cents").eq("tenant_id", tenant_id).eq("form_id", fid)
                .execute().data or [])
        if len(back) != len(rows):
            raise RuntimeError("the timesheet was not saved")
        return (f"✅ Timesheet recorded: {len(back)} row(s), pay due {_r(sum(int(r['total_cents'] or 0) for r in back))}. "
                "Ask “what do I owe the workers this week?” any time.")
    project = h.get("project") or form.get("project")
    items = [{"text": ln["item"] + (f" ({ln['status']})" if ln["status"] == "N/A" else "")
              + (f" — {ln['notes']}" if ln["notes"] else ""), "done": ln["status"] in ("Done", "N/A")} for ln in lines]
    card = {"tenant_id": tenant_id, "project": project, "card_type": "checklist", "x": 40, "y": 40,
            "width": 280, "height": 60 + 22 * min(len(items), 20), "z_index": 1,
            "content": {"title": f"Site checklist {h.get('date') or ''}".strip(), "items": items,
                        "checked_by": h.get("checked_by") or None, "form_id": fid},
            "created_by": by or None}
    res = _db().table("vula_project_cards").insert(card).execute()
    cid = ((res.data or [{}])[0]).get("id")
    back = (_db().table("vula_project_cards").select("id").eq("tenant_id", tenant_id).eq("id", cid)
            .limit(1).execute().data or []) if cid else []
    if not back:
        raise RuntimeError("the checklist was not saved")
    todo = [ln["item"] for ln in lines if ln["status"] == "Not done"]
    return (f"✅ Checklist added to *{project}*'s board." + (f" Still to do: {', '.join(todo[:10])}." if todo else " Everything is done."))


# ── what the timesheets say ──────────────────────────────────────────────────

def pay_due(tenant_id: str, since: Optional[str] = None, until: Optional[str] = None,
            project: Optional[str] = None) -> Dict[str, Any]:
    """Time worked and pay due per worker from confirmed timesheets, for a period."""
    q = _db().table(TIMESHEET).select("worker_name,project,work_date,quantity,unit,total_cents").eq("tenant_id", tenant_id)
    if since:
        q = q.gte("work_date", since)
    if until:
        q = q.lte("work_date", until)
    rows = q.order("work_date").execute().data or []
    if project:
        rows = [r for r in rows if _key(r.get("project")) == _key(project)]
    per: Dict[str, Dict[str, Any]] = {}
    for r in rows:
        w = per.setdefault(r["worker_name"], {"days": 0.0, "cents": 0, "projects": set()})
        w["days"] += float(r.get("quantity") or 0)
        w["cents"] += int(r.get("total_cents") or 0)
        if r.get("project"):
            w["projects"].add(r["project"])
    lines = [f"• {name}: {v['days']:g} day(s), {_r(v['cents'])}" + (f" ({', '.join(sorted(v['projects']))})" if v["projects"] else "")
             for name, v in sorted(per.items())]
    total = sum(v["cents"] for v in per.values())
    when = f"{since or 'the start'} – {until or 'today'}"
    text = (f"👷 Pay due from timesheets, {when}" + (f", {project}" if project else "") + ":\n" + "\n".join(lines)
            + f"\nTotal {_r(total)}. This is what the timesheets say is owed — check it against what's been paid."
            if lines else f"No timesheets recorded for {when}" + (f" on {project}" if project else "") + ".")
    return {"text": text, "total_cents": total, "workers": len(per)}
