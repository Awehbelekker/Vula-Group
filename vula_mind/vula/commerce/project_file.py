"""vula/commerce/project_file.py — one project's documents, in folders and by supplier (3 Nov 2026).

Ian (a building client's project): "I want to share all the invoices for <project> by folder and sorted by
supplier … something we would like to be able to do for other clients … a dashboard with a quick
view." Doing it by hand showed why it needs its own place: the same supplier filed under three
spellings, invoices filed under another project or none, and payments in the bank with no invoice
behind them.

  overview(tenant, project)     the quick view: every document grouped by folder and supplier with
                                counts and totals, plus what looks missing (bank payments on the
                                project with no document, documents that name the project but
                                aren't filed under it, documents without a file).
  build_pack(tenant, project, audience)
                                a zip: <folder>/<supplier>/<date> <number> <amount>.<ext>, and
                                Index.xlsx (one row per document, subtotals per supplier as SUM
                                formulas). The index is read back from the saved file and the zip
                                re-opened before anything is handed out; any difference → nothing.

Audiences: "internal" (everything) and "client" (supplier invoices, quotes, drawings/BOQ/specs —
never statements, proofs of payment, contracts, reports or correspondence). Owner and managers only
(the API route is full-members-only in server.py; the WhatsApp tool is never given to restricted
staff). Every query is tenant-scoped. Money is integer cents.
"""
from __future__ import annotations

import io
import logging
import re
import zipfile
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional, Tuple

log = logging.getLogger(__name__)

AUDIENCES = ("internal", "client")
MAX_PACK_BYTES = 300 * 1024 * 1024          # stop before a pack can exhaust the worker's memory

# (folder, categories, by supplier?, in the client pack?)
FOLDERS: List[Tuple[str, frozenset, bool, bool]] = [
    ("01 Invoices", frozenset({"Invoice", "Delivery Note"}), True, True),
    ("02 Quotes", frozenset({"Quote / Estimate", "Fee Proposal / Schedule"}), True, True),
    ("03 Statements", frozenset({"Account Statement", "Settlement Statement"}), True, False),
    ("04 Proofs of payment", frozenset({"Proof of Payment"}), False, False),
    ("05 Contracts", frozenset({"Contract / Agreement", "Supplier Agreement", "Tender Document"}), True, False),
    ("06 Drawings, BOQ and specifications", frozenset({"Drawing / Plan", "Bill of Quantities (BOQ)",
                                                       "Specification", "Programme / Schedule"}), False, True),
]
OTHER_FOLDER = ("07 Reports and other documents", frozenset(), False, False)
MONEY_FOLDERS = {"01 Invoices", "02 Quotes"}
# Bank lines booked to these accounts are wages and labour: usually paid without an invoice.
LABOUR_CODES = frozenset({"casual_labour", "wages", "salaries", "labour", "subcontractors"})

_CREDIT = re.compile(r"\b(refund|credit\s*note)\b", re.I)
_ISO = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_BAD_CHARS = re.compile(r'[\\/:*?"<>|\r\n\t]+')
_GENERIC = frozenset({"property", "development", "developments", "architecture", "architects", "services",
                      "service", "construction", "trading", "group", "holdings", "the", "and", "of", "t",
                      "a", "pty", "ltd", "cc", "company", "co", "sa", "south", "africa", "demo", "design"})


def _client():
    from vula.commerce.service import _client as c
    return c()


def _r(cents: int) -> str:
    s = f"R{abs(cents) / 100:,.2f}"
    return f"-{s}" if cents < 0 else s


def _clean(text: str, limit: int = 90) -> str:
    out = re.sub(r"\s+", " ", _BAD_CHARS.sub(" ", text or "")).strip(" .")
    return out[:limit].rstrip(" .") or "document"


def _tidy(name: str) -> str:
    """'GARDENS HANDIMAN CENTRE' → 'Gardens Handiman Centre'; mixed case and short acronyms stay."""
    name = re.sub(r"\s+", " ", (name or "").strip())
    if name.isupper() and len(name) > 4:
        name = " ".join(w if len(w) <= 3 and not re.search(r"[AEIOU]", w) else w.capitalize()
                        for w in name.split(" "))
    return name


