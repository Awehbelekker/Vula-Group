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

19 Nov 2026, the documents behind the money (vula/commerce/doc_audit.py), owner/manager only:
  POST …/books-audit/docs/{id}/figures      {changes, reason}  correct what was read (old values kept)
  POST …/books-audit/docs/{id}/confirm      {note}             the figures are right as read
  POST …/books-audit/docs/{id}/book                            put an invoice on file into the books
  POST …/books-audit/docs/{id}/not-a-bill   {reason}           an invoice on file that isn't a cost to book
  POST …/books-audit/docs/{id}/duplicate    {of_id}            a second copy of another document
  POST …/books-audit/docs/{id}/vat-basis    {basis}            its prices include VAT / exclude it / no VAT
  POST …/books-audit/bills/{id}/cancel-duplicate {of_id, reason}  the second copy of a bill (never a paid one)
  POST …/books-audit/bills/{id}/match-document   {reason}     an unpaid bill booked at its document's figures
  Duplicate suppliers are merged with the existing POST /admin/suppliers/merge.
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


async def _viewer(tenant_id: str, request: Request) -> dict:
    """An owner or manager, or the business's accountant (read-only, 19 Nov 2026); 403 for anyone
    else. Returns the scope."""
    from vula.api.tenant_auth import ACCOUNTANT, member_scope, scope_allows
    scope = await member_scope(request.headers.get("authorization", ""), tenant_id)
    if scope_allows(scope, frozenset()):
        return scope
    if scope and scope.get("role") == ACCOUNTANT and scope.get("access"):
        return scope
    raise HTTPException(status_code=403, detail="Only an owner, a manager or the business's accountant can see this.")


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
    await _viewer(tenant_id, request)
    from vula.commerce import books_review
    return await asyncio.to_thread(books_review.latest, tenant_id) or {}


@router.get("/{tenant_id}/admin/books-audit/review")
async def review(tenant_id: str, request: Request) -> dict:
    await _viewer(tenant_id, request)
    from vula.commerce import books_review
    return await asyncio.to_thread(books_review.review, tenant_id)


class LinesBody(BaseModel):
    ids: List[str]
    reason: Optional[str] = None


class LinkBody(BaseModel):
    invoice_id: Optional[str] = None
    expense_id: Optional[str] = None


class PayBillBody(BaseModel):
    bill_ids: List[str] = []
    bill_id: Optional[str] = None       # one bill (the first screen sent this)
    account_code: str


class AddInvoiceBody(BaseModel):
    txn_id: Optional[str] = None        # the money received that paid it, tied as its payment


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


@router.post("/{tenant_id}/admin/books-audit/docs/{doc_id}/certificate")
async def label_certificate(tenant_id: str, doc_id: str, request: Request) -> dict:
    _, actor = await _full(tenant_id, request)
    from vula.commerce import certificates
    return await _fix(certificates.label, tenant_id, doc_id, actor=actor)


@router.post("/{tenant_id}/admin/books-audit/docs/{doc_id}/add-to-invoices")
async def add_own_invoice(tenant_id: str, doc_id: str, body: AddInvoiceBody, request: Request) -> dict:
    _, actor = await _full(tenant_id, request)
    from vula.commerce import doc_audit
    return await _fix(doc_audit.add_to_invoices, tenant_id, doc_id, body.txn_id, actor=actor)


class PopLinkBody(BaseModel):
    bill_ids: List[str] = []


class PopDismissBody(BaseModel):
    reason: str = ""


@router.post("/{tenant_id}/admin/books-audit/pops/{doc_id}/link")
async def link_pop(tenant_id: str, doc_id: str, body: PopLinkBody, request: Request) -> dict:
    """This proof of payment paid these bills (vula/commerce/pop_links.py, 20 Nov 2026)."""
    _, actor = await _full(tenant_id, request)
    from vula.commerce import pop_links
    try:
        return await pop_links.link(tenant_id, doc_id, body.bill_ids, actor=actor)
    except pop_links.Refused as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.post("/{tenant_id}/admin/books-audit/pops/{doc_id}/dismiss")
