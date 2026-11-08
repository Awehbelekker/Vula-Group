"""vula/migrate/commit.py — the one-off move: bring a business's books in from Xero, check them to the
cent against Xero's own figures, then disconnect from Xero (Move from Xero, step 2; migration 248).

Order, each step safe to run again (every Vula row created here has an id derived from the source
record, so a second run updates the same row instead of adding another):
  1. accounts   — Xero accounts with no Vula equivalent are added to the chart
  2. contacts   — customers and suppliers: matched ones are reused, the rest added
  3. items      — Xero items become reusable invoice lines
  4. opening    — ONE balanced journal from Xero's trial balance at the cut-over date
  5. documents  — every invoice, bill and credit note dated on or before the cut-over, with its
                  original number and lines. Open ones ("migration_open") keep what is still owed;
                  paid ones ("migration_history") are history. None of them is posted to the
                  ledger: their effect is already in the opening journal. A payment recorded on an
                  open one later clears debtors/creditors (vula/commerce/ledger.py), never sales.
  6. archive    — every record read, as Xero gave it (vula_import_records): payments, bank
                  transactions, manual journals included.
  7. check      — the opening journal per account equals Xero's trial balance; what customers owe
                  equals Xero's debtors; what is owed to suppliers equals Xero's creditors; every
                  planned document is there. Any difference → 'failed' with the differences listed.
  8. on success — Vula's access at Xero is revoked and the owner is told on WhatsApp.

It refuses to start when Vula already has posted books on or before the cut-over date (an opening
balance on top of them would count everything twice), or when Xero changed since the preview.
"""
from __future__ import annotations

import logging
import re
import uuid
from datetime import date, datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from vula.migrate import plan, runs
from vula.xero.client import cents, xdate

log = logging.getLogger(__name__)

SOURCE = "xero"
OPEN, HISTORY = "migration_open", "migration_history"
_NS = uuid.UUID("6c1f7f0e-4a1b-4f7a-9d55-2c3f0b6a8e11")
_CHUNK = 100


def _db():
    from vula.commerce.service import _client
    return _client()


def vid(tenant_id: str, kind: str, source_id: str) -> str:
    """The Vula id for a source record — the same every run, so nothing is ever added twice."""
    return str(uuid.uuid5(_NS, f"{tenant_id}|{SOURCE}|{kind}|{source_id}"))


def _chunks(rows: List[Dict[str, Any]]):
    for i in range(0, len(rows), _CHUNK):
        yield rows[i:i + _CHUNK]


def _upsert(table: str, rows: List[Dict[str, Any]], on_conflict: str = "id") -> None:
    for chunk in _chunks(rows):
        _db().table(table).upsert(chunk, on_conflict=on_conflict).execute()


def _map(tenant_id: str, run_id: str, rows: List[Tuple[str, str, str, str]]) -> None:
    """(source_type, source_id, vula_table, vula_id) → vula_import_map, once each."""
    _upsert("vula_import_map", [{"tenant_id": tenant_id, "import_id": run_id, "source": SOURCE, "source_type": st,
                                 "source_id": sid, "vula_table": t, "vula_id": v} for st, sid, t, v in rows],
            on_conflict="tenant_id,source,source_type,source_id")


# ── guards ───────────────────────────────────────────────────────────────────────────────────

def books_before(tenant_id: str, cutover: date) -> int:
    """Journal entries Vula already has on or before the cut-over, other than an opening balance."""
    rows = (_db().table("journal_entries").select("id,source_type").eq("tenant_id", tenant_id)
            .lte("entry_date", cutover.isoformat()).limit(50).execute().data or [])
    return sum(1 for r in rows if r.get("source_type") not in ("opening_balance", "opening_balance_reversal"))


def changed_since(preview: Dict[str, Any], fresh: Dict[str, Any]) -> List[str]:
    out = []
    for key, label in (("receivables", "what customers owe"), ("payables", "what you owe suppliers")):
        if (preview.get(key) or {}).get("total_cents") != fresh[key]["total_cents"]:
            out.append(f"{label} changed in Xero since the preview")
    if (preview.get("trial_balance") or {}).get("debits_cents") != fresh["trial_balance"]["debits_cents"]:
        out.append("Xero's trial balance changed since the preview")
    return out


