"""vula/commerce/statement_files.py — every bank statement kept, under its own bank account.

19 Nov 2026 (Ian, DIGG: "is everything in the correct folder so an accountant can navigate — bank
statements labelled per account?"). Until now a statement sent on WhatsApp or uploaded on the dashboard
was read into the bank lines and then thrown away; only email copies were filed, under whatever the
model called them. Of DIGG's 50 statement files, 11 were kept, none said which account they were.

`keep` files the PDF itself (vula_filed_documents, category "Bank Statement", never on a project) with
the account it is for, the bank, the period and the opening and closing balance, read in code from the
statement (vula/ingestion/fnb_statement.py, bank_accounts.account_number). The same file sent twice is
one document; a copy filed earlier under another category is relabelled, not filed again. A statement of
one member's private account is never the business's (vula/private_docs.py) and is left alone here.

`backfill` does the same for the statements already behind the bank lines (the originals still on the
server's volume, and the copies already filed). `coverage` says, per account and month of the financial
year, whether the statement is on file — what to fetch from the bank before an accountant looks.

Only the last four digits of an account number are ever shown or stored on the document.
"""
from __future__ import annotations

import hashlib
import logging
import re
from datetime import date
from pathlib import Path
from typing import Any, Dict, List, Optional

log = logging.getLogger(__name__)

CATEGORY = "Bank Statement"
_MONTHS = "Jan Feb Mar Apr May Jun Jul Aug Sep Oct Nov Dec".split()


def _client():
    from vula.commerce.service import _client as c
    return c()


def _mask(number: Optional[str]) -> Optional[str]:
    return f"…{str(number)[-4:]}" if number else None


def mask_name(name: str) -> str:
    """A file name as a person may see it here: any long run of digits (an account number) masked."""
    return re.sub(r"\d{6,}", lambda m: "…" + m.group(0)[-4:], name or "")


def read_text(tenant_id: str, path: Path, password: Optional[str] = None) -> str:
    from vula.commerce import bank_rec
    from vula.ingestion import fnb_statement
    pwd = password if password is not None else bank_rec.get_statement_password(tenant_id)
    text = ""
    try:
        text = fnb_statement.pdf_text(Path(path), pwd)
    except Exception:  # noqa: BLE001
        text = ""
    if not text.strip():
        try:
            text = bank_rec.extract_pdf_text(Path(path), pwd)
        except Exception:  # noqa: BLE001
            text = ""
    return text


