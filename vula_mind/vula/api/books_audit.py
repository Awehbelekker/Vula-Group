"""vula/api/books_audit.py — the books audit and official-statement checks for the dashboard.

17 Nov 2026 (Phase 0 of Xero-quality books). Mounted under /v1/commerce, so the paths are
/v1/commerce/{tenant_id}/admin/… and pass the tenant membership guard; every route here also needs
full access (an owner or manager) — the bank data behind the books is the business's to decide.

  POST /{tenant_id}/admin/books-audit/run                 run the audit now and keep the report
  GET  /{tenant_id}/admin/books-audit/latest              the latest kept report
  POST /{tenant_id}/admin/books-audit/statements          upload an official statement — compare only
  GET  /{tenant_id}/admin/books-audit/statements          the comparisons, newest first
  GET  /{tenant_id}/admin/books-audit/statements/{id}     one comparison with its proposed actions
  POST /{tenant_id}/admin/books-audit/statements/{id}/apply   do the confirmed actions (all, or `accept`)
"""
from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path
from typing import List, Optional

from fastapi import APIRouter, File, HTTPException, Request, UploadFile
from pydantic import BaseModel

router = APIRouter(tags=["books-audit"])
MAX_PDF_BYTES = 15 * 1024 * 1024


async def _full(tenant_id: str, request: Request) -> tuple:
    """(person key, actor) for an owner or manager; 403 for anyone else."""
    from vula.api.tenant_auth import member_scope, scope_allows
    from vula.identity import person_for_login
    auth = request.headers.get("authorization", "")
    scope = await member_scope(auth, tenant_id)
    if not scope_allows(scope, frozenset()):
        raise HTTPException(status_code=403, detail="Only an owner or manager can audit the books.")
    person = await person_for_login(auth, tenant_id)
    return (person.key if person is not None else ""), {"email": (scope or {}).get("email")}


def _client():
    from vula.commerce.service import _client as c
    return c()


def store_report(tenant_id: str, rep: dict, created_by: str) -> Optional[str]:
    try:
        got = (_client().table("vula_books_audits").insert({
            "tenant_id": tenant_id, "report": rep, "summary": rep.get("summary") or {},
            "created_by": created_by}).execute().data or [])
        return got[0]["id"] if got else None
    except Exception:  # noqa: BLE001 — before migration 287 the report is still returned
        return None


@router.post("/{tenant_id}/admin/books-audit/run")
async def run_audit(tenant_id: str, request: Request) -> dict:
    person, _ = await _full(tenant_id, request)
    from vula.commerce import books_audit
    rep = await asyncio.to_thread(books_audit.run, tenant_id)
    rep["id"] = await asyncio.to_thread(store_report, tenant_id, rep, person or "owner")
    return rep


@router.get("/{tenant_id}/admin/books-audit/latest")
async def latest_audit(tenant_id: str, request: Request) -> dict:
    await _full(tenant_id, request)
    rows = await asyncio.to_thread(lambda: _client().table("vula_books_audits")
                                   .select("id,report,created_by,created_at,signed_off_by,signed_off_at")
                                   .eq("tenant_id", tenant_id).order("created_at", desc=True).limit(1)
                                   .execute().data or [])
    return rows[0] if rows else {}


@router.post("/{tenant_id}/admin/books-audit/statements")
async def upload_statement(tenant_id: str, request: Request, file: UploadFile = File(...)) -> dict:
    person, _ = await _full(tenant_id, request)
    data = await file.read()
    if not data or len(data) > MAX_PDF_BYTES:
        raise HTTPException(status_code=400, detail="Send one statement PDF (up to 15 MB).")
    name = Path(file.filename or "statement.pdf").name[:200]
    from vula.commerce import statement_compare
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "statement.pdf"
        path.write_bytes(data)
        out = await asyncio.to_thread(statement_compare.compare, tenant_id, filename=name, pdf_path=path,
                                      created_by=person or "owner")
    if out.get("error"):
        raise HTTPException(status_code=422, detail=out["error"])
    return out


@router.get("/{tenant_id}/admin/books-audit/statements")
async def list_checks(tenant_id: str, request: Request) -> dict:
    await _full(tenant_id, request)
    rows = await asyncio.to_thread(lambda: _client().table("vula_statement_checks")
                                   .select("id,filename,account_number,bank,period_from,period_to,summary,"
                                           "status,created_at,applied_by,applied_at")
                                   .eq("tenant_id", tenant_id).order("created_at", desc=True).limit(100)
                                   .execute().data or [])
    for r in rows:
        num = r.pop("account_number", None)
        r["account"] = f"…{num[-4:]}" if num else None
    return {"checks": rows}


@router.get("/{tenant_id}/admin/books-audit/statements/{check_id}")
async def get_check(tenant_id: str, check_id: str, request: Request) -> dict:
    await _full(tenant_id, request)
    rows = await asyncio.to_thread(lambda: _client().table("vula_statement_checks").select("*")
                                   .eq("tenant_id", tenant_id).eq("id", check_id).limit(1).execute().data or [])
    if not rows:
        raise HTTPException(status_code=404, detail="That comparison isn't there.")
    r = rows[0]
    num = r.pop("account_number", None)
    r["account"] = f"…{num[-4:]}" if num else None
    for a in r.get("actions") or []:
        if a.get("account"):
            a["account"] = f"…{a['account'][-4:]}"
    return r


class ApplyBody(BaseModel):
    accept: Optional[List[str]] = None      # the action ids to do; omitted = all of them


@router.post("/{tenant_id}/admin/books-audit/statements/{check_id}/apply")
async def apply_check(tenant_id: str, check_id: str, body: ApplyBody, request: Request) -> dict:
    person, actor = await _full(tenant_id, request)
    from vula.commerce import statement_compare
    out = await asyncio.to_thread(statement_compare.apply, tenant_id, check_id, person=person or "owner",
                                  full_access=True, accept=body.accept, actor=actor)
    if out.get("error") and not out.get("done"):
        raise HTTPException(status_code=400, detail=out["error"])
    return out
