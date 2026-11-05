"""vula/migrate/plan.py — what moving a business's books into Vula would bring in, at a cut-over
date. Pure functions over the source's records (Xero's shapes for now); nothing here writes.

The rule that keeps it right: everything is measured AT THE CUT-OVER DATE.
- Opening balances = the source's trial balance at that date, account by account.
- Open items = invoices still owed to the business and bills it still owes AT that date (payments
  and credit-note allocations dated after it don't count), with their original numbers.
- Their totals must equal the source's own debtors / creditors balances at that date — the preview
  says so, or lists the difference. A trial balance whose debits and credits differ is flagged.
- Customers and suppliers are matched to the ones Vula already has (VAT number, then name), never
  duplicated; accounts are mapped to Vula's chart, and the ones with no equivalent are listed as new.
"""
from __future__ import annotations

import difflib
import re
from datetime import date
from typing import Any, Dict, Iterable, List, Optional, Tuple

from vula.xero.client import cents, xdate

OPEN_STATUSES = {"AUTHORISED", "PAID", "SUBMITTED"}

# Vula's own accounts (vula/commerce/accounting.py DEFAULT_CHART) by name keywords, checked in order.
_EXPENSE_WORDS: List[Tuple[str, Tuple[str, ...]]] = [
    ("wages", ("salar", "wage", "payroll", "staff cost")),
    ("subcontractors", ("subcontract", "sub-contract")),
    ("casual_labour", ("casual", "labour")),
    ("rent", ("rent", "lease")),
    ("fuel", ("fuel", "petrol", "diesel")),
    ("bank_charges", ("bank charge", "bank fee", "bank service")),
    ("insurance", ("insurance",)),
    ("marketing", ("marketing", "advertis", "promotion")),
    ("utilities", ("electric", "water", "utilit", "telephone", "internet", "cellphone", "municipal")),
    ("professional_fees", ("accounting", "audit", "legal", "consult", "professional")),
    ("delivery", ("freight", "courier", "delivery", "transport", "postage")),
    ("packaging", ("packag",)),
    ("equipment", ("equipment", "tools", "computer")),
]
_TYPE_DEFAULT = {"REVENUE": "sales", "SALES": "sales", "OTHERINCOME": "other_income",
                 "DIRECTCOSTS": "cost_of_sales", "EXPENSE": "other_expense", "OVERHEADS": "other_expense"}
_CLASS_TYPE = {"ASSET": "asset", "LIABILITY": "liability", "EQUITY": "equity",
               "REVENUE": "income", "EXPENSE": "expense"}


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", (text or "").lower()).strip("_")[:40]


def map_account(acc: Dict[str, Any]) -> Dict[str, Any]:
    """One source account → the Vula account it lands on: an existing default, or a new one."""
    name, code = acc.get("Name") or "", acc.get("Code") or ""
    low, typ, sysacc = name.lower(), (acc.get("Type") or "").upper(), (acc.get("SystemAccount") or "").upper()
    hit = None
    if sysacc == "DEBTORS":
        hit = ("accounts_receivable", "Accounts receivable", "asset", True)
    elif sysacc == "CREDITORS":
        hit = ("accounts_payable", "Accounts payable", "liability", False)
    elif sysacc in ("GST", "VAT"):
        hit = ("vat_output", "VAT output (owed)", "liability", False)
    elif typ == "BANK":
        hit = ("bank_cash", "Bank / cash", "asset", False)
    elif typ == "EQUITY" and ("drawing" in low or "withdraw" in low):
        hit = ("owner_drawings", "Owner drawings", "equity", False)
    elif typ in ("EXPENSE", "OVERHEADS"):
        code_hit = next((c for c, words in _EXPENSE_WORDS if any(w in low for w in words)), None)
        if code_hit:
            hit = (code_hit, None, "expense", False)
    if hit is None and typ in _TYPE_DEFAULT and not (typ in ("EXPENSE", "OVERHEADS")):
        hit = (_TYPE_DEFAULT[typ], None, _CLASS_TYPE.get((acc.get("Class") or "").upper(), "expense"), False)
    if hit is None:   # no Vula equivalent: a new account of the same kind, named as in the source
        vtype = _CLASS_TYPE.get((acc.get("Class") or "").upper(), "asset")
        hit = (f"x_{_slug(code or name)}", name, vtype, True)
    vcode, vname, vtype, new = hit
    return {"source_id": acc.get("AccountID"), "source_code": code, "source_name": name, "source_type": typ,
            "vula_code": vcode, "vula_name": vname or name, "vula_type": vtype, "new": new}


