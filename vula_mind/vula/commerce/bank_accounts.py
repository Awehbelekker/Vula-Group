"""vula/commerce/bank_accounts.py — a business's bank accounts, and the project one can belong to.

11 Nov 2026 (Ian, DIGG): "Bank statements should not be allocated to a project, it's the whole
business, unless the tenant adds a bank account to a certain project." DIGG's FNB account ending 6574
is Porterfield's own account; everything else on its statements is the whole business's.

- Every statement Vula reads names its account; the account is remembered here (migration 261) and
  each bank line records the account it came from (commerce_bank_transactions.bank_account).
- The owner can tie an account to one project (Bank › Tools › Bank accounts). Every line from that
  account is then that project's — new statements, and the lines already in (set_project).
- A statement itself is never filed on a project (vula/integrations/doc_filing.py): one line naming
  "22 Porterfield" doesn't make the whole business account Porterfield's.

Before migration 261 every function here is a harmless no-op.
"""
from __future__ import annotations

import logging
import re
import time
from typing import Any, Dict, List, Optional

log = logging.getLogger(__name__)

# "Account Number: 6321 425 4607", "Account no. 63214254607", "Acc No 62-0001-23456"
_LABELLED = re.compile(r"(?:account\s*(?:number|no\.?|nr\.?|#)|acc(?:ount)?\s*no\.?)\s*[:#.]?\s*"
                       r"((?:\d[ \-]?){7,15}\d)\b", re.IGNORECASE)
# FNB's header: "Platinum Business Account : 63214254607" / "Gold Cheque Account 62012345678"
_NAMED = re.compile(r"\b(?:cheque|current|business|savings|transactional|credit card|platinum|gold|"
                    r"private wealth|easy|aspire|premier)\s+(?:business\s+)?account\s*[:#]?\s*(\d{9,13})\b",
                    re.IGNORECASE)
_BANKS = (("first national bank", "FNB"), ("fnb", "FNB"), ("standard bank", "Standard Bank"),
          ("absa", "Absa"), ("nedbank", "Nedbank"), ("capitec", "Capitec"), ("investec", "Investec"),
          ("tymebank", "TymeBank"), ("discovery bank", "Discovery Bank"), ("african bank", "African Bank"),
          ("bidvest bank", "Bidvest Bank"))


def account_number(text: str) -> Optional[str]:
    """The account a statement is for (digits only), read from its own header, else None."""
    head = (text or "")[:6000]
    for rx in (_LABELLED, _NAMED):
        m = rx.search(head)
        if m:
            digits = re.sub(r"\D", "", m.group(1))
            if 8 <= len(digits) <= 16:
                return digits
    return None


def bank_name(text: str) -> Optional[str]:
    low = (text or "")[:6000].lower()
    return next((name for needle, name in _BANKS if needle in low), None)


def last4(number: Optional[str]) -> str:
    return (number or "")[-4:]


def _client():
    from vula.commerce.service import _client as c
    return c()


_AVAILABLE: Dict[str, float] = {}


def available() -> bool:
    """Migration 261 is in (cached for ten minutes either way)."""
    hit = _AVAILABLE.get("v")
    if hit is not None and time.monotonic() - abs(hit) < 600:
        return hit > 0
    try:
        _client().table("commerce_bank_accounts").select("id").limit(1).execute()
        ok = True
    except Exception:
        ok = False
    _AVAILABLE["v"] = time.monotonic() if ok else -time.monotonic()
    return ok