def blockers(fresh: Dict[str, Any]) -> List[str]:
    out = []
    if not fresh["trial_balance"]["balanced"]:
        out.append("Xero's trial balance doesn't balance at the cut-over date")
    if not fresh["receivables"]["agrees"]:
        out.append("what customers owe doesn't agree with Xero's debtors")
    if not fresh["payables"]["agrees"]:
        out.append("what you owe suppliers doesn't agree with Xero's creditors")
    if any(r["vula_code"] == "x_unknown" for r in fresh["opening_balances"] if r["balance_cents"]):
        out.append("Xero's trial balance names an account Xero didn't list")
    return out


# ── 1. accounts ──────────────────────────────────────────────────────────────────────────────

def bring_accounts(tenant_id: str, run_id: str, fresh: Dict[str, Any]) -> int:
    from vula.commerce import accounting
    have = {a["code"] for a in accounting.ensure_chart(tenant_id)}
    new, seen = [], set()
    for m in fresh["accounts"]:
        code = m["vula_code"]
        if code in have or code in seen:
            continue
        seen.add(code)
        pl = m["vula_type"] in ("income", "expense")
        new.append({"tenant_id": tenant_id, "code": code, "name": m["vula_name"][:80], "type": m["vula_type"],
                    "vat_treatment": "standard" if pl else "none", "deductible": m["vula_type"] == "expense",
                    "sort": 500 + len(new), "active": True})
    if new:
        _upsert("commerce_accounts", new, on_conflict="tenant_id,code")
    _map(tenant_id, run_id, [("account", m["source_id"], "commerce_accounts", m["vula_code"])
                             for m in fresh["accounts"] if m.get("source_id")])
    return len(new)


# ── 2. contacts, 3. items ────────────────────────────────────────────────────────────────────

def _phone(c: Dict[str, Any]) -> Optional[str]:
    for p in c.get("Phones") or []:
        d = re.sub(r"\D", "", f"{p.get('PhoneCountryCode') or ''}{p.get('PhoneAreaCode') or ''}{p.get('PhoneNumber') or ''}")
        if len(d) >= 9:
            return d
    return None


def mark_roles(contacts: List[Dict[str, Any]], documents: List[Dict[str, Any]]) -> None:
    """A contact on a bill is a supplier and on an invoice a customer, even if Xero's flags say
    otherwise — so every moved document has its contact in Vula."""
    roles: Dict[str, set] = {}
    for d in documents:
        cid = (d.get("Contact") or {}).get("ContactID")
        if cid:
            roles.setdefault(cid, set()).add(
                "_is_supplier" if (d.get("Type") or "").upper() in ("ACCPAY", "ACCPAYCREDIT") else "_is_customer")
    for c in contacts:
        for flag in roles.get(c.get("ContactID") or "", ()):
            c[flag] = True


def bring_contacts(tenant_id: str, run_id: str, contacts: List[Dict[str, Any]], fresh_side: Dict[str, Any]) -> Dict[str, Any]:
    """Suppliers and customers: an existing Vula one is reused (VAT number, email, phone, name),
    otherwise added. Returns {xero contact id: (vula supplier id or None, vula customer id or None)}."""
    suppliers, customers = fresh_side["vula_suppliers"], fresh_side["vula_customers"]
    taken = {plan.norm_name(s.get("name") or "") for s in suppliers}
    links: Dict[str, Dict[str, Optional[str]]] = {}
    new_sup, new_cus, maps = [], [], []
    for c in contacts:
        cid, name = c.get("ContactID"), (c.get("Name") or "").strip()
        if not cid or not name:
            continue
        link: Dict[str, Optional[str]] = {"supplier": None, "customer": None}
        if c.get("IsSupplier") or c.get("_is_supplier"):
            m = plan.match_contact(c, suppliers, supplier=True)
            if m["match"] == "same":
                link["supplier"] = m["vula_id"]
            elif plan.norm_name(name) in taken:          # same name differently spelt: reuse, never a clash
                link["supplier"] = next(s["id"] for s in suppliers if plan.norm_name(s.get("name") or "") == plan.norm_name(name))
            else:
                sid = vid(tenant_id, "supplier", cid)
                new_sup.append({"id": sid, "tenant_id": tenant_id, "name": name[:200],
                                "tax_id": (c.get("TaxNumber") or None), "contact_email": c.get("EmailAddress") or None,
                                "contact_phone": _phone(c), "aliases": [], "category": "general",
                                "payment_terms_days": 30, "notes": "Moved from Xero"})
                taken.add(plan.norm_name(name))
                link["supplier"] = sid
            maps.append(("supplier", cid, "commerce_suppliers", link["supplier"]))
        if c.get("IsCustomer") or c.get("_is_customer"):
            m = plan.match_contact(c, customers, supplier=False)
            if m["match"] == "same":
                link["customer"] = m["vula_id"]
            else:
                kid = vid(tenant_id, "customer", cid)
                new_cus.append({"id": kid, "tenant_id": tenant_id, "name": name[:200], "email": c.get("EmailAddress") or None,
                                "phone": _phone(c), "vat_number": c.get("TaxNumber") or None,
                                "notes": "Moved from Xero"})
                link["customer"] = kid
            maps.append(("customer", cid, "commerce_invoice_clients", link["customer"]))
        links[cid] = link
        links["name:" + plan.norm_name(name)] = link
    _upsert("commerce_suppliers", new_sup)
    _upsert("commerce_invoice_clients", new_cus)
    _map(tenant_id, run_id, [m for m in maps if m[3]])
    return {"links": links, "suppliers_added": len(new_sup), "customers_added": len(new_cus)}


