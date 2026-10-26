"""A supplier's record kept up to date from their own invoices (2026-10-25).

Ian: "is Vula allocating details to the supplier when we scan invoices?" It linked each invoice
to a supplier, but never wrote anything back: across the three live businesses no supplier had a
phone or email, few had a VAT number, and there was nowhere to keep bank details.

When an invoice from a known supplier is filed, apply_document_details():
  * fills what the supplier's record is MISSING — VAT number, phone, email, bank details —
    from the invoice's checked fields (each verified to appear on the page and, for a VAT
    number, to be a valid SA one: extraction_quality.verify_identifiers). Nothing already on the
    record is ever overwritten by a document.
  * flags different bank details: an invoice paying into an account other than the one on file
    is the most common invoice fraud. The record is not changed; the finance team is told
    (notify "bank_review"), with only the last 4 digits of each account in the message.
"""
from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from typing import Any, Dict, Optional, Tuple

logger = logging.getLogger(__name__)

BANK_COLUMNS = ("bank_name", "bank_account_name", "bank_account_number", "bank_branch_code",
                "bank_details_doc", "bank_details_at")


def _digits(v: Any) -> str:
    return re.sub(r"\D", "", str(v or ""))


def _blank(v: Any) -> bool:
    return v is None or str(v).strip() == ""


def last4(v: Any) -> str:
    d = _digits(v)
    return f"…{d[-4:]}" if len(d) >= 4 else "…"


def updates_from_document(supplier: Dict[str, Any], fields: Dict[str, Any],
                          filed_document_id: Optional[str] = None, exclude_vats: Optional[set] = None
                          ) -> Tuple[Dict[str, Any], Optional[Dict[str, Any]]]:
    """(column updates for the supplier's blank fields, bank change or None). exclude_vats: VAT
    numbers known not to be any supplier's (the business's own)."""
    from vula.commerce.extraction_quality import valid_sa_vat
    s, f = supplier or {}, fields or {}
    up: Dict[str, Any] = {}
    vat = f.get("tax_id")
    if (_blank(s.get("tax_id")) and valid_sa_vat(vat) and _digits(vat) != _digits(f.get("customer_vat_number"))
            and _digits(vat) not in (exclude_vats or set())):
        up["tax_id"] = str(vat).strip()
    phone = f.get("supplier_phone")
    if _blank(s.get("contact_phone")) and len(_digits(phone)) >= 9:
        up["contact_phone"] = str(phone).strip()
    email = (f.get("supplier_email") or "").strip()
    if _blank(s.get("contact_email")) and re.fullmatch(r"[^@\s]+@[^@\s]+\.[A-Za-z]{2,}", email):
        up["contact_email"] = email
    from vula.commerce.extraction_quality import is_sa_bank
    bank = f.get("supplier_bank") if isinstance(f.get("supplier_bank"), dict) else {}
    if not is_sa_bank(bank.get("bank_name")):
        bank = {}           # an account at no recognised bank is a misread, not bank details
    acct = _digits(bank.get("account_number"))
    change = None
    if acct and len(acct) >= 6:
        on_file = _digits(s.get("bank_account_number"))
        if not on_file:
            up.update({"bank_name": bank.get("bank_name"), "bank_account_name": bank.get("account_name"),
                       "bank_account_number": str(bank["account_number"]).strip(),
                       "bank_branch_code": bank.get("branch_code"),
                       "bank_details_doc": filed_document_id,
                       "bank_details_at": datetime.now(timezone.utc).isoformat()})
        elif on_file != acct:
            change = {"on_file": last4(on_file), "on_invoice": last4(acct),
                      "on_file_bank": s.get("bank_name"), "on_invoice_bank": bank.get("bank_name")}
    return up, change


