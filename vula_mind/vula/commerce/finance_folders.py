"""vula/commerce/finance_folders.py — a business's money documents for one financial year, in the folders
an accountant expects (19 Nov 2026).

Ian (DIGG): "is everything in the correct folder so it's easy for an accountant to navigate, like bank
statements labelled per account?" Documents were one flat list, filed by project or nothing. These folders
are worked out in code from what each document is (its category), its own date and, for a bank statement,
its account — nothing is moved:

  01 Bank statements                      › account (name …last4)  (and which months are missing)
  02 Sales invoices and credit notes      › month
  03 Supplier invoices and delivery notes › supplier
  04 Proofs of payment                    › month
  05 Card machine and payment settlements › month
  06 Statements from suppliers and customers › who sent it
  07 Contracts, insurance and legal

A document is in the year when its own date is (a date read more than a year from when it was filed is
not trusted — vula/commerce/project_file._date — and the filing date is used). Copies kept as duplicates,
superseded versions and the later pages of a document aren't listed again. `build_zip` gives the year (or
one folder) as a zip with Index.xlsx, both read back before it is handed out. Owner/manager only; every
query is this business's. Money is integer cents.
"""
from __future__ import annotations

import io
import logging
import re
import zipfile
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta
from typing import Any, Callable, Dict, List, Optional, Tuple

from vula.commerce import project_file as pf
from vula.commerce import statement_files

log = logging.getLogger(__name__)

# (key, title, categories, grouped by)
FOLDERS: List[Tuple[str, str, frozenset, str]] = [
    # the business's own company and tax documents — not of one year, shown in every year (20 Nov 2026)
    ("company", "00 Company and tax", frozenset({"Company Registration", "Tax / SARS", "Compliance Certificate"}),
     "category"),
    ("bank", "01 Bank statements", frozenset({statement_files.CATEGORY}), "account"),
    # invoices the business issued, and the payment certificates its invoices answer (20 Nov 2026)
    ("sales", "02 Sales invoices and credit notes", frozenset({"Payment Certificate"}), "month"),
    ("bills", "03 Supplier invoices and delivery notes", frozenset({"Invoice", "Delivery Note", "Credit Note"}),
     "supplier"),
    ("pops", "04 Proofs of payment", frozenset({"Proof of Payment"}), "month"),
    ("settlements", "05 Card machine and payment settlements", frozenset({"Settlement Statement"}), "month"),
    ("statements", "06 Statements from suppliers and customers", frozenset({"Account Statement"}), "supplier"),
    ("legal", "07 Contracts, insurance and legal", frozenset({"Contract / Agreement", "Supplier Agreement",
                                                              "Insurance", "Legal / Property"}), "category"),
]
TITLES = {k: t for k, t, _c, _g in FOLDERS}
_MONTHS = "January February March April May June July August September October November December".split()
MAX_ZIP_BYTES = pf.MAX_PACK_BYTES


def _client():
    from vula.commerce.service import _client as c
    return c()


def year_bounds(year_start: Optional[str] = None, year_end_month: int = 2) -> Tuple[date, date]:
    """The financial year that starts on `year_start` (default: the one today falls in — 1 March for a
    February year end)."""
    s = (date.fromisoformat(year_start[:10]) if year_start
         else statement_files.year_start(year_end_month=year_end_month))
    return s, date(s.year + 1, s.month, 1) - timedelta(days=1)


def _month_name(ym: str) -> str:
    try:
        y, m = ym.split("-")
        return f"{y}-{m} {_MONTHS[int(m) - 1]}"
    except (ValueError, IndexError):
        return "Date not read"


def _documents(tenant_id: str) -> List[Dict[str, Any]]:
    db = _client()
    return pf._pages(lambda: db.table("vula_filed_documents")
                     .select("id,category,status,doc_status,supplier_id,commerce_invoice_id,fields,filename,file_url,"
                             "mime,created_at,summary")
                     .eq("tenant_id", tenant_id))


def _directions(tenant_id: str, ids: List[str]) -> Dict[str, str]:
    out: Dict[str, str] = {}
    db = _client()
    for i in range(0, len(ids), 150):
        try:
            for r in (db.table("commerce_invoices").select("id,direction").eq("tenant_id", tenant_id)
                      .in_("id", ids[i:i + 150]).execute().data or []):
                out[str(r["id"])] = r.get("direction") or "outbound"
        except Exception:  # noqa: BLE001
            pass
    return out


def _accounts(tenant_id: str) -> Dict[str, str]:
    from vula.commerce.bank_accounts import last4
    try:
        rows = (_client().table("commerce_bank_accounts").select("id,account_number,bank,name,private_to")
                .eq("tenant_id", tenant_id).execute().data or [])
    except Exception:  # noqa: BLE001
        return {}
    return {str(r["id"]): f"{r.get('name') or r.get('bank') or 'Bank'} …{last4(r['account_number'])}"
            for r in rows if not r.get("private_to")}