def bring_items(tenant_id: str, run_id: str, items: List[Dict[str, Any]], products: List[Dict[str, Any]]) -> int:
    have = {plan.norm_name(p.get("name") or "") for p in products}
    rows = []
    for it in items:
        name = (it.get("Name") or it.get("Code") or "").strip()
        if not it.get("ItemID") or not name or plan.norm_name(name) in have:
            continue
        price = cents((it.get("SalesDetails") or {}).get("UnitPrice"))
        rows.append({"id": vid(tenant_id, "item", it["ItemID"]), "tenant_id": tenant_id, "name": name[:200],
                     "description": (it.get("Description") or None), "unit_price_cents": price,
                     "kind": "product" if (it.get("IsTrackedAsInventory") or it.get("IsPurchased")) else "service",
                     "active": True})
    _upsert("commerce_invoice_items", rows)
    _map(tenant_id, run_id, [("item", r["id"], "commerce_invoice_items", r["id"]) for r in rows])
    return len(rows)


# ── 4. opening balances ──────────────────────────────────────────────────────────────────────

def opening_lines(fresh: Dict[str, Any]) -> List[Dict[str, Any]]:
    lines = []
    for r in fresh["opening_balances"]:
        b = int(r["balance_cents"])
        if b:
            lines.append({"account_code": r["vula_code"], "debit_cents": max(b, 0), "credit_cents": max(-b, 0),
                          "memo": ("From Xero: " + ", ".join(r.get("from") or []))[:200]})
    return lines


def post_opening(tenant_id: str, run_id: str, cutover: date, fresh: Dict[str, Any]) -> None:
    from vula.commerce import ledger
    lines = opening_lines(fresh)
    if sum(l["debit_cents"] for l in lines) != sum(l["credit_cents"] for l in lines):
        raise ValueError("The opening balances don't balance — nothing posted.")
    if lines:
        ledger._post(tenant_id, entry_date=cutover.isoformat(), description=f"Opening balances from Xero at {cutover}",
                     source_type="opening_balance", source_id=run_id, lines=lines)


# ── 5. documents ─────────────────────────────────────────────────────────────────────────────

def _lines(doc: Dict[str, Any]) -> List[Dict[str, Any]]:
    out = []
    for li in doc.get("LineItems") or []:
        qty = li.get("Quantity")
        out.append({"description": (li.get("Description") or li.get("ItemCode") or "")[:500],
                    "quantity": float(qty) if qty not in (None, "") else 1.0,
                    "unit_price_cents": cents(li.get("UnitAmount")), "total_cents": cents(li.get("LineAmount")),
                    "vat_cents": cents(li.get("TaxAmount")), "account": li.get("AccountCode") or None,
                    "item_code": li.get("ItemCode") or None})
    return out


def _last_paid(doc: Dict[str, Any]) -> Optional[str]:
    d = xdate(doc.get("FullyPaidOnDate"))
    if not d:
        dates = [xdate(p.get("Date")) for p in doc.get("Payments") or []]
        dates = [x for x in dates if x]
        d = max(dates) if dates else None
    return d.isoformat() if d else None