async def dismiss_pop(tenant_id: str, doc_id: str, body: PopDismissBody, request: Request) -> dict:
    """Not a payment of a supplier's bill — kept with what it was."""
    _, actor = await _full(tenant_id, request)
    from vula.commerce import pop_links
    try:
        return await asyncio.to_thread(pop_links.dismiss, tenant_id, doc_id, body.reason, actor=actor)
    except pop_links.Refused as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.post("/{tenant_id}/admin/books-audit/lines/{txn_id}/pay-bill")
async def pay_bill(tenant_id: str, txn_id: str, body: PayBillBody, request: Request) -> dict:
    _, actor = await _full(tenant_id, request)
    from vula.commerce import books_review
    return await _fix(books_review.pay_bill, tenant_id, txn_id, body.bill_ids or [body.bill_id or ""],
                      body.account_code, actor=actor)


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


class FiguresBody(BaseModel):
    changes: dict
    reason: str


class NoteBody(BaseModel):
    note: Optional[str] = None


class ReasonBody(BaseModel):
    reason: str


class OfBody(BaseModel):
    of_id: str
    reason: Optional[str] = None


class BasisBody(BaseModel):
    basis: str


@router.post("/{tenant_id}/admin/books-audit/docs/{doc_id}/figures")
async def doc_figures(tenant_id: str, doc_id: str, body: FiguresBody, request: Request) -> dict:
    _, actor = await _full(tenant_id, request)
    from vula.commerce import doc_audit
    return await _fix(doc_audit.correct_figures, tenant_id, doc_id, body.changes, body.reason, actor=actor)


@router.post("/{tenant_id}/admin/books-audit/docs/{doc_id}/confirm")
async def doc_confirm(tenant_id: str, doc_id: str, body: NoteBody, request: Request) -> dict:
    _, actor = await _full(tenant_id, request)
    from vula.commerce import doc_audit
    return await _fix(doc_audit.confirm, tenant_id, doc_id, body.note or "", actor=actor)


@router.post("/{tenant_id}/admin/books-audit/docs/{doc_id}/book")
async def doc_book(tenant_id: str, doc_id: str, request: Request) -> dict:
    _, actor = await _full(tenant_id, request)
    from vula.commerce import doc_audit
    from vula.commerce.books_review import Refused
    try:
        return await doc_audit.book(tenant_id, doc_id, actor=actor)
    except Refused as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.post("/{tenant_id}/admin/books-audit/docs/{doc_id}/not-a-bill")
async def doc_not_a_bill(tenant_id: str, doc_id: str, body: ReasonBody, request: Request) -> dict:
    _, actor = await _full(tenant_id, request)
    from vula.commerce import doc_audit
    return await _fix(doc_audit.not_a_bill, tenant_id, doc_id, body.reason, actor=actor)


@router.post("/{tenant_id}/admin/books-audit/docs/{doc_id}/duplicate")
async def doc_duplicate(tenant_id: str, doc_id: str, body: OfBody, request: Request) -> dict:
    _, actor = await _full(tenant_id, request)
    from vula.commerce import doc_audit
    return await _fix(doc_audit.mark_duplicate, tenant_id, doc_id, body.of_id, actor=actor)


@router.post("/{tenant_id}/admin/books-audit/docs/{doc_id}/vat-basis")
async def doc_vat_basis(tenant_id: str, doc_id: str, body: BasisBody, request: Request) -> dict:
    _, actor = await _full(tenant_id, request)
    from vula.commerce import doc_audit
    return await _fix(doc_audit.set_vat_basis, tenant_id, doc_id, body.basis, actor=actor)


@router.post("/{tenant_id}/admin/books-audit/bills/{bill_id}/cancel-duplicate")
async def bill_cancel_duplicate(tenant_id: str, bill_id: str, body: OfBody, request: Request) -> dict:
    _, actor = await _full(tenant_id, request)
    from vula.commerce import doc_audit
    return await _fix(doc_audit.cancel_duplicate_bill, tenant_id, bill_id, body.of_id, body.reason or "", actor=actor)