def apply_document_details(tenant_id: str, supplier: Dict[str, Any], fields: Dict[str, Any],
                           filed_document_id: Optional[str] = None) -> Dict[str, Any]:
    """Write the blanks this invoice fills; return {"filled": [...], "bank_change": ...}."""
    from vula.commerce import service
    up, change = updates_from_document(supplier, fields, filed_document_id)
    out: Dict[str, Any] = {"filled": [], "bank_change": change}
    if not up or not supplier.get("id"):
        return out
    q = lambda vals: (service._client().table("commerce_suppliers").update(vals)  # noqa: E731
                      .eq("tenant_id", tenant_id).eq("id", supplier["id"]).execute())
    try:
        q(up)
    except Exception as exc:
        # Before migration 224 the bank columns don't exist: keep the rest.
        rest = {k: v for k, v in up.items() if k not in BANK_COLUMNS}
        logger.info("supplier bank details not saved (%s)", type(exc).__name__)
        if not rest:
            return out
        q(rest)
        up = rest
    out["filled"] = [k for k in up if k not in ("bank_details_doc", "bank_details_at")]
    return out


def bank_change_message(supplier_name: str, change: Dict[str, Any], amount_cents: Optional[int] = None) -> str:
    amt = f" for R{amount_cents / 100:,.2f}".replace(",", " ") if amount_cents else ""
    was = f"{change.get('on_file_bank') or 'account'} {change['on_file']}"
    now = f"{change.get('on_invoice_bank') or 'account'} {change['on_invoice']}"
    return (f"⚠️ Bank details changed: the invoice from {supplier_name}{amt} asks for payment into "
            f"{now}, but the account on file for them is {was}. Changed bank details are the most "
            f"common invoice fraud — please confirm with {supplier_name} by phone, using a number "
            f"from your own records (not the one on this invoice), before paying.")


async def alert_bank_change(tenant_id: str, supplier_name: str, change: Dict[str, Any],
                            filed_document_id: Optional[str], amount_cents: Optional[int] = None) -> int:
    from vula.integrations.notify import notify_team
    return await notify_team(tenant_id, "bank_review", bank_change_message(supplier_name, change, amount_cents),
                             idem_key=f"bank-change:{filed_document_id or supplier_name}")


def backfill(tenant_id: str, apply: bool = True, own_names: Optional[list] = None) -> Dict[str, Any]:
    """One pass over the supplier bills already filed, newest first: fill each supplier's blanks
    from their own invoices. No alerts — a historical bank difference is counted, not sent. A VAT
    number on 3+ suppliers' invoices is the business's own and is never given to a supplier."""
    from vula import kb_records
    from vula.commerce import service
    db = service._client()
    sups = {s["id"]: dict(s) for s in (db.table("commerce_suppliers").select("*")
                                        .eq("tenant_id", tenant_id).execute().data or [])}
    rows = (db.table("vula_filed_documents").select("id,supplier_id,fields,created_at,category")
            .eq("tenant_id", tenant_id).in_("category", ["Invoice", "Quote / Estimate"])
            .order("created_at", desc=True).limit(5000).execute().data or [])
    # The business's own "supplier" records (its own invoices filed as bills) get nothing.
    mine = {i for i, sup in sups.items() if own_names and service.is_own_name(sup.get("name") or "", own_names)}
    rows = [r for r in rows if r.get("supplier_id") in sups and r.get("supplier_id") not in mine]
    own = kb_records.own_vat_numbers(rows)
    out: Dict[str, Any] = {"tenant": tenant_id, "suppliers": len(sups), "documents": len(rows),
                           "suppliers_updated": 0, "filled": {}, "bank_differences": 0}
    touched = set()
    for r in rows:
        sup = sups[r["supplier_id"]]
        up, change = updates_from_document(sup, r.get("fields") or {}, r["id"], exclude_vats=own)
        if change:
            out["bank_differences"] += 1
        if not up:
            continue
        if apply:
            try:
                db.table("commerce_suppliers").update(up).eq("tenant_id", tenant_id).eq("id", sup["id"]).execute()
            except Exception as exc:  # noqa: BLE001 — before migration 224: no bank columns
                up = {k: v for k, v in up.items() if k not in BANK_COLUMNS}
                if not up:
                    continue
                logger.info("backfill without bank columns (%s)", type(exc).__name__)
                db.table("commerce_suppliers").update(up).eq("tenant_id", tenant_id).eq("id", sup["id"]).execute()
        sup.update(up)
        touched.add(sup["id"])
        for k in up:
            if k not in ("bank_details_doc", "bank_details_at", "bank_name", "bank_account_name",
                         "bank_branch_code"):
                out["filled"][k] = out["filled"].get(k, 0) + 1
    out["suppliers_updated"] = len(touched)
    return out