def _business(tenant_id: str) -> str:
    try:
        from vula.api.tenants import display_name
        return display_name(tenant_id) or ""
    except Exception:  # noqa: BLE001
        return ""


def organise(tenant_id: str, year_start: Optional[str] = None) -> Dict[str, Any]:
    """Every money document of the year in its folder and group, with the list of what's missing."""
    start, end = year_bounds(year_start)
    docs = _documents(tenant_id)
    firsts = [d for d in docs if not (d.get("fields") or {}).get("page_of")
              and (d.get("status") or "") != "duplicate" and (d.get("doc_status") or "current") != "superseded"]
    linked = [str(d["commerce_invoice_id"]) for d in firsts if d.get("commerce_invoice_id")]
    direction = _directions(tenant_id, linked) if linked else {}
    suppliers = pf._Suppliers(pf._supplier_rows(tenant_id), _business(tenant_id))
    accounts = _accounts(tenant_id)
    folders: Dict[str, Dict[str, List[Dict[str, Any]]]] = {k: {} for k, *_ in FOLDERS}
    for d in firsts:
        cat = d.get("category") or ""
        f = d.get("fields") or {}
        key = next((k for k, _t, cats, _g in FOLDERS if cat in cats), None)
        if key is None:
            continue
        when = pf._date(d) or str(d.get("created_at") or "")[:10]
        if key != "company" and (not when or not (start.isoformat() <= when <= end.isoformat())):
            continue
        party = suppliers.label(d.get("supplier_id"), f.get("supplier") or f.get("supplier_name"))
        if key == "bills":
            dirn = direction.get(str(d.get("commerce_invoice_id") or ""))
            if dirn == "outbound" or (dirn is None and party != "Supplier not named" and suppliers.is_us(party)):
                key = "sales"
        grouping = next(g for k, _t, _c, g in FOLDERS if k == key)
        if grouping == "account":
            group = accounts.get(str(f.get("bank_account_id") or ""), "Account not read")
        elif grouping == "supplier":
            group = party
        elif grouping == "category":
            from vula.commerce.company_docs import GROUP_NAMES
            group = GROUP_NAMES.get(cat, cat)
        else:
            group = _month_name(when[:7])
        amount = pf._amount(d)
        label = (d.get("summary") if key == "bank" and d.get("summary") else
                 (d.get("summary") or d.get("filename") or cat)[:120] if key == "company" else
                 " ".join(x for x in (party if key != "bank" else "", pf._number(d)) if x) or cat)
        acct = "".join(ch for ch in str(f.get("account_number") or "") if ch.isdigit())
        if key == "company" and len(acct) >= 4:
            label = f"Account …{acct[-4:]}: {label}"     # one bank letter per account; last 4 digits only
        folders[key].setdefault(group, []).append({
            "id": d["id"], "date": when, "label": statement_files.mask_name(label)[:120], "category": cat,
            "amount_cents": amount if key not in ("bank", "statements") else None,
            "filename": statement_files.mask_name(d.get("filename") or ""), "file_url": d.get("file_url"),
            "ext": pf._ext(d), "official": bool(f.get("official")) if key == "bank" else None})
    out = []
    for key, title, _cats, _g in FOLDERS:
        groups = [{"name": g, "count": len(items),
                   "items": sorted(items, key=lambda x: (x["date"], x["label"]))}
                  for g, items in sorted(folders[key].items(), key=lambda kv: kv[0].lower())]
        out.append({"key": key, "title": title, "count": sum(g["count"] for g in groups), "groups": groups})
    cover = statement_files.coverage(tenant_id, start, None)
    return {"from": start.isoformat(), "to": end.isoformat(), "folders": out, "coverage": cover}


def public(tree: Dict[str, Any]) -> Dict[str, Any]:
    """For the screen: file links signed for an hour."""
    from vula.storage_links import sign_row
    for folder in tree["folders"]:
        for g in folder["groups"]:
            sign_row(g["items"])
            for it in g["items"]:
                it.pop("ext", None)
    return tree


# ── the zip ──────────────────────────────────────────────────────────────────

def _index(business: str, rows: List[Dict[str, Any]], tree: Dict[str, Any]) -> Tuple[bytes, Dict[str, int]]:
    from openpyxl import Workbook
    from openpyxl.styles import Font
    from vula.commerce.xlsx_check import safe_text
    wb = Workbook()
    ws = wb.active
    ws.title = "Index"
    safe_text(ws.cell(1, 1), f"{business or 'Business'} — documents for the year {tree['from']} to {tree['to']}")
    ws.cell(1, 1).font = Font(bold=True, size=12)
    heads = ["Folder", "Group", "Date", "Document", "Amount (R)", "File in this zip", "Original file name"]
    for c, h in enumerate(heads, 1):
        ws.cell(3, c, h).font = Font(bold=True)
    r = 3
    for row in rows:
        r += 1
        for c, v in enumerate((row["folder"], row["group"], row["date"], row["label"]), 1):
            safe_text(ws.cell(r, c), v)
        if row["amount_cents"] is not None:
            ws.cell(r, 5, row["amount_cents"] / 100).number_format = "#,##0.00"
        safe_text(ws.cell(r, 6), row["path"] or "(file could not be fetched)")
        safe_text(ws.cell(r, 7), row["filename"])
    r += 2
    safe_text(ws.cell(r, 4), "Total of the amounts above")
    ws.cell(r, 4).font = Font(bold=True)
    ws.cell(r, 5, f"=SUM(E4:E{r - 2})").number_format = "#,##0.00"
    for col, width in zip("ABCDEFG", (34, 30, 12, 50, 14, 60, 40)):
        ws.column_dimensions[col].width = width
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue(), {f"E{r}": sum(x["amount_cents"] or 0 for x in rows)}