# ── trial balance ────────────────────────────────────────────────────────────────────────────

def _row_cells(report: Dict[str, Any]) -> Iterable[List[Dict[str, Any]]]:
    for section in report.get("Rows") or []:
        for row in section.get("Rows") or []:
            if row.get("RowType") == "Row":
                yield row.get("Cells") or []


def trial_balance(report: Dict[str, Any]) -> Dict[str, int]:
    """{source account id: balance in cents, debit positive} from a TrialBalance report. With the
    YTD columns present (5 cells) the year-to-date / as-at balance is used."""
    out: Dict[str, int] = {}
    for cells in _row_cells(report):
        if len(cells) < 3:
            continue
        acc_id = next((a.get("Value") for a in (cells[0].get("Attributes") or []) if a.get("Id") == "account"), None)
        if not acc_id:
            continue
        dr, cr = (cells[3], cells[4]) if len(cells) >= 5 else (cells[1], cells[2])
        out[acc_id] = out.get(acc_id, 0) + cents(dr.get("Value")) - cents(cr.get("Value"))
    return out


# ── open items at the cut-over ───────────────────────────────────────────────────────────────

def _credit_allocations(credit_notes: List[Dict[str, Any]], cutover: date) -> Dict[str, int]:
    """Credit-note amounts set against each invoice on or before the cut-over."""
    out: Dict[str, int] = {}
    for cn in credit_notes:
        for a in cn.get("Allocations") or []:
            inv = (a.get("Invoice") or {}).get("InvoiceID")
            d = xdate(a.get("Date")) or xdate(cn.get("Date"))
            if inv and d and d <= cutover:
                out[inv] = out.get(inv, 0) + cents(a.get("Amount"))
    return out


def owed_at(inv: Dict[str, Any], cutover: date, credited: Dict[str, int]) -> int:
    """What was still owed on an invoice/bill at the end of the cut-over date, in cents."""
    if (inv.get("Status") or "").upper() not in OPEN_STATUSES:
        return 0
    d = xdate(inv.get("DateString") or inv.get("Date"))
    if not d or d > cutover:
        return 0
    paid = sum(cents(p.get("Amount")) for p in inv.get("Payments") or []
               if (xdate(p.get("Date")) or cutover) <= cutover)
    applied = sum(cents(x.get("AppliedAmount")) for k in ("Prepayments", "Overpayments")
                  for x in inv.get(k) or [] if (xdate(x.get("Date")) or cutover) <= cutover)
    return max(0, cents(inv.get("Total")) - paid - applied - credited.get(inv.get("InvoiceID"), 0))


def credit_left_at(cn: Dict[str, Any], cutover: date, refunds: Dict[str, int]) -> int:
    """Credit on a credit note not yet used or refunded at the cut-over (cents)."""
    if (cn.get("Status") or "").upper() not in OPEN_STATUSES:
        return 0
    d = xdate(cn.get("DateString") or cn.get("Date"))
    if not d or d > cutover:
        return 0
    used = sum(cents(a.get("Amount")) for a in cn.get("Allocations") or []
               if (xdate(a.get("Date")) or d) <= cutover)
    return max(0, cents(cn.get("Total")) - used - refunds.get(cn.get("CreditNoteID"), 0))


def _refunds(payments: List[Dict[str, Any]], cutover: date) -> Dict[str, int]:
    out: Dict[str, int] = {}
    for p in payments:
        cn = (p.get("CreditNote") or {}).get("CreditNoteID")
        if cn and (xdate(p.get("Date")) or cutover) <= cutover:
            out[cn] = out.get(cn, 0) + cents(p.get("Amount"))
    return out


def open_items(invoices: List[Dict[str, Any]], credit_notes: List[Dict[str, Any]],
               payments: List[Dict[str, Any]], cutover: date) -> Dict[str, Any]:
    credited, refunds = _credit_allocations(credit_notes, cutover), _refunds(payments, cutover)
    rec, pay, history = [], [], 0
    for inv in invoices:
        owed = owed_at(inv, cutover, credited)
        d = xdate(inv.get("DateString") or inv.get("Date"))
        if not owed:
            if d and d <= cutover:
                history += 1
            continue
        item = {"source_id": inv.get("InvoiceID"), "number": inv.get("InvoiceNumber") or "",
                "contact": (inv.get("Contact") or {}).get("Name") or "", "date": d.isoformat() if d else None,
                "due": (xdate(inv.get("DueDateString") or inv.get("DueDate")) or d).isoformat() if d else None,
                "total_cents": cents(inv.get("Total")), "owed_cents": owed,
                "currency": inv.get("CurrencyCode") or "ZAR"}
        (rec if (inv.get("Type") or "").upper() == "ACCREC" else pay).append(item)
    for cn in credit_notes:
        left = credit_left_at(cn, cutover, refunds)
        if not left:
            continue
        d = xdate(cn.get("DateString") or cn.get("Date"))
        item = {"source_id": cn.get("CreditNoteID"), "number": cn.get("CreditNoteNumber") or "",
                "contact": (cn.get("Contact") or {}).get("Name") or "", "date": d.isoformat() if d else None,
                "due": d.isoformat() if d else None, "total_cents": -cents(cn.get("Total")),
                "owed_cents": -left, "currency": cn.get("CurrencyCode") or "ZAR", "credit_note": True}
        (rec if (cn.get("Type") or "").upper() == "ACCRECCREDIT" else pay).append(item)
    return {"receivables": rec, "payables": pay, "history_documents": history}