def document_rows(tenant_id: str, cutover: date, invoices: List[Dict[str, Any]], credit_notes: List[Dict[str, Any]],
                  payments: List[Dict[str, Any]], links: Dict[str, Dict[str, Optional[str]]],
                  existing_numbers: set) -> List[Dict[str, Any]]:
    """Every invoice, bill and credit note dated on or before the cut-over, as Vula rows."""
    credited = plan._credit_allocations(credit_notes, cutover)
    refunds = plan._refunds(payments, cutover)
    rows = []
    used = set(existing_numbers)

    def number(raw: str, src_id: str) -> str:
        n = (raw or "").strip() or f"XERO-{src_id[:8]}"
        if n.lower() in used:
            n = f"{n} (Xero)"
        used.add(n.lower())
        return n

    for kind, docs in (("invoice", invoices), ("credit_note", credit_notes)):
        for d in docs:
            when = xdate(d.get("DateString") or d.get("Date"))
            if not when or when > cutover:
                continue
            is_cn = kind == "credit_note"
            sid = d.get("CreditNoteID") if is_cn else d.get("InvoiceID")
            xtype = (d.get("Type") or "").upper()
            inbound = xtype in ("ACCPAY", "ACCPAYCREDIT")
            total = cents(d.get("Total"))
            left = plan.credit_left_at(d, cutover, refunds) if is_cn else plan.owed_at(d, cutover, credited)
            contact = d.get("Contact") or {}
            link = (links.get(contact.get("ContactID") or "")
                    or links.get("name:" + plan.norm_name(contact.get("Name") or "")) or {})
            row = {"id": vid(tenant_id, kind, sid), "tenant_id": tenant_id,
                   "invoice_number": number(d.get("CreditNoteNumber") if is_cn else d.get("InvoiceNumber"), sid),
                   "direction": "inbound" if inbound else "outbound", "doc_type": kind,
                   "issue_date": when.isoformat(),
                   "due_date": (xdate(d.get("DueDateString") or d.get("DueDate")) or when).isoformat(),
                   "line_items": _lines(d), "subtotal_cents": cents(d.get("SubTotal")),
                   "vat_cents": cents(d.get("TotalTax")), "total_cents": total,
                   "total_paid_cents": total - left,
                   "status": ("part_paid" if left < total else "sent") if left else "paid",
                   "paid_at": None if left else _last_paid(d),
                   "source": OPEN if left else HISTORY,
                   "notes": f"Moved from Xero ({d.get('Reference') or 'no reference'})"[:300]}
            if inbound:
                row.update({"supplier": contact.get("Name") or "", "supplier_id": link.get("supplier"),
                            "customer_name": None})
            else:
                row.update({"customer_name": contact.get("Name") or "", "customer_email": contact.get("EmailAddress") or None})
            rows.append({**row, "_sid": sid, "_kind": kind, "_left": left, "_cn": is_cn})
    return rows


def bring_documents(tenant_id: str, run_id: str, rows: List[Dict[str, Any]]) -> int:
    clean = [{k: v for k, v in r.items() if not k.startswith("_")} for r in rows]
    _upsert("commerce_invoices", clean)
    _map(tenant_id, run_id, [(r["_kind"], r["_sid"], "commerce_invoices", r["id"]) for r in rows])
    return len(rows)


# ── 5b. attachments (8 Nov 2026) ─────────────────────────────────────────────────────────────
# The PDFs and photos attached to each invoice, bill and credit note in Xero come across too, as
# filed documents linked to the moved document (vula_filed_documents.commerce_invoice_id) in the
# private "documents" bucket. Read after the to-the-cent check passes and before Vula disconnects.
# A file that can't be read (too big, refused) is listed, never fails the move. A move that
# failed its check and is brought in again skips files already brought in (fixed ids). They are part of the move, so a Starter plan's
# document cap does not apply to them.

MAX_ATTACHMENT_BYTES = 20 * 1024 * 1024
MAX_ATTACHMENTS = 2000