def describe(text: str, txns: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
    """What a statement is, read in code: account number, bank, period, opening and closing balance."""
    from vula.commerce import bank_accounts
    from vula.ingestion import fnb_statement
    out: Dict[str, Any] = {"number": bank_accounts.account_number(text), "bank": bank_accounts.bank_name(text),
                           "period_from": None, "period_to": None, "opening_cents": None, "closing_cents": None}
    exact = None
    try:
        exact = fnb_statement.parse(text)
    except Exception:  # noqa: BLE001
        exact = None
    if exact:
        out.update(opening_cents=exact["opening_cents"], closing_cents=exact["closing_cents"])
        txns = exact["transactions"]
    dates = sorted(str(t.get("date"))[:10] for t in (txns or []) if t.get("date"))
    if dates:
        out.update(period_from=dates[0], period_to=dates[-1])
    return out


def label(info: Dict[str, Any]) -> str:
    """'FNB statement …7663, 1 Mar to 31 Mar 2026' — what the document is called in the folders."""
    def day(d):
        try:
            x = date.fromisoformat(d)
            return f"{x.day} {_MONTHS[x.month - 1]} {x.year}"
        except (TypeError, ValueError):
            return None
    who = " ".join(x for x in (info.get("bank") or "Bank", "statement", _mask(info.get("number")) or "") if x)
    a, b = day(info.get("period_from")), day(info.get("period_to"))
    return f"{who}, {a} to {b}" if a and b else who


def _private(tenant_id: str, number: Optional[str]) -> bool:
    if not number:
        return False
    from vula import private_docs
    try:
        return bool(private_docs.private_accounts(tenant_id).get(number))
    except Exception:  # noqa: BLE001
        return False


def _fields(tenant_id: str, info: Dict[str, Any], official: bool) -> Dict[str, Any]:
    from vula.commerce import bank_accounts
    acct = bank_accounts.remember(tenant_id, info["number"], bank=info.get("bank"),
                                  seen=info.get("period_to")) if info.get("number") else None
    return {"bank_account_id": (acct or {}).get("id"), "account": _mask(info.get("number")),
            "bank": info.get("bank"), "period_from": info.get("period_from"), "period_to": info.get("period_to"),
            "statement_date": info.get("period_to"), "opening_cents": info.get("opening_cents"),
            "closing_cents": info.get("closing_cents"), "official": bool(official)}


def _relabel(tenant_id: str, row: Dict[str, Any], fields: Dict[str, Any], summary: str) -> Dict[str, Any]:
    """A copy already filed (often as "Account Statement" or "General Document"): make it what it is."""
    merged = {**(row.get("fields") or {}), **{k: v for k, v in fields.items() if v is not None}}
    merged["official"] = bool((row.get("fields") or {}).get("official")) or bool(fields.get("official"))
    patch = {"category": CATEGORY, "fields": merged, "project": None, "summary": summary}
    db = _client()
    db.table("vula_filed_documents").update(patch).eq("id", row["id"]).eq("tenant_id", tenant_id).execute()
    back = (db.table("vula_filed_documents").select("id,category,fields,filename,file_url")
            .eq("id", row["id"]).limit(1).execute().data or [{}])[0]
    return {**back, "relabelled": back.get("category") == CATEGORY}


async def keep(tenant_id: str, path: Path, *, filename: str, source: str, text: Optional[str] = None,
               txns: Optional[List[Dict[str, Any]]] = None, official: bool = False,
               password: Optional[str] = None, filed_by: str = "vula") -> Optional[Dict[str, Any]]:
    """File one bank statement under its account. Returns the document row, or None when it isn't
    the business's to keep (a private account) or couldn't be read. Never raises."""
    try:
        data = Path(path).read_bytes()
    except Exception as exc:  # noqa: BLE001
        log.warning("statement file not kept for %s: %s", tenant_id, exc)
        return None
    try:
        text = text if text and text.strip() else read_text(tenant_id, Path(path), password)
        info = describe(text, txns)
        if _private(tenant_id, info["number"]):
            return None
        fields = _fields(tenant_id, info, official)
        summary = label(info)
        digest = hashlib.sha256(data).hexdigest()
        same = (_client().table("vula_filed_documents").select("id,category,fields,filename,file_url")
                .eq("tenant_id", tenant_id).eq("content_hash", digest).limit(1).execute().data or [])
        if same:
            return _relabel(tenant_id, same[0], fields, summary)
        from vula.integrations import doc_filing
        row = await doc_filing.file_document(tenant_id, filename=filename or "statement.pdf", data=data,
                                             content_type="application/pdf", category=CATEGORY, summary=summary,
                                             fields=fields, source=source, filed_by=filed_by, status="filed")
        if row.get("private"):
            return None
        return row
    except Exception as exc:  # noqa: BLE001 — keeping the file must never stop a statement being read
        log.warning("statement file not kept for %s: %s", tenant_id, exc)
        return None


# ── what's already there ─────────────────────────────────────────────────────

async def backfill(tenant_id: str) -> Dict[str, Any]:
    """File every statement already behind the bank lines, under its account: the originals still on
    the server's volume, and the copies already filed under another name. Says which are gone."""
    from config import settings
    from vula import storage_links
    db = _client()
    names = sorted({r["source_file"] for r in (db.table("commerce_bank_transactions").select("source_file")
                                                .eq("tenant_id", tenant_id).limit(20000).execute().data or [])
                    if r.get("source_file") and r["source_file"] not in ("whatsapp_pop", "pasted-statement")})
    root = settings.upload_dir / tenant_id
    kept, relabelled, missing = 0, 0, []
    import tempfile
    filed = (db.table("vula_filed_documents").select("id,filename,category,summary,file_url,fields")
             .eq("tenant_id", tenant_id).execute().data or [])
    by_name = {}
    for d in filed:
        by_name.setdefault(d.get("filename"), d)
    from vula.integrations.doc_filing import is_bank_statement
    for name in names:
        safe = re.sub(r"[^A-Za-z0-9._-]+", "_", name)
        path = None
        if root.exists():
            path = next((p for p in root.rglob(name) if p.is_file()), None) \
                or next((p for p in root.rglob(safe) if p.is_file()), None)
        doc = by_name.get(name)
        if path:
            row = await keep(tenant_id, path, filename=name, source="volume")
        elif doc and doc.get("file_url"):
            with tempfile.TemporaryDirectory() as tmp:
                p = Path(tmp) / "statement.pdf"
                try:
                    p.write_bytes(storage_links.fetch_sync(doc["file_url"]))
                except Exception:  # noqa: BLE001
                    missing.append(mask_name(name))
                    continue
                row = await keep(tenant_id, p, filename=name, source="volume")
        else:
            missing.append(mask_name(name))
            continue
        if row and row.get("relabelled"):
            relabelled += 1
        elif row and row.get("id"):
            kept += 1
    # statements filed under another category that aren't behind any lines (e.g. emailed, not read)
    for d in filed:
        if d.get("category") == CATEGORY or d.get("filename") in names or not d.get("file_url"):
            continue
        if not is_bank_statement(d.get("category") or "", d.get("summary") or "", d.get("filename") or ""):
            continue
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "statement.pdf"
            try:
                p.write_bytes(storage_links.fetch_sync(d["file_url"]))
            except Exception:  # noqa: BLE001
                continue
            row = await keep(tenant_id, p, filename=d.get("filename") or "statement.pdf", source="volume")
        if row and row.get("relabelled"):
            relabelled += 1
    log.info("statement files for %s: %s kept, %s relabelled, %s no longer anywhere", tenant_id, kept,
             relabelled, len(missing))
    return {"kept": kept, "relabelled": relabelled, "missing": missing, "statement_files": len(names)}


# ── which months are on file ─────────────────────────────────────────────────

def year_start(today: Optional[date] = None, year_end_month: int = 2) -> date:
    """The first day of the financial year `today` falls in (year end February → 1 March)."""
    t = today or date.today()
    first = year_end_month % 12 + 1
    return date(t.year if t.month >= first else t.year - 1, first, 1)


def _months(start: date, end: date) -> List[str]:
    out, y, m = [], start.year, start.month
    while (y, m) <= (end.year, end.month):
        out.append(f"{y:04d}-{m:02d}")
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return out


def coverage(tenant_id: str, start: Optional[date] = None, end: Optional[date] = None) -> Dict[str, Any]:
    """Per bank account and month of the year: is the statement on file (official or not), and how many
    bank lines are in the books for it. A month with lines and no statement is one to fetch."""
    start = start or year_start()
    year_end = date(start.year + 1, start.month, 1)
    end = min(end or date.today(), date.fromordinal(year_end.toordinal() - 1))
    months = _months(start, end)
    db = _client()
    accounts = [a for a in (db.table("commerce_bank_accounts").select("id,account_number,bank,name,private_to")
                            .eq("tenant_id", tenant_id).execute().data or []) if not a.get("private_to")]
    docs = (db.table("vula_filed_documents").select("id,fields").eq("tenant_id", tenant_id)
            .eq("category", CATEGORY).execute().data or [])
    lines = (db.table("commerce_bank_transactions").select("bank_account,txn_date,match_status")
             .eq("tenant_id", tenant_id).gte("txn_date", start.isoformat()).lte("txn_date", end.isoformat())
             .limit(50000).execute().data or [])
    out = []
    for a in accounts:
        have: Dict[str, Dict[str, bool]] = {}
        for d in docs:
            f = d.get("fields") or {}
            if f.get("bank_account_id") != a["id"] or not f.get("period_from") or not f.get("period_to"):
                continue
            try:
                for m in _months(date.fromisoformat(f["period_from"][:10]), date.fromisoformat(f["period_to"][:10])):
                    cur = have.setdefault(m, {"official": False})
                    cur["official"] = cur["official"] or bool(f.get("official"))
            except ValueError:
                continue
        counts: Dict[str, int] = {}
        for ln in lines:
            if ln.get("bank_account") == a["account_number"] and ln.get("match_status") != "ignored":
                k = str(ln.get("txn_date"))[:7]
                counts[k] = counts.get(k, 0) + 1
        rows = [{"month": m, "statement": m in have, "official": bool(have.get(m, {}).get("official")),
                 "lines": counts.get(m, 0)} for m in months]
        out.append({"id": a["id"], "account": f"{a.get('name') or a.get('bank') or 'Bank'} {_mask(a['account_number'])}",
                    "months": rows, "missing": [r["month"] for r in rows if not r["statement"]]})
    no_account = sum(1 for ln in lines if not ln.get("bank_account") and ln.get("match_status") != "ignored")
    return {"from": start.isoformat(), "to": end.isoformat(), "accounts": out, "lines_on_no_account": no_account}