def _folder_for(category: Optional[str]) -> Tuple[str, frozenset, bool, bool]:
    for f in FOLDERS:
        if (category or "") in f[1]:
            return f
    return OTHER_FOLDER


def _norm(s: str) -> str:
    from vula.commerce.service import _norm_name
    return _norm_name(s or "")


def _words(text: str) -> List[str]:
    toks = re.findall(r"[a-z0-9]+", (text or "").lower())
    return toks + [a + b for a, b in zip(toks, toks[1:])]         # "bella donna" also reads "belladonna"


def mentions_project(text: str, project: str) -> bool:
    """Every word of the project's name appears in the text, give or take one typo (service.
    close_spelling: one letter dropped or doubled, as bank lines often have it). Short words must match exactly."""
    from vula.commerce.service import close_spelling, project_key
    want = [w for w in project_key(project).split() if len(w) >= 3]
    if not want:
        return False
    have = _words(text)
    return all(any(t == w or close_spelling(t, w) for t in have) for w in want)


class _Suppliers:
    """The business's supplier register: one name per supplier, whichever spelling a document used."""

    def __init__(self, rows: List[Dict[str, Any]], business: str):
        self.by_id = {str(r.get("id")): r for r in rows}
        self.by_norm: Dict[str, Dict[str, Any]] = {}
        for r in rows:
            for n in [r.get("name")] + list(r.get("aliases") or []):
                if n and _norm(n):
                    self.by_norm.setdefault(_norm(n), r)
        self.business = business or ""
        self._us = {w for w in _norm(business).split() if len(w) >= 3 and w not in _GENERIC}

    def is_us(self, name: str) -> bool:
        return bool(self._us & {w for w in _norm(name).split() if w not in _GENERIC})

    def label(self, supplier_id: Optional[str], raw: Optional[str]) -> str:
        row = self.by_id.get(str(supplier_id)) if supplier_id else None
        row = row or (self.by_norm.get(_norm(raw)) if raw else None)
        name = (row or {}).get("name") or (raw or "").strip()
        if not name:
            return "Supplier not named"
        if self.is_us(name):
            return f"{self.business} (our documents)" if self.business else "Our documents"
        label = _tidy(name)
        alias = next((a for a in (row or {}).get("aliases") or [] if self._shown_alias(a, label)), None)
        return f"{label} ({alias})" if alias else label

    @staticmethod
    def _shown_alias(alias: str, label: str) -> bool:
        """An alias worth showing next to the name: a different trading name ("Jack Hammer" for
        "Gardens Handiman Centre") — not an email address, a short misspelling ("Dyi"), or another
        spelling of the same name ("Pudlo", "ICES ELIGHTING ELECTRICAL") merged into this supplier."""
        from vula.commerce.supplier_identity import is_email, _TRADE
        if not alias or is_email(alias) or len(re.sub(r"[^a-z0-9]", "", alias.lower())) <= 4:
            return False
        if _norm(alias) in _norm(label):
            return False
        mine = {w for w in _norm(label).split() if len(w) >= 4 and w not in _TRADE}
        return not ({w for w in _norm(alias).split() if len(w) >= 4 and w not in _TRADE} & mine)

    def names_for(self, label: str) -> List[str]:
        """The spellings a bank line might use for a supplier (name + aliases, normalised)."""
        base = label.split(" (")[0]
        row = self.by_norm.get(_norm(base))
        names = [base] + ([row.get("name")] + list(row.get("aliases") or []) if row else [])
        return sorted({_norm(n) for n in names if n and len(_norm(n)) >= 4})


def _amount(doc: Dict[str, Any]) -> Optional[int]:
    v = (doc.get("fields") or {}).get("total_cents")
    try:
        cents = int(v) if v not in (None, "") else None
    except (TypeError, ValueError):
        return None
    if cents is not None and _is_credit(doc):
        cents = -abs(cents)
    return cents