async def bring_attachments(tenant_id: str, run_id: str, client, rows: List[Dict[str, Any]],
                            src: Dict[str, Any]) -> Dict[str, Any]:
    from vula.api.whatsapp import _upload_to_storage
    import hashlib
    flagged = {d.get("InvoiceID") for d in src.get("invoices") or [] if d.get("HasAttachments")} | \
              {d.get("CreditNoteID") for d in src.get("credit_notes") or [] if d.get("HasAttachments")}
    todo = [r for r in rows if r["_sid"] in flagged]
    out: Dict[str, Any] = {"attachments": 0, "attachments_skipped": []}
    db = _db()
    done = 0
    for r in todo:
        endpoint = "CreditNotes" if r["_cn"] else "Invoices"
        try:
            atts = await client.attachments(endpoint, r["_sid"])
        except Exception as exc:  # noqa: BLE001
            out["attachments_skipped"].append(f"{r['invoice_number']}: couldn't list its files ({type(exc).__name__})")
            continue
        for att in atts:
            name = att.get("FileName") or "attachment"
            if done >= MAX_ATTACHMENTS:
                out["attachments_skipped"].append(f"{r['invoice_number']} {name}: more than {MAX_ATTACHMENTS} files")
                continue
            fid = vid(tenant_id, "attachment", att.get("AttachmentID") or f"{r['_sid']}:{name}")
            if db.table("vula_filed_documents").select("id").eq("tenant_id", tenant_id).eq("id", fid) \
                    .limit(1).execute().data:
                done += 1
                continue                                   # brought in by an earlier run
            if int(att.get("ContentLength") or 0) > MAX_ATTACHMENT_BYTES:
                out["attachments_skipped"].append(f"{r['invoice_number']} {name}: bigger than 20 MB")
                continue
            try:
                data = await client.attachment(endpoint, r["_sid"], att, MAX_ATTACHMENT_BYTES)
            except Exception as exc:  # noqa: BLE001
                why = str(exc) if isinstance(exc, ValueError) else type(exc).__name__
                out["attachments_skipped"].append(f"{r['invoice_number']} {name}: {why}")
                continue
            mime = att.get("MimeType") or "application/octet-stream"
            safe = re.sub(r"[^A-Za-z0-9._-]+", "_", name)[:120]
            url = _upload_to_storage("documents", f"{tenant_id}/xero/{fid}_{safe}", data, mime)
            if not url:
                out["attachments_skipped"].append(f"{r['invoice_number']} {name}: couldn't be stored")
                continue
            inbound = r["direction"] == "inbound"
            db.table("vula_filed_documents").insert({
                "id": fid, "tenant_id": tenant_id, "filename": name, "mime": mime, "file_url": url,
                "content_hash": hashlib.sha256(data).hexdigest(), "source": "xero", "status": "filed",
                "category": "credit_note" if r["_cn"] else ("supplier_invoice" if inbound else "invoice"),
                "summary": f"{'Bill' if inbound else 'Invoice'} {r['invoice_number']} "
                           f"({r.get('supplier') or r.get('customer_name') or ''}), moved from Xero",
                "commerce_invoice_id": r["id"], "supplier_id": r.get("supplier_id"), "filed_by": "xero move",
                "fields": {"moved_from": "xero", "invoice_number": r["invoice_number"],
                           "document_date": r["issue_date"], "total_cents": r["total_cents"],
                           "supplier": r.get("supplier"), "customer": r.get("customer_name")}}).execute()
            _map(tenant_id, run_id, [("attachment", att.get("AttachmentID") or fid, "vula_filed_documents", fid)])
            out["attachments"] += 1
            done += 1
    out["attachments_skipped"] = out["attachments_skipped"][:50]
    return out


# ── 6. archive ───────────────────────────────────────────────────────────────────────────────

_ARCHIVE = {  # kind: (records key, id field, date field, reference field, amount field)
    "account": ("accounts", "AccountID", None, "Code", None),
    "contact": ("contacts", "ContactID", None, "Name", None),
    "item": ("items", "ItemID", None, "Code", None),
    "invoice": ("invoices", "InvoiceID", "DateString", "InvoiceNumber", "Total"),
    "credit_note": ("credit_notes", "CreditNoteID", "DateString", "CreditNoteNumber", "Total"),
    "payment": ("payments", "PaymentID", "Date", "Reference", "Amount"),
    "bank_transaction": ("bank_transactions", "BankTransactionID", "DateString", "Reference", "Total"),
    "manual_journal": ("manual_journals", "ManualJournalID", "DateString", "Narration", None),
}