@router.post("/{tenant_id}/admin/books-audit/bills/{bill_id}/match-document")
async def bill_match_document(tenant_id: str, bill_id: str, body: ReasonBody, request: Request) -> dict:
    _, actor = await _full(tenant_id, request)
    from vula.commerce import doc_audit
    return await _fix(doc_audit.match_document, tenant_id, bill_id, body.reason, actor=actor)


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
        if not out.get("error"):
            # the official statement itself is kept, under its account (vula/commerce/statement_files.py)
            from vula.commerce import statement_files
            kept = await statement_files.keep(tenant_id, path, filename=name, source="dashboard", official=True,
                                              filed_by=person or "owner")
            out["filed"] = bool(kept and kept.get("id"))
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


# ── 19 Nov 2026: the accountant's file room (vula/commerce/finance_folders.py, statement_files.py) ──

@router.get("/{tenant_id}/admin/finance-folders")
async def finance_folders(tenant_id: str, request: Request, year: Optional[str] = None) -> dict:
    """The year's money documents in an accountant's folders, with the bank statements per account and
    the months still missing. `year`: the first day of the financial year (default: this one)."""
    scope = await _viewer(tenant_id, request)
    from vula.commerce import finance_folders as ff
    try:
        tree = await asyncio.to_thread(ff.organise, tenant_id, year)
    except ValueError:
        raise HTTPException(status_code=400, detail="year is the first day of the financial year, e.g. 2026-03-01")
    if scope.get("role") == "accountant":
        # no signed links for an accountant: each document is opened through
        # /v1/documents/{t}/filed/{id}/open, which puts it on the business's record
        for f in tree["folders"]:
            for g in f["groups"]:
                for it in g["items"]:
                    it["file_url"] = None
                    it["open"] = True
                    it.pop("ext", None)
        return tree
    return ff.public(tree)


@router.get("/{tenant_id}/admin/finance-folders.zip")
async def finance_folders_zip(tenant_id: str, request: Request, year: Optional[str] = None,
                              folder: Optional[str] = None):
    """The year's money documents (or one folder) as a zip with Index.xlsx, both read back first."""
    await _viewer(tenant_id, request)
    from fastapi.responses import Response
    from vula.commerce import finance_folders as ff
    try:
        data, manifest = await asyncio.to_thread(ff.build_zip, tenant_id, year, folder)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except RuntimeError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    return Response(content=data, media_type="application/zip",
                    headers={"Content-Disposition": f'attachment; filename="{ff.zip_filename(manifest)}"',
                             "X-Files-Failed": str(len(manifest["files_failed"]))})


@router.get("/{tenant_id}/admin/bank-statements/coverage")
async def statement_coverage(tenant_id: str, request: Request, year: Optional[str] = None) -> dict:
    """Per bank account and month of the year: is the statement on file, and how many bank lines."""
    await _viewer(tenant_id, request)
    from datetime import date as _date
    from vula.commerce import statement_files
    try:
        start = _date.fromisoformat(year[:10]) if year else None
    except ValueError:
        raise HTTPException(status_code=400, detail="year is the first day of the financial year, e.g. 2026-03-01")
    return await asyncio.to_thread(statement_files.coverage, tenant_id, start)


@router.post("/{tenant_id}/admin/books-audit/docs/{doc_id}/link-invoice")
async def link_own_invoice(tenant_id: str, doc_id: str, body: OfBody, request: Request) -> dict:
    _, actor = await _full(tenant_id, request)
    from vula.commerce import doc_audit
    return await _fix(doc_audit.link_invoice, tenant_id, doc_id, body.of_id, actor=actor)


class IdsBody(BaseModel):
    ids: List[str]


@router.get("/{tenant_id}/admin/company-docs/proposals")
async def company_doc_proposals(tenant_id: str, request: Request) -> dict:
    """Documents filed elsewhere whose own words say they're company or tax documents (a preview)."""
    await _full(tenant_id, request)
    from vula.commerce import company_docs
    return {"documents": await asyncio.to_thread(company_docs.proposals, tenant_id)}


@router.post("/{tenant_id}/admin/company-docs/refile")
async def company_doc_refile(tenant_id: str, body: IdsBody, request: Request) -> dict:
    _, actor = await _full(tenant_id, request)
    from vula.commerce import company_docs
    return await _fix(company_docs.refile, tenant_id, body.ids, actor=actor)