def _is_credit(doc: Dict[str, Any]) -> bool:
    f = doc.get("fields") or {}
    if (doc.get("category") or "") == "Credit Note":
        return True
    return bool(_CREDIT.search(" ".join(str(x or "") for x in (doc.get("filename"), f.get("document_type"),
                                                               f.get("doc_type")))))


def _read_date(doc: Dict[str, Any]) -> Optional[str]:
    f = doc.get("fields") or {}
    for k in ("date", "statement_date", "issue_date"):
        v = str(f.get(k) or "")[:10]
        if _ISO.match(v):
            return v
    return None


def _date(doc: Dict[str, Any]) -> Optional[str]:
    """The document's date, unless its year is more than a year away from when it was filed — a
    slip from last week read as 2022 (seen on photographed slips) would sort and name the file wrongly."""
    v = _read_date(doc)
    filed = str(doc.get("created_at") or "")[:4]
    if v and filed.isdigit() and abs(int(v[:4]) - int(filed)) > 1:
        return None
    return v


def _number(doc: Dict[str, Any]) -> str:
    f = doc.get("fields") or {}
    return str(f.get("document_number") or f.get("invoice_number") or f.get("contract_number") or "").strip()


def _ext(doc: Dict[str, Any]) -> str:
    m = re.search(r"\.([A-Za-z0-9]{2,5})$", (doc.get("filename") or "").strip())
    if m:
        return "." + m.group(1).lower()
    return {"application/pdf": ".pdf", "image/jpeg": ".jpg", "image/png": ".png",
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": ".xlsx"}.get(doc.get("mime") or "", "")


def _pages(make) -> List[Dict[str, Any]]:
    """Every row of a query, page by page in a stable order (PostgREST caps a response at 1,000 rows)."""
    from vula.commerce.ledger import _all_pages
    return _all_pages(lambda: make().order("id"))


def _documents(tenant_id: str, project: str) -> List[Dict[str, Any]]:
    from vula.commerce.service import project_key
    db = _client()
    # every page (10 Nov 2026: PostgREST answers at most 1,000 rows, whatever .limit() asks — DIGG's
    # 1,054th document and any just edited fell off the client's link)
    rows = _pages(lambda: db.table("vula_filed_documents")
                  .select("id,project,category,status,supplier_id,fields,filename,file_url,mime,created_at,summary")
                  .eq("tenant_id", tenant_id))
    key = project_key(project)
    return [r for r in rows if project_key(r.get("project")) == key and (r.get("status") or "") != "duplicate"]


def _unfiled(tenant_id: str) -> List[Dict[str, Any]]:
    db = _client()
    return _pages(lambda: db.table("vula_filed_documents")
                  .select("id,project,category,status,supplier_id,fields,filename,file_url,summary,created_at")
                  .eq("tenant_id", tenant_id).eq("status", "pending_project"))


def _bank_lines(tenant_id: str, project: str) -> List[Dict[str, Any]]:
    from vula.commerce.service import project_key
    db = _client()
    rows = _pages(lambda: db.table("commerce_bank_transactions")
                  .select("id,txn_date,description,reference,amount_cents,direction,account_code,project")
                  .eq("tenant_id", tenant_id).eq("direction", "out"))
    key = project_key(project)
    return [r for r in rows if project_key(r.get("project")) == key]


def _supplier_rows(tenant_id: str) -> List[Dict[str, Any]]:
    try:
        return _pages(lambda: _client().table("commerce_suppliers").select("id,name,aliases")
                      .eq("tenant_id", tenant_id))
    except Exception as exc:  # noqa: BLE001 — group by the printed names instead
        log.warning("project file: supplier register unavailable for %s: %s", tenant_id, exc)
        return []


def organise(docs: List[Dict[str, Any]], suppliers: _Suppliers) -> Dict[str, Any]:
    """Documents → rows with folder, supplier, date, number and amount, sorted folder → supplier →
    date. A later page of a document rides along with its first page; a second copy of the same
    supplier document (same number and amount) is left out and counted."""
    pages: Dict[str, List[Dict[str, Any]]] = {}
    firsts = []
    for d in docs:
        parent = (d.get("fields") or {}).get("page_of")
        (pages.setdefault(str(parent), []) if parent else firsts).append(d)
    rows, seen, dupes = [], set(), 0
    for d in sorted(firsts, key=lambda x: str(x.get("created_at") or "")):
        folder, _cats, by_supplier, client_ok = _folder_for(d.get("category"))
        f = d.get("fields") or {}
        supplier = suppliers.label(d.get("supplier_id"), f.get("supplier") or f.get("supplier_name"))
        number, amount = _number(d), _amount(d)
        if folder in MONEY_FOLDERS and number:
            k = (folder, supplier, re.sub(r"[^a-z0-9]", "", number.lower()), amount)
            if k in seen:
                dupes += 1
                continue
            seen.add(k)
        date, read = _date(d), _read_date(d)
        rows.append({"id": d["id"], "folder": folder, "by_supplier": by_supplier,
                     # an internal cost sheet never reaches a client, whatever its category (data_checks),
                     # nor a document the owner took off the link (a quote a later one replaced)
                     "client": client_ok and not f.get("_internal") and not f.get("_hidden_from_client"),
                     "supplier": supplier, "date": date, "number": number, "amount_cents": amount,
                     "date_note": f"date read as {read} — check" if read and not date else "",
                     "credit": _is_credit(d), "category": d.get("category") or "", "filename": d.get("filename") or "",
                     "file_url": d.get("file_url"), "ext": _ext(d),
                     "pages": [{"id": p["id"], "file_url": p.get("file_url"), "ext": _ext(p),
                                "page": (p.get("fields") or {}).get("page")}
                               for p in sorted(pages.get(str(d["id"]), []),
                                               key=lambda p: (p.get("fields") or {}).get("page") or 0)]})
    order = {f[0]: i for i, f in enumerate(FOLDERS + [OTHER_FOLDER])}
    rows.sort(key=lambda r: (order[r["folder"]], r["supplier"].lower() if r["by_supplier"] else "",
                             r["date"] or "9999", r["number"], r["filename"]))
    return {"rows": rows, "duplicates_skipped": dupes}


def _missing(rows: List[Dict[str, Any]], bank: List[Dict[str, Any]], unfiled: List[Dict[str, Any]],
             project: str, suppliers: _Suppliers) -> Dict[str, Any]:
    """What looks missing. Payments: a bank line on the project counts as covered when a document
    on the project has its exact amount or its document number appears in the line; otherwise it
    is listed, with a hint when other documents from that supplier are on file."""
    amounts = {abs(r["amount_cents"]) for r in rows if r["amount_cents"]}
    numbers = {re.sub(r"[^a-z0-9]", "", r["number"].lower()) for r in rows if len(r["number"]) >= 4}
    # the business's own documents are never "the supplier" of a payment (a bank reference like
    # "Paid from <business>'s bank account" names the business, not who was paid)
    labels = sorted({r["supplier"] for r in rows if r["by_supplier"] and r["supplier"] != "Supplier not named"
                     and not r["supplier"].endswith("(our documents)") and r["supplier"] != "Our documents"})
    no_doc, labour_n, labour_c = [], 0, 0
    for b in sorted(bank, key=lambda x: str(x.get("txn_date") or "")):
        cents = abs(int(b.get("amount_cents") or 0))
        if not cents:
            continue
        if (b.get("account_code") or "") in LABOUR_CODES:
            labour_n += 1
            labour_c += cents
            continue
        text = f"{b.get('description') or ''} {b.get('reference') or ''}"
        flat = re.sub(r"[^a-z0-9]", "", text.lower())
        if cents in amounts or any(n and n in flat for n in numbers):
            continue
        normed = f" {_norm(text)} "
        hint = next((lab for lab in labels if any(n in normed for n in suppliers.names_for(lab))), None)
        no_doc.append({"date": str(b.get("txn_date") or "")[:10], "amount_cents": cents,
                       "description": b.get("description") or "", "account_code": b.get("account_code"),
                       "supplier_on_file": hint})
    named = []
    for d in unfiled:
        f = d.get("fields") or {}
        text = " ".join(str(x or "") for x in (d.get("summary"), d.get("filename"), f.get("delivery_address"),
                                               f.get("customer_reference"), f.get("order_number"),
                                               f.get("customer"), f.get("site"), f.get("reference")))
        if mentions_project(text, project):
            named.append({"id": d["id"], "supplier": suppliers.label(d.get("supplier_id"), f.get("supplier")),
                          "number": _number(d), "amount_cents": _amount(d), "date": _date(d),
                          "category": d.get("category"), "filename": d.get("filename")})
    return {"payments_without_document": no_doc,
            "labour_payments": {"count": labour_n, "total_cents": labour_c},
            "documents_naming_project_not_filed": named,
            "documents_without_file": [{"id": r["id"], "supplier": r["supplier"], "number": r["number"],
                                        "filename": r["filename"]} for r in rows if not r["file_url"]]}


def _ours(supplier: str) -> bool:
    """The business's own documents (its invoices to the client) — never added to supplier totals."""
    return supplier.endswith("(our documents)") or supplier == "Our documents"


def _summarise(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    by: Dict[str, Dict[str, Any]] = {}
    for r in rows:
        if not r["by_supplier"]:
            continue
        s = by.setdefault(r["supplier"], {"supplier": r["supplier"], "documents": 0, "invoices": 0,
                                          "invoices_cents": 0, "quotes": 0, "quotes_cents": 0,
                                          "first_date": None, "last_date": None, "folders": {}})
        s["documents"] += 1
        s["folders"][r["folder"]] = s["folders"].get(r["folder"], 0) + 1
        if r["folder"] == "01 Invoices":
            s["invoices"] += 1
            s["invoices_cents"] += r["amount_cents"] or 0
        elif r["folder"] == "02 Quotes":
            s["quotes"] += 1
            s["quotes_cents"] += r["amount_cents"] or 0
        if r["date"]:
            s["first_date"] = min(filter(None, [s["first_date"], r["date"]]))
            s["last_date"] = max(filter(None, [s["last_date"], r["date"]]))
    folders: Dict[str, int] = {}
    for r in rows:
        folders[r["folder"]] = folders.get(r["folder"], 0) + 1
    return {"suppliers": sorted(by.values(), key=lambda s: -abs(s["invoices_cents"])),
            "folders": folders,
            # supplier invoices only: the business's own invoice to the client is money in
            "invoices_cents": sum(s["invoices_cents"] for s in by.values() if not _ours(s["supplier"])),
            "documents": len(rows)}


def overview(tenant_id: str, project: str, business: str = "") -> Dict[str, Any]:
    """The quick view for one project (tenant-scoped reads only)."""
    from vula.commerce.service import canonical_project
    name = canonical_project(tenant_id, project) or project
    sup = _Suppliers(_supplier_rows(tenant_id), business)
    org = organise(_documents(tenant_id, name), sup)
    rows = org["rows"]
    out = {"project": name, "as_of": datetime.now(timezone.utc).date().isoformat(),
           **_summarise(rows), "duplicates_skipped": org["duplicates_skipped"],
           "documents_list": [{k: r[k] for k in ("id", "folder", "supplier", "date", "number", "amount_cents",
                                                 "credit", "category", "filename", "client", "file_url",
                                                 "date_note")}
                              for r in rows]}
    try:
        out["missing"] = _missing(rows, _bank_lines(tenant_id, name), _unfiled(tenant_id), name, sup)
    except Exception as exc:  # noqa: BLE001 — the documents still show; the checks say they didn't run
        log.warning("project file checks failed for %s/%s: %s", tenant_id, name, exc)
        out["missing"] = None
    return out


# ── the pack ──────────────────────────────────────────────────────────────────────────────────

def _paths(rows: List[Dict[str, Any]], root: str) -> Dict[str, List[str]]:
    """Zip path(s) for each row: <root>/<folder>[/<supplier>]/<date> <number> <amount><ext>, with
    later pages as '… page N'. Names never collide; a file shared by two rows is written once."""
    used, by_url, out = set(), {}, {}
    for r in rows:
        parts = [root, r["folder"]] + ([_clean(r["supplier"], 60)] if r["by_supplier"] else [])
        stem_bits = [r["date"] or "undated"]
        stem_bits.append(r["number"] or _clean(re.sub(r"\.[A-Za-z0-9]{2,5}$", "", r["filename"]), 50))
        if r["amount_cents"] is not None and r["folder"] in MONEY_FOLDERS:
            stem_bits.append(_r(r["amount_cents"]))
        stem = _clean(" ".join(stem_bits))
        files = [(r["file_url"], r["ext"], "")] + [(p["file_url"], p["ext"], f" page {p.get('page') or i + 2}")
                                                   for i, p in enumerate(r["pages"])]
        paths = []
        for url, ext, suffix in files:
            if not url:
                continue
            if url in by_url:
                paths.append(by_url[url])
                continue
            base, n = "/".join(parts + [stem + suffix]), 1
            path = base + ext
            while path.lower() in used:
                n += 1
                path = f"{base} ({n}){ext}"
            used.add(path.lower())
            by_url[url] = path
            paths.append(path)
        out[r["id"]] = paths
    return out


def index_workbook(business: str, project: str, audience: str, rows: List[Dict[str, Any]],
                   paths: Dict[str, List[str]], root: str, as_of: str) -> Tuple[bytes, Dict[str, int]]:
    """Index.xlsx: one row per document, a SUM subtotal per supplier and per folder, and the
    invoice and quote totals. Returns (bytes, {cell: cents}) for the read-back."""
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill
    from vula.commerce.xlsx_check import safe_text
    money = '"R"#,##0.00;-"R"#,##0.00;"R0.00"'
    font = lambda **k: Font(**{"name": "Arial", "size": 9, **k})  # noqa: E731
    wb = Workbook()
    ws = wb.active
    ws.title = "Index"
    for col, w in zip("ABCDEFGH", (30, 34, 11, 18, 18, 14, 60, 30)):
        ws.column_dimensions[col].width = w
    safe_text(ws["A1"], f"{project} — project file")
    ws["A1"].font = font(bold=True, size=13)
    safe_text(ws["A2"], f"{business} · {'For the client' if audience == 'client' else 'Internal — all documents'}"
                        f" · as at {as_of}. Amounts as on each document (incl. VAT where shown); credit notes negative.")
    ws["A2"].font = font(italic=True, color="555555")
    head = ["Folder", "Supplier", "Date", "Number", "Type", "Amount (R)", "File", "Note"]
    r = 4
    for i, h in enumerate(head, 1):
        c = ws.cell(r, i, h)
        c.font = font(bold=True, color="FFFFFF")
        c.fill = PatternFill("solid", start_color="1F2933", end_color="1F2933")
    want: Dict[str, int] = {}
    folder_subtotals: Dict[str, List[str]] = {}
    i = 0
    while i < len(rows):
        folder, supplier = rows[i]["folder"], rows[i]["supplier"] if rows[i]["by_supplier"] else None
        j = i
        while j < len(rows) and rows[j]["folder"] == folder and \
                (rows[j]["supplier"] if rows[j]["by_supplier"] else None) == supplier:
            j += 1
        first = r + 1
        for row in rows[i:j]:
            r += 1
            p = paths.get(row["id"]) or []
            rel = p[0][len(root) + 1:] if p else ""
            vals = [row["folder"], row["supplier"] if row["by_supplier"] else "", row["date"] or "",
                    row["number"], row["category"] + (" (credit)" if row["credit"] else "")]
            for k, v in enumerate(vals, 1):
                safe_text(ws.cell(r, k), v)
                ws.cell(r, k).font = font()
            if row["amount_cents"] is not None and folder in MONEY_FOLDERS:
                c = ws.cell(r, 6, row["amount_cents"] / 100)
                c.number_format = money
                c.font = font()
            fc = ws.cell(r, 7)
            safe_text(fc, rel or "(no file on record)")
            fc.font = font(color="1F4E99" if rel else "B42318", underline="single" if rel else None)
            if rel:
                fc.hyperlink = rel
            notes = [row["date_note"]] if row.get("date_note") else []
            if len(p) > 1 and p[1] != p[0]:
                notes.append(f"+{len(p) - 1} more page(s)")
            if sum(1 for q in paths.values() if q and p and q[0] == p[0]) > 1:
                notes.append("same file as another row")
            safe_text(ws.cell(r, 8), "; ".join(notes))
            ws.cell(r, 8).font = font(color="555555")
        if folder in MONEY_FOLDERS:
            r += 1
            lab = f"Subtotal — {supplier}" if supplier else f"Subtotal — {folder}"
            safe_text(ws.cell(r, 2), lab)
            ws.cell(r, 2).font = font(bold=True)
            c = ws.cell(r, 6, f"=SUM(F{first}:F{r - 1})")
            c.number_format = money
            c.font = font(bold=True)
            want[f"F{r}"] = sum(x["amount_cents"] or 0 for x in rows[i:j])
            if not (supplier and _ours(supplier)):
                folder_subtotals.setdefault(folder, []).append(f"F{r}")
        i = j
    r += 1
    for folder in ("01 Invoices", "02 Quotes"):
        if folder not in folder_subtotals:
            continue
        r += 1
        safe_text(ws.cell(r, 2), "Total supplier invoices" if folder == "01 Invoices"
                  else "Total supplier quotes (not added to invoices)")
        ws.cell(r, 2).font = font(bold=True, size=10)
        c = ws.cell(r, 6, "=" + "+".join(folder_subtotals[folder]))
        c.number_format = money
        c.font = font(bold=True, size=10)
        want[f"F{r}"] = sum(x["amount_cents"] or 0 for x in rows if x["folder"] == folder and not _ours(x["supplier"]))
    ws.freeze_panes = "A5"
    ws.auto_filter.ref = f"A4:H{max(r, 5)}"
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue(), want


def build_pack(tenant_id: str, project: str, audience: str = "internal", business: str = "",
               fetch: Optional[Callable[[str], bytes]] = None) -> Tuple[bytes, Dict[str, Any]]:
    """The zip and what's in it. RuntimeError when the index or the zip doesn't read back."""
    from vula.commerce.service import canonical_project
    from vula.commerce.xlsx_check import mismatches
    if audience not in AUDIENCES:
        raise ValueError("audience must be internal or client")
    if fetch is None:
        from vula.storage_links import fetch_sync as fetch
    name = canonical_project(tenant_id, project) or project
    sup = _Suppliers(_supplier_rows(tenant_id), business)
    org = organise(_documents(tenant_id, name), sup)
    rows = [r for r in org["rows"] if audience == "internal" or r["client"]]
    if not rows:
        raise LookupError(f"no documents are filed under {name}")
    as_of = datetime.now(timezone.utc).date().isoformat()
    root = _clean(f"{name} - project file" + (" (client)" if audience == "client" else ""), 80)
    paths = _paths(rows, root)
    urls: Dict[str, str] = {}             # file link → its path in the zip (a shared file once)
    for r in rows:
        files = [x for x in [r["file_url"]] + [pg["file_url"] for pg in r["pages"]] if x]
        for u, p in zip(files, paths[r["id"]]):
            urls.setdefault(u, p)

    def get(item):
        u, p = item
        try:
            return p, fetch(u), None
        except Exception as exc:  # noqa: BLE001 — listed in the index, never silently dropped
            return p, None, str(exc)[:200]

    with ThreadPoolExecutor(max_workers=6) as pool:
        got = list(pool.map(get, urls.items()))
    failed = {p for p, data, err in got if data is None}
    total = sum(len(d) for _p, d, _e in got if d)
    if total > MAX_PACK_BYTES:
        raise RuntimeError(f"the files come to {total // (1024 * 1024)} MB — too big for one download")
    if failed:          # those rows say so in the index instead of pointing at a missing file
        for r in rows:
            paths[r["id"]] = [p for p in paths[r["id"]] if p not in failed]
    index, want = index_workbook(business, name, audience, rows, paths, root, as_of)
    wrong = mismatches(index, want)
    if wrong:
        log.error("project file index read-back failed for %s/%s: %s", tenant_id, name, wrong)
        raise RuntimeError("the index didn't add up to the documents")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr(f"{root}/Index.xlsx", index)
        for p, data, _err in got:
            if data is not None:
                z.writestr(p, data)
    data = buf.getvalue()
    with zipfile.ZipFile(io.BytesIO(data)) as z:          # read back: every file there and intact
        names = set(z.namelist())
        expect = {p for p, d, _e in got if d is not None} | {f"{root}/Index.xlsx"}
        if names != expect or z.testzip() is not None:
            raise RuntimeError("the zip didn't read back complete")
    manifest = {"project": name, "audience": audience, "as_of": as_of, "root": root,
                "documents": len(rows), "files": len(expect) - 1, "files_failed": sorted(failed),
                "bytes": len(data), "duplicates_skipped": org["duplicates_skipped"],
                **{k: v for k, v in _summarise(rows).items() if k in ("suppliers", "folders", "invoices_cents")}}
    return data, manifest


def zip_filename(project: str, audience: str, as_of: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9 _-]+", "", project).strip().replace(" ", "_") or "Project"
    return f"{safe}_project_file{'_client' if audience == 'client' else ''}_{as_of}.zip"


def store_pack(tenant_id: str, data: bytes, manifest: Dict[str, Any]) -> Optional[str]:
    """Upload the zip privately and return a link that lasts a week, or None (e.g. too big for
    storage — the dashboard download has no such limit)."""
    from vula.api.whatsapp import _upload_to_storage
    from vula.commerce.service import project_key
    from vula.storage_links import SHARE_TTL, signed
    key = project_key(manifest["project"]).replace(" ", "_") or "project"
    path = f"{tenant_id}/projects/{key}/" + zip_filename(manifest["project"], manifest["audience"], manifest["as_of"])
    url = _upload_to_storage("documents", path, data, "application/zip")
    return signed(url, SHARE_TTL) if url else None


def summary_text(ov: Dict[str, Any], top: int = 12) -> str:
    """The quick view as WhatsApp text (no markdown links)."""
    lines = [f"📁 *{ov['project']}* — {ov['documents']} documents"]
    for s in ov["suppliers"][:top]:
        bits = []
        if s["invoices"]:
            bits.append(f"{s['invoices']} invoice{'s' if s['invoices'] != 1 else ''} {_r(s['invoices_cents'])}")
        if s["quotes"]:
            bits.append(f"{s['quotes']} quote{'s' if s['quotes'] != 1 else ''}")
        other = s["documents"] - s["invoices"] - s["quotes"]
        if other:
            bits.append(f"{other} other")
        lines.append(f"• {s['supplier']}: " + ", ".join(bits))
    if len(ov["suppliers"]) > top:
        lines.append(f"• …and {len(ov['suppliers']) - top} more suppliers")
    lines.append(f"Supplier invoices in total: {_r(ov['invoices_cents'])}")
    m = ov.get("missing")
    if m:
        gaps = []
        if m["payments_without_document"]:
            tot = sum(x["amount_cents"] for x in m["payments_without_document"])
            gaps.append(f"{len(m['payments_without_document'])} payments in the bank with no document ({_r(tot)})")
        if m["documents_naming_project_not_filed"]:
            gaps.append(f"{len(m['documents_naming_project_not_filed'])} documents that name the project "
                        "but aren't filed under it")
        if m["documents_without_file"]:
            gaps.append(f"{len(m['documents_without_file'])} documents without a file")
        if gaps:
            lines.append("\n⚠️ Missing: " + "; ".join(gaps) + ". The list is on the project page.")
    return "\n".join(lines)