def archive(tenant_id: str, run_id: str, src: Dict[str, Any]) -> int:
    rows = []
    for kind, (key, idf, datef, reff, amtf) in _ARCHIVE.items():
        for r in src.get(key) or []:
            if not r.get(idf):
                continue
            d = xdate(r.get(datef) or r.get("Date")) if datef else None
            rows.append({"tenant_id": tenant_id, "import_id": run_id, "source": SOURCE, "kind": kind,
                         "source_id": r[idf], "record_date": d.isoformat() if d else None,
                         "reference": (str(r.get(reff) or "")[:200] or None),
                         "contact": ((r.get("Contact") or {}).get("Name") or None),
                         "amount_cents": cents(r.get(amtf)) if amtf else None, "data": r})
    _upsert("vula_import_records", rows, on_conflict="tenant_id,source,kind,source_id")
    return len(rows)


# ── 7. the check ─────────────────────────────────────────────────────────────────────────────

def check(tenant_id: str, run_id: str, fresh: Dict[str, Any], rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    db = _db()
    problems: List[str] = []
    entry = (db.table("journal_entries").select("id").eq("tenant_id", tenant_id).eq("source_type", "opening_balance")
             .eq("source_id", run_id).limit(1).execute().data or [])
    want = {r["vula_code"]: r["balance_cents"] for r in fresh["opening_balances"] if r["balance_cents"]}
    got: Dict[str, int] = {}
    if entry:
        accounts = {a["id"]: a["code"] for a in (db.table("commerce_accounts").select("id,code")
                                                 .eq("tenant_id", tenant_id).execute().data or [])}
        for l in db.table("journal_lines").select("account_id,debit_cents,credit_cents") \
                .eq("journal_entry_id", entry[0]["id"]).execute().data or []:
            code = accounts.get(l["account_id"], "?")
            got[code] = got.get(code, 0) + int(l.get("debit_cents") or 0) - int(l.get("credit_cents") or 0)
    elif want:
        problems.append("the opening balances weren't posted")
    for code in sorted(set(want) | set(got)):
        if want.get(code, 0) != got.get(code, 0):
            problems.append(f"opening balance for {code}: Xero R{want.get(code, 0)/100:,.2f}, Vula R{got.get(code, 0)/100:,.2f}")
    ids = [r["id"] for r in rows]
    found: Dict[str, Dict[str, Any]] = {}
    for i in range(0, len(ids), 150):
        for r in db.table("commerce_invoices").select("id,direction,doc_type,total_cents,total_paid_cents,source") \
                .eq("tenant_id", tenant_id).in_("id", ids[i:i + 150]).execute().data or []:
            found[r["id"]] = r
    if len(found) != len(ids):
        problems.append(f"{len(ids) - len(found)} document(s) didn't arrive")

    def owed(direction: str) -> int:
        t = 0
        for r in found.values():
            if r.get("direction") == direction and r.get("source") == OPEN:
                left = int(r.get("total_cents") or 0) - int(r.get("total_paid_cents") or 0)
                t += -left if r.get("doc_type") == "credit_note" else left
        return t
    rec, pay = owed("outbound"), owed("inbound")
    if rec != fresh["receivables"]["total_cents"]:
        problems.append(f"customers owe R{rec/100:,.2f} in Vula, R{fresh['receivables']['total_cents']/100:,.2f} in Xero")
    if pay != fresh["payables"]["total_cents"]:
        problems.append(f"owed to suppliers R{pay/100:,.2f} in Vula, R{fresh['payables']['total_cents']/100:,.2f} in Xero")
    return {"ok": not problems, "problems": problems, "receivables_cents": rec, "payables_cents": pay,
            "opening_accounts": len(want), "documents": len(found)}


# ── the run ──────────────────────────────────────────────────────────────────────────────────

def start_commit(tenant_id: str, run_id: str, by: str = "") -> Dict[str, Any]:
    import asyncio
    run = runs.get(tenant_id, run_id)
    if not run:
        raise LookupError("That run wasn't found.")
    if run.get("status") not in ("previewed", "failed") or not (run.get("preview") or {}).get("cutover"):
        raise ValueError("Show the preview first, then bring it in.")
    runs._set(tenant_id, run_id, {"status": "importing", "error": None,
                                  "result": {**(run.get("result") or {}), "started_by": by}})
    task = asyncio.get_event_loop().create_task(run_commit(tenant_id, run_id))
    runs._TASKS.add(task)
    task.add_done_callback(runs._TASKS.discard)
    return {**run, "status": "importing"}


async def run_commit(tenant_id: str, run_id: str, client=None, *, disconnect: bool = True) -> Dict[str, Any]:
    run = runs.get(tenant_id, run_id) or {}
    cutover = date.fromisoformat(str(run.get("cutover_date"))[:10])
    try:
        if books_before(tenant_id, cutover):
            raise ValueError(f"Vula already has books on or before {cutover} — pick a later cut-over date "
                             "(the day after Vula's own records start), or ask Vula's team.")
        from vula.xero.client import XeroClient
        client = client or XeroClient(tenant_id)
        src = await runs.read_xero(tenant_id, cutover, client)
        calls = src.pop("calls", 0)
        side = await runs._vula_side(tenant_id)
        fresh = plan.build(cutover=cutover, **src, **side)
        stop = changed_since(run.get("preview") or {}, fresh) or blockers(fresh)
        if stop:
            raise ValueError("Not brought in: " + "; ".join(stop) + ". Show the preview again.")
        mark_roles(src["contacts"], src["invoices"] + src["credit_notes"])
        accounts_added = bring_accounts(tenant_id, run_id, fresh)
        contacts = bring_contacts(tenant_id, run_id, src["contacts"], side)
        items_added = bring_items(tenant_id, run_id, src["items"], side["vula_products"])
        post_opening(tenant_id, run_id, cutover, fresh)
        rows = document_rows(tenant_id, cutover, src["invoices"], src["credit_notes"], src["payments"],
                             contacts["links"], _existing_numbers(tenant_id))
        docs = bring_documents(tenant_id, run_id, rows)
        archived = archive(tenant_id, run_id, src)
        result = check(tenant_id, run_id, fresh, rows)
        result.update({"accounts_added": accounts_added, "suppliers_added": contacts["suppliers_added"],
                       "customers_added": contacts["customers_added"], "items_added": items_added,
                       "documents": docs, "open_documents": sum(1 for r in rows if r["source"] == OPEN),
                       "history_documents": sum(1 for r in rows if r["source"] == HISTORY),
                       "archived": archived, "xero_calls": calls,
                       "finished_at": datetime.now(timezone.utc).isoformat()})
        if not result["ok"]:
            runs._set(tenant_id, run_id, {"status": "failed", "result": result,
                                          "error": "Brought in, but it doesn't agree with Xero: " + "; ".join(result["problems"][:5])})
            return result
        try:
            result.update(await bring_attachments(tenant_id, run_id, client, rows, src))
        except Exception as exc:  # noqa: BLE001 — the books are in; the files are a bonus
            log.warning("xero attachments for %s stopped: %s", tenant_id, exc)
            result.setdefault("attachments_skipped", []).append(
                "reading the attached files stopped part-way; the books themselves are all in")
        runs._set(tenant_id, run_id, {"status": "done", "result": result, "error": None})
        if disconnect:
            await _finish(tenant_id, fresh, result)
        return result
    except (LookupError, PermissionError, RuntimeError, ValueError) as exc:
        runs._set(tenant_id, run_id, {"status": "failed", "error": str(exc)[:400]})
    except Exception as exc:  # noqa: BLE001 — never leave a run 'importing' forever
        log.warning("xero move %s for %s failed: %s", run_id[:8], tenant_id, exc)
        runs._set(tenant_id, run_id, {"status": "failed", "error": "Something went wrong bringing the books in — try again; "
                                                                   "nothing is added twice."})
    return {}


def _existing_numbers(tenant_id: str) -> set:
    """Document numbers Vula already uses, except the ones this move made (a re-run keeps its own)."""
    rows = (_db().table("commerce_invoices").select("invoice_number,source").eq("tenant_id", tenant_id)
            .execute().data or [])
    return {(r.get("invoice_number") or "").lower() for r in rows if r.get("source") not in (OPEN, HISTORY)}


async def _finish(tenant_id: str, fresh: Dict[str, Any], result: Dict[str, Any]) -> None:
    """Disconnect from Xero (the move is one-off) and tell the owner."""
    try:
        from vula.xero import credentials
        await credentials.disconnect(tenant_id)
    except Exception as exc:  # noqa: BLE001 — the move stands; the connection can be removed by hand
        log.warning("xero disconnect after the move failed for %s: %s", tenant_id, exc)
    try:
        from vula.api.tenants import dashboard_link
        from vula.api.whatsapp import _send_reply
        from vula.connections import _owner_phones
        text = (f"✅ Your books from Xero are in Vula (cut-over {fresh['cutover']}).\n"
                f"{result['documents']} invoices, bills and credit notes ({result['open_documents']} still open), "
                f"opening balances for {result['opening_accounts']} accounts"
                + (f", {result['attachments']} attached files" if result.get("attachments") else "") + ".\n"
                f"Customers owe R{result['receivables_cents']/100:,.2f} and you owe suppliers "
                f"R{result['payables_cents']/100:,.2f} — both agree with Xero to the cent.\n"
                f"Vula has disconnected from Xero. {dashboard_link(tenant_id, 'settings')}")
        for phone in _owner_phones(tenant_id):
            await _send_reply(phone, text, tenant_id=tenant_id, idem_key=f"xero-move:{tenant_id}:{fresh['cutover']}:{phone}")
    except Exception as exc:  # noqa: BLE001
        log.info("xero move message not sent for %s: %s", tenant_id, exc)


# ── undo ─────────────────────────────────────────────────────────────────────────────────────

def undo(tenant_id: str, run_id: str, by: str = "") -> Dict[str, Any]:
    """Take a move back out: its documents are cancelled (kept on record, not deleted) and the
    opening balances reversed. Refused once a payment has been recorded on a moved document.
    Contacts, accounts, items and the archive stay (harmless, and reused by a new run)."""
    from vula.commerce import ledger
    db = _db()
    run = runs.get(tenant_id, run_id)
    if not run or run.get("status") not in ("done", "failed"):
        raise ValueError("Only a move that was brought in can be undone.")
    mapped = (db.table("vula_import_map").select("source_type,vula_id").eq("tenant_id", tenant_id)
              .eq("import_id", run_id).execute().data or [])
    doc_ids = [m["vula_id"] for m in mapped if m["source_type"] in ("invoice", "credit_note")]
    for i in range(0, len(doc_ids), 150):
        paid = (db.table("commerce_invoice_payments").select("id").eq("tenant_id", tenant_id)
                .in_("invoice_id", doc_ids[i:i + 150]).limit(1).execute().data or [])
        if paid:
            raise ValueError("A payment has been recorded on a moved document since — this move can't be undone.")
    for i in range(0, len(doc_ids), 150):
        db.table("commerce_invoices").update({"status": "cancelled", "cancel_reason": f"Move from Xero undone by {by or 'owner'}",
                                              "cancelled_at": datetime.now(timezone.utc).isoformat()}) \
            .eq("tenant_id", tenant_id).in_("id", doc_ids[i:i + 150]).execute()
    entry = (db.table("journal_entries").select("id,entry_date").eq("tenant_id", tenant_id)
             .eq("source_type", "opening_balance").eq("source_id", run_id).limit(1).execute().data or [])
    if entry:
        accounts = {a["id"]: a["code"] for a in (db.table("commerce_accounts").select("id,code")
                                                 .eq("tenant_id", tenant_id).execute().data or [])}
        lines = [{"account_code": accounts.get(l["account_id"]), "debit_cents": int(l.get("credit_cents") or 0),
                  "credit_cents": int(l.get("debit_cents") or 0)}
                 for l in db.table("journal_lines").select("account_id,debit_cents,credit_cents")
                 .eq("journal_entry_id", entry[0]["id"]).execute().data or []]
        ledger._post(tenant_id, entry_date=str(entry[0]["entry_date"])[:10], description="Opening balances from Xero undone",
                     source_type="opening_balance_reversal", source_id=run_id, lines=lines)
    db.table("vula_import_map").delete().eq("tenant_id", tenant_id).eq("import_id", run_id) \
        .in_("source_type", ["invoice", "credit_note"]).execute()
    runs._set(tenant_id, run_id, {"status": "undone", "error": None})
    return {"cancelled": len(doc_ids), "reversed": bool(entry)}