def document_rows(tree: Dict[str, Any], root: str, folder: Optional[str] = None) -> List[Dict[str, Any]]:
    """Each document of the year (or one folder) with its path in a zip under `root`."""
    rows, used = [], set()
    for f in tree["folders"]:
        if folder and f["key"] != folder:
            continue
        for g in f["groups"]:
            for it in g["items"]:
                base = pf._clean(f"{it['date']} {it['label']}", 100)
                path = f"{root}/{f['title']}/{pf._clean(g['name'], 60)}/{base}{it['ext'] or ''}"
                n = 2
                while path in used:
                    path = f"{root}/{f['title']}/{pf._clean(g['name'], 60)}/{base} ({n}){it['ext'] or ''}"
                    n += 1
                used.add(path)
                rows.append({"folder": f["title"], "group": g["name"], "date": it["date"], "label": it["label"],
                             "amount_cents": it["amount_cents"], "filename": it["filename"],
                             "file_url": it["file_url"], "path": path if it["file_url"] else None})
    return rows


def fetch_files(rows: List[Dict[str, Any]], fetch: Optional[Callable[[str], bytes]] = None
                ) -> Tuple[List[Tuple[Dict[str, Any], Optional[bytes], Optional[str]]], List[str]]:
    """Every row's file, 6 at a time. A file that can't be fetched is listed (its row's path set to
    None, so the index says so), never silently dropped. RuntimeError when the files are too big."""
    if fetch is None:
        from vula.storage_links import fetch_sync as fetch

    def get(row):
        try:
            return row, fetch(row["file_url"]), None
        except Exception as exc:  # noqa: BLE001 — listed in the index, never silently dropped
            return row, None, str(exc)[:200]

    with ThreadPoolExecutor(max_workers=6) as pool:
        got = list(pool.map(get, [r for r in rows if r["path"]]))
    failed = [r["path"] for r, data, _e in got if data is None]
    for r, data, _e in got:
        if data is None:
            r["path"] = None
    total = sum(len(d) for _r, d, _e in got if d)
    if total > MAX_ZIP_BYTES:
        raise RuntimeError(f"the files come to {total // (1024 * 1024)} MB — download one folder at a time")
    return got, failed


def build_zip(tenant_id: str, year_start: Optional[str] = None, folder: Optional[str] = None,
              fetch: Optional[Callable[[str], bytes]] = None) -> Tuple[bytes, Dict[str, Any]]:
    """The year's money documents (or one folder) as a zip with Index.xlsx. RuntimeError when the
    index or the zip doesn't read back; LookupError when there's nothing to put in it."""
    from vula.commerce.xlsx_check import mismatches
    if folder and folder not in TITLES:
        raise ValueError("unknown folder")
    tree = organise(tenant_id, year_start)
    business = _business(tenant_id)
    root = pf._clean(f"{business or 'Documents'} - year {tree['from'][:4]}-{tree['to'][2:4]}", 80)
    rows = document_rows(tree, root, folder)
    if not rows:
        raise LookupError("no documents for that year")
    got, failed = fetch_files(rows, fetch)
    index, want = _index(business, rows, tree)
    if mismatches(index, want):
        raise RuntimeError("the index didn't add up to the documents")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr(f"{root}/Index.xlsx", index)
        for r, data, _e in got:
            if data is not None:
                z.writestr(r["path"], data)
    data = buf.getvalue()
    with zipfile.ZipFile(io.BytesIO(data)) as z:          # read back: every file there and intact
        expect = {r["path"] for r, d, _e in got if d is not None} | {f"{root}/Index.xlsx"}
        if set(z.namelist()) != expect or z.testzip() is not None:
            raise RuntimeError("the zip didn't read back complete")
    return data, {"from": tree["from"], "to": tree["to"], "folder": folder, "documents": len(rows),
                  "files": len(expect) - 1, "files_failed": [statement_files.mask_name(p) for p in failed],
                  "bytes": len(data), "root": root}


def zip_filename(manifest: Dict[str, Any]) -> str:
    part = re.sub(r"[^A-Za-z0-9]+", "_", TITLES.get(manifest.get("folder") or "", "") or "all").strip("_")
    return f"Documents_{manifest['from'][:4]}-{manifest['to'][2:4]}_{part}.zip"