def remember(tenant_id: str, number: Optional[str], *, bank: Optional[str] = None,
             holder: Optional[str] = None, seen: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """Record that a statement for `number` came in. Returns the account row (with its project)."""
    if not number or not available():
        return None
    db = _client()
    try:
        rows = (db.table("commerce_bank_accounts").select("*").eq("tenant_id", tenant_id)
                .eq("account_number", number).limit(1).execute().data or [])
        if rows:
            row = rows[0]
            patch = {"updated_at": "now()"}
            if seen and (not row.get("last_seen") or str(row["last_seen"]) < seen):
                patch["last_seen"] = seen
            if seen and (not row.get("first_seen") or str(row["first_seen"]) > seen):
                patch["first_seen"] = seen
            for k, v in (("bank", bank), ("holder", holder)):
                if v and not row.get(k):
                    patch[k] = v
            db.table("commerce_bank_accounts").update(patch).eq("id", row["id"]).execute()
            return {**row, **{k: v for k, v in patch.items() if k != "updated_at"}}
        row = {"tenant_id": tenant_id, "account_number": number, "bank": bank, "holder": holder,
               "first_seen": seen, "last_seen": seen}
        ins = db.table("commerce_bank_accounts").insert(row).execute()
        return (ins.data or [row])[0]
    except Exception as exc:
        log.warning("bank account %s not recorded for %s: %s", last4(number), tenant_id, exc)
        return None


def project_for(tenant_id: str, number: Optional[str]) -> Optional[str]:
    """The project every line from this account belongs to, or None (the whole business)."""
    if not number or not available():
        return None
    try:
        rows = (_client().table("commerce_bank_accounts").select("project").eq("tenant_id", tenant_id)
                .eq("account_number", number).limit(1).execute().data or [])
        return (rows[0].get("project") or None) if rows else None
    except Exception:
        return None


def list_accounts(tenant_id: str) -> List[Dict[str, Any]]:
    if not available():
        return []
    db = _client()
    try:
        rows = (db.table("commerce_bank_accounts").select("*").eq("tenant_id", tenant_id)
                .order("last_seen", desc=True).execute().data or [])
    except Exception:
        return []
    out = []
    for r in rows:
        if r.get("private_to"):
            continue                          # one member's own account: not listed for the business
        try:
            n = len(db.table("commerce_bank_transactions").select("id").eq("tenant_id", tenant_id)
                    .eq("bank_account", r["account_number"]).execute().data or [])
        except Exception:
            n = None
        out.append({"id": r["id"], "account": f"…{last4(r['account_number'])}", "bank": r.get("bank"),
                    "name": r.get("name"), "holder": r.get("holder"), "project": r.get("project"),
                    "first_seen": r.get("first_seen"), "last_seen": r.get("last_seen"), "lines": n})
    return out


def set_project(tenant_id: str, account_id: str, project: Optional[str],
                name: Optional[str] = None) -> Dict[str, Any]:
    """Tie an account to a project (None: the whole business) and re-file the lines already in.
    A line the owner put on a project by hand keeps it. Read back before it is reported."""
    if not available():
        return {"error": "Bank accounts need migration 261."}
    from vula.commerce.service import canonical_project
    db = _client()
    rows = (db.table("commerce_bank_accounts").select("*").eq("tenant_id", tenant_id)
            .eq("id", account_id).limit(1).execute().data or [])
    if not rows:
        return {"error": "account not found"}
    acct = rows[0]
    if acct.get("private_to"):
        return {"error": "account not found"}  # private to one member: not the business's to change
    project = canonical_project(tenant_id, project) if (project or "").strip() else None
    patch: Dict[str, Any] = {"project": project, "updated_at": "now()"}
    if name is not None:
        patch["name"] = name.strip() or None
    db.table("commerce_bank_accounts").update(patch).eq("id", account_id).eq("tenant_id", tenant_id).execute()
    lines = (db.table("commerce_bank_transactions").select("id,project,project_source")
             .eq("tenant_id", tenant_id).eq("bank_account", acct["account_number"]).execute().data or [])
    moved = 0
    for ln in lines:
        if ln.get("project_source") == "owner":
            continue                          # the owner's own choice for that line stands
        if project and ln.get("project") != project:
            new = {"project": project, "project_source": "account"}
        elif not project and ln.get("project_source") == "account":
            new = {"project": None, "project_source": None}   # untied: back to the whole business
        else:
            continue
        db.table("commerce_bank_transactions").update(new).eq("id", ln["id"]).eq("tenant_id", tenant_id).execute()
        moved += 1
    back = (db.table("commerce_bank_accounts").select("project").eq("id", account_id).limit(1)
            .execute().data or [{}])[0]
    if (back.get("project") or None) != project:
        return {"error": "The change didn't save — try again."}
    return {"ok": True, "account": f"…{last4(acct['account_number'])}", "project": project,
            "lines_moved": moved}


def tag_lines(tenant_id: str, source_file: str, number: str) -> int:
    """Lines read from `source_file` before migration 261 get the account they came from (and its
    project, unless the owner placed them). Returns how many were tagged."""
    if not (source_file and number and available()):
        return 0
    db = _client()
    project = project_for(tenant_id, number)
    try:
        rows = (db.table("commerce_bank_transactions").select("id,project,project_source")
                .eq("tenant_id", tenant_id).eq("source_file", source_file).is_("bank_account", "null")
                .execute().data or [])
    except Exception:
        return 0
    for r in rows:
        patch: Dict[str, Any] = {"bank_account": number}
        if project and r.get("project_source") != "owner":
            patch.update({"project": project, "project_source": "account"})
        db.table("commerce_bank_transactions").update(patch).eq("id", r["id"]).execute()
    return len(rows)


_BACKFILLED: set = set()


def backfill_from_files(tenant_id: str) -> Dict[str, Any]:
    """[blocking] Lines read before migration 261 don't know their account. For every statement file
    still on the server (settings.upload_dir, kept since the volume was added on 10 Nov 2026), read the
    account off it and tag its lines (and put them on the account's project, if it has one). Once per
    tenant per process."""
    if tenant_id in _BACKFILLED or not available():
        return {"skipped": True}
    _BACKFILLED.add(tenant_id)
    from config import settings
    from vula.commerce.bank_rec import extract_pdf_text, get_statement_password
    db = _client()
    try:
        rows = (db.table("commerce_bank_transactions").select("source_file").eq("tenant_id", tenant_id)
                .is_("bank_account", "null").limit(5000).execute().data or [])
    except Exception:
        return {"error": "lookup failed"}
    files = sorted({r["source_file"] for r in rows if r.get("source_file")})
    root = settings.upload_dir / tenant_id
    pwd = get_statement_password(tenant_id)
    tagged, read, missing = 0, 0, 0
    for name in files:
        safe = re.sub(r"[^A-Za-z0-9._-]+", "_", name)
        path = next((p for p in (root.rglob(name) if root.exists() else []) if p.is_file()), None) \
            or next((p for p in (root.rglob(safe) if root.exists() else []) if p.is_file()), None)
        if not path:
            missing += 1
            continue
        try:
            from vula.ingestion import fnb_statement
            text = fnb_statement.pdf_text(path, pwd) or extract_pdf_text(path, pwd)
        except Exception:
            try:
                text = extract_pdf_text(path, pwd)
            except Exception:
                continue
        number = account_number(text)
        if not number:
            continue
        read += 1
        remember(tenant_id, number, bank=bank_name(text))
        tagged += tag_lines(tenant_id, name, number)
    log.info("bank accounts backfill for %s: %s files read, %s lines tagged, %s files no longer on the server",
             tenant_id, read, tagged, missing)
    return {"files_read": read, "lines_tagged": tagged, "files_missing": missing}