# ── customers and suppliers ──────────────────────────────────────────────────────────────────

_SUFFIX = re.compile(r"\b(\(?pty\)?|ltd|limited|cc|inc|t/a|trading as)\b\.?", re.I)


def norm_name(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", _SUFFIX.sub(" ", (name or "").lower())).strip()


def _digits(s: Optional[str]) -> str:
    return re.sub(r"\D", "", s or "")


def _phone(contact: Dict[str, Any]) -> str:
    for p in contact.get("Phones") or []:
        d = _digits(f"{p.get('PhoneCountryCode') or ''}{p.get('PhoneAreaCode') or ''}{p.get('PhoneNumber') or ''}")
        if len(d) >= 9:
            return d[-9:]
    return ""


def match_contact(contact: Dict[str, Any], existing: List[Dict[str, Any]], *, supplier: bool) -> Dict[str, Any]:
    """A source contact against Vula's suppliers (or customers): 'same' (VAT number, email, phone
    or exact name), 'probably' (very close name — shown, never merged automatically) or 'new'."""
    name = contact.get("Name") or ""
    tax, email, phone = _digits(contact.get("TaxNumber")), (contact.get("EmailAddress") or "").lower(), _phone(contact)
    key = norm_name(name)
    for e in existing:
        if supplier and tax and len(tax) >= 6 and _digits(e.get("tax_id")) == tax:
            return {"match": "same", "by": "VAT number", "vula_id": e.get("id"), "vula_name": e.get("name")}
    for e in existing:
        names = [e.get("name") or ""] + list(e.get("aliases") or [])
        if key and any(norm_name(n) == key for n in names):
            return {"match": "same", "by": "name", "vula_id": e.get("id"), "vula_name": e.get("name")}
        if email and email == (e.get("email") or e.get("contact_email") or "").lower():
            return {"match": "same", "by": "email", "vula_id": e.get("id"), "vula_name": e.get("name")}
        if phone and phone == _digits(e.get("phone") or e.get("contact_phone"))[-9:]:
            return {"match": "same", "by": "phone", "vula_id": e.get("id"), "vula_name": e.get("name")}
    best, score = None, 0.0
    for e in existing:
        r = difflib.SequenceMatcher(None, key, norm_name(e.get("name") or "")).ratio()
        if r > score:
            best, score = e, r
    if best and score >= 0.92:
        return {"match": "probably", "by": f"similar name ({score:.0%})", "vula_id": best.get("id"),
                "vula_name": best.get("name")}
    return {"match": "new"}


# ── the preview ──────────────────────────────────────────────────────────────────────────────

def build(*, org: Dict[str, Any], cutover: date, accounts: List[Dict[str, Any]], tb_report: Dict[str, Any],
          contacts: List[Dict[str, Any]], items: List[Dict[str, Any]], invoices: List[Dict[str, Any]],
          credit_notes: List[Dict[str, Any]], payments: List[Dict[str, Any]],
          bank_transactions: List[Dict[str, Any]], manual_journals: List[Dict[str, Any]],
          vula_suppliers: List[Dict[str, Any]], vula_customers: List[Dict[str, Any]],
          vula_products: List[Dict[str, Any]]) -> Dict[str, Any]:
    warnings: List[str] = []
    mapped = {m["source_id"]: m for m in (map_account(a) for a in accounts if a.get("AccountID"))}
    tb = trial_balance(tb_report)
    unknown_tb = [aid for aid in tb if aid not in mapped]
    if unknown_tb:
        warnings.append(f"{len(unknown_tb)} trial-balance line(s) name an account Xero didn't list.")
    by_vula: Dict[str, Dict[str, Any]] = {}
    for aid, bal in tb.items():
        m = mapped.get(aid) or {"vula_code": "x_unknown", "vula_name": "Unknown account", "new": True}
        row = by_vula.setdefault(m["vula_code"], {"vula_code": m["vula_code"], "vula_name": m["vula_name"],
                                                  "new": m["new"], "balance_cents": 0, "from": []})
        row["balance_cents"] += bal
        row["from"].append((mapped.get(aid) or {}).get("source_name") or aid)
    debits = sum(b for b in tb.values() if b > 0)
    credits = -sum(b for b in tb.values() if b < 0)
    if debits != credits:
        warnings.append(f"Xero's trial balance doesn't balance at the cut-over (debits R{debits/100:,.2f}, "
                        f"credits R{credits/100:,.2f}) — check it in Xero before moving.")

    opens = open_items(invoices, credit_notes, payments, cutover)
    rec_total = sum(i["owed_cents"] for i in opens["receivables"])
    pay_total = sum(i["owed_cents"] for i in opens["payables"])
    sysbal = {(a.get("SystemAccount") or "").upper(): tb.get(a.get("AccountID"), 0) for a in accounts
              if a.get("SystemAccount")}
    debtors, creditors = sysbal.get("DEBTORS"), sysbal.get("CREDITORS")
    foreign = [i for i in opens["receivables"] + opens["payables"] if i["currency"] != "ZAR"]
    if foreign:
        warnings.append(f"{len(foreign)} open item(s) aren't in rand — they'll be listed for you to check.")

    customers = [c for c in contacts if c.get("IsCustomer")]
    suppliers = [c for c in contacts if c.get("IsSupplier")]
    seen_rec = {(i["contact"] or "").lower() for i in opens["receivables"]}
    seen_pay = {(i["contact"] or "").lower() for i in opens["payables"]}
    customers += [c for c in contacts if not c.get("IsCustomer") and (c.get("Name") or "").lower() in seen_rec]
    suppliers += [c for c in contacts if not c.get("IsSupplier") and (c.get("Name") or "").lower() in seen_pay]
    sup_m = [{"name": c.get("Name"), **match_contact(c, vula_suppliers, supplier=True)} for c in suppliers]
    cus_m = [{"name": c.get("Name"), **match_contact(c, vula_customers, supplier=False)} for c in customers]
    prod_names = {norm_name(p.get("name") or "") for p in vula_products}

    def tally(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
        return {"same": sum(r["match"] == "same" for r in rows), "probably": sum(r["match"] == "probably" for r in rows),
                "new": sum(r["match"] == "new" for r in rows),
                "check": [r for r in rows if r["match"] == "probably"][:50]}

    before = [t for t in bank_transactions if (xdate(t.get("DateString") or t.get("Date")) or cutover) <= cutover]
    return {
        "source": "xero", "organisation": org.get("Name") or "", "cutover": cutover.isoformat(),
        "base_currency": org.get("BaseCurrency") or "ZAR",
        "counts": {"accounts": len(accounts), "customers": len(customers), "suppliers": len(suppliers),
                   "items": len(items), "invoices_and_bills": len(invoices), "credit_notes": len(credit_notes),
                   "payments": len(payments), "bank_transactions": len(bank_transactions),
                   "manual_journals": len(manual_journals)},
        "accounts": sorted(mapped.values(), key=lambda m: (m["vula_type"], m["source_code"] or m["source_name"])),
        "new_accounts": sorted({m["vula_code"] for m in mapped.values() if m["new"]}),
        "opening_balances": sorted(by_vula.values(), key=lambda r: r["vula_code"]),
        "trial_balance": {"debits_cents": debits, "credits_cents": credits, "balanced": debits == credits},
        "receivables": {"count": len(opens["receivables"]), "total_cents": rec_total, "xero_debtors_cents": debtors,
                        "agrees": debtors is not None and debtors == rec_total, "items": opens["receivables"][:200]},
        "payables": {"count": len(opens["payables"]), "total_cents": pay_total,
                     "xero_creditors_cents": (-creditors if creditors is not None else None),
                     "agrees": creditors is not None and -creditors == pay_total, "items": opens["payables"][:200]},
        "customers": tally(cus_m), "suppliers": tally(sup_m),
        "items": {"same": sum(norm_name(i.get("Name") or i.get("Code") or "") in prod_names for i in items),
                  "new": sum(norm_name(i.get("Name") or i.get("Code") or "") not in prod_names for i in items)},
        "history": {"documents": opens["history_documents"], "bank_transactions": len(before),
                    "manual_journals": len(manual_journals)},
        "warnings": warnings,
    }
