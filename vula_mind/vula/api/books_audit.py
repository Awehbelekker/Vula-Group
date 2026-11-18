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

18 Nov 2026, the books review screen (vula/commerce/books_review.py):
  POST /{tenant_id}/admin/books-audit/run                 now also keeps the rebuild dry run (`books`)
  GET  /{tenant_id}/admin/books-audit/review              the worklist, every item as it is now
  POST /{tenant_id}/admin/books-audit/lines/restore       {ids, reason}  set-aside lines back in the books
  POST /{tenant_id}/admin/books-audit/lines/set-aside     {ids, reason}  lines out of the books
  POST /{tenant_id}/admin/books-audit/lines/transfer      {ids}          two legs of a move between own accounts
  POST /{tenant_id}/admin/books-audit/lines/{id}/link     {invoice_id | expense_id}  this line IS that payment
  POST /{tenant_id}/admin/books-audit/documents/{kind}/{id}/date  {date, reason}  correct a misread date
  POST /{tenant_id}/admin/books-audit/{audit_id}/agree    {note}         the business agrees the audit
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


@router.post("/{tenant_id}/admin/books-audit/run")
async def run_audit(tenant_id: str, request: Request) -> dict:
    """The books audit and the rebuild dry run (what the one trusted book would hold), kept together."""
    person, _ = await _full(tenant_id, request)
    from vula.commerce import books_review
    return await asyncio.to_thread(books_review.run, tenant_id, person or "owner")


@router.get("/{tenant_id}/admin/books-audit/latest")
async def latest_audit(tenant_id: str, request: Request) -> dict:
    await _full(tenant_id, request)
    from vula.commerce import books_review
    return await asyncio.to_thread(books_review.latest, tenant_id) or {}


@router.get("/{tenant_id}/admin/books-audit/review")
async def review(tenant_id: str, request: Request) -> dict:
    await _full(tenant_id, request)
    from vula.commerce import books_review
    return await asyncio.to_thread(books_review.review, tenant_id)


class LinesBody(BaseModel):
    ids: List[str]
    reason: Optional[str] = None


class LinkBody(BaseModel):
    invoice_id: Optional[str] = None
    expense_id: Optional[str] = None


class DateBody(BaseModel):
    date: str
    reason: str


class AgreeBody(BaseModel):
    note: Optional[str] = None


async def _fix(fn, *args, **kwargs) -> dict:
    from vula.commerce.books_review import Refused
    try:
        return await asyncio.to_thread(fn, *args, **kwargs)
    except Refused as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.post("/{tenant_id}/admin/books-audit/lines/restore")
async def restore_lines(tenant_id: str, body: LinesBody, request: Request) -> dict:
    _, actor = await _full(tenant_id, request)
    from vula.commerce import books_review
    return await _fix(books_review.restore, tenant_id, body.ids, body.reason or "", actor=actor)


@router.post("/{tenant_id}/admin/books-audit/lines/set-aside")
async def set_aside_lines(tenant_id: str, body: LinesBody, request: Request) -> dict:
    _, actor = await _full(tenant_id, request)
    from vula.commerce import books_review
    return await _fix(books_review.set_aside, tenant_id, body.ids, body.reason or "", actor=actor)


@router.post("/{tenant_id}/admin/books-audit/lines/transfer")
async def transfer_lines(tenant_id: str, body: LinesBody, request: Request) -> dict:
    _, actor = await _full(tenant_id, request)
    from vula.commerce import books_review
    return await _fix(books_review.transfer, tenant_id, body.ids, actor=actor)


@router.post("/{tenant_id}/admin/books-audit/lines/{txn_id}/link")
async def link_line(tenant_id: str, txn_id: str, body: LinkBody, request: Request) -> dict:
    _, actor = await _full(tenant_id, request)
    from vula.commerce import books_review
    return await _fix(books_review.link, tenant_id, txn_id, invoice_id=body.invoice_id,
                      expense_id=body.expense_id, actor=actor)


@router.post("/{tenant_id}/admin/books-audit/documents/{kind}/{doc_id}/date")
async def correct_date(tenant_id: str, kind: str, doc_id: str, body: DateBody, request: Request) -> dict:
    _, actor = await _full(tenant_id, request)
    from vula.commerce import books_review
    return await _fix(books_review.set_date, tenant_id, kind, doc_id, body.date, body.reason, actor=actor)


@router.post("/{tenant_id}/admin/books-audit/{audit_id}/agree")
async def agree_audit(tenant_id: str, audit_id: str, body: AgreeBody, request: Request) -> dict:
    person, actor = await _full(tenant_id, request)
    from vula.commerce import books_review
    return await _fix(books_review.agree, tenant_id, audit_id, body.note or "", person or "owner", actor=actor)


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
