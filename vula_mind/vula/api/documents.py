"""
vula/api/documents.py — Vula Documents library (filed documents by project).

    GET  /v1/documents/{tenant}/filed?project=   → filed documents, filterable (see list_filed)
    GET  /v1/documents/{tenant}/projects          → project labels for the assign dropdown
    POST /v1/documents/{id}/assign-project        → manually file an Unfiled document
    POST /v1/documents/{tenant}/media             → "just store this" upload, no OCR/KB-extraction

Filed records are written by the WhatsApp ingest path (vula/integrations/doc_filing.py).
"""
from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, Depends, File, Form, Request, UploadFile
from pydantic import BaseModel

from vula.api.master_auth import authorized_tenant, require_auth

log = logging.getLogger(__name__)
router = APIRouter(tags=["documents"])


def _client():
    from vula.commerce import service as commerce_service
    return commerce_service._client()


@router.get("/{tenant_id}/filed")
async def list_filed(
    tenant_id: str, project: Optional[str] = None, commerce_invoice_id: Optional[str] = None,
    customer_phone: Optional[str] = None, category: Optional[str] = None,
    since: Optional[str] = None, until: Optional[str] = None, search: Optional[str] = None,
    filed_by: Optional[str] = None, limit: int = 100, offset: int = 0,
) -> dict:
    """Filed documents for a tenant (newest first) — real filters (customer/category/date-range/
    text search), not just project, and real pagination (limit/offset, not a flat 500-row cap) so
    the Documents screen can actually be browsed instead of client-side-filtering one giant list.
    `commerce_invoice_id` lets the Invoices tab link an inbound invoice back to the real scanned/
    emailed document it came from (migration 102's bridge column)."""
    try:
        q = (_client().table("vula_filed_documents").select("*", count="exact")
             .eq("tenant_id", tenant_id).order("created_at", desc=True))
        if project:
            q = q.eq("project", project)
        if commerce_invoice_id:
            q = q.eq("commerce_invoice_id", commerce_invoice_id)
        if customer_phone:
            q = q.eq("customer_phone", customer_phone)
        if category:
            q = q.eq("category", category)
        if filed_by:
            q = q.eq("filed_by", filed_by)
        if since:
            q = q.gte("created_at", since)
        if until:
            q = q.lte("created_at", until)
        if search:
            # One search across title, summary, project, type and the supplier/customer/number
            # fields — words in order (doc_quality.search_clauses, 2026-10-02).
            from vula.commerce.doc_quality import search_clauses
            clauses = search_clauses(search)
            if clauses:
                q = q.or_(clauses)
        res = q.range(offset, offset + min(limit, 200) - 1).execute()
        rows = res.data or []
        total = res.count if res.count is not None else len(rows)
    except Exception as exc:
        log.warning("documents list failed (run migration 015/109?): %s", exc)
        rows, total = [], 0
    from vula.storage_links import sign_row
    return {"tenant_id": tenant_id, "documents": sign_row(rows), "count": len(rows), "total": total}


@router.get("/{tenant_id}/health")
async def documents_health(tenant_id: str) -> dict:
    """Documents › Health: catch-alls, missing details, waiting on a project, and project names
    that aren't on the register — with samples to act on (vula/commerce/doc_quality.py)."""
    from vula.commerce.doc_quality import health
    try:
        return {"tenant_id": tenant_id, **health(tenant_id)}
    except Exception as exc:
        log.warning("documents health failed: %s", exc)
        return {"tenant_id": tenant_id, "error": "Couldn't read the documents right now."}


@router.get("/{tenant_id}/projects")
async def projects(tenant_id: str) -> dict:
    """Project labels for the assign-project dropdown: the project register first (2026-10-02 —
    ClickUp list names like "Team Space / Get Started with ClickUp" were offered as projects and
    documents got filed under them), then ClickUp lists and field-ops projects, each mapped to
    its registered name where there is one."""
    out: list[dict] = []
    try:
        from vula.commerce.service import canonical_project, registered_projects
        from vula.integrations.doc_filing import _clickup_candidates, _project_label, _field_projects
        seen = set()
        for r in registered_projects(tenant_id):
            if r.get("name") and (r.get("status") or "active") == "active" and r["name"] not in seen:
                seen.add(r["name"])
                out.append({"label": r["name"], "clickup_list_id": None, "registered": True})
        for lid, lname in _clickup_candidates(tenant_id):
            label = canonical_project(tenant_id, _project_label(lname)) or _project_label(lname)
            if label in seen:
                for o in out:          # the register's entry gains the ClickUp list to attach to
                    if o["label"] == label and not o.get("clickup_list_id"):
                        o["clickup_list_id"] = lid
                continue
            if label not in seen:
                seen.add(label)
                out.append({"label": label, "clickup_list_id": lid})
        for pid in _field_projects(tenant_id):
            if pid not in seen:
                seen.add(pid)
                out.append({"label": pid, "clickup_list_id": None})
    except Exception as exc:
        log.warning("projects list failed: %s", exc)
    return {"tenant_id": tenant_id, "projects": out}


class AssignIn(BaseModel):
    project: str
    tenant_id: Optional[str] = None    # how a signed-in member authenticates (require_auth)
    clickup_list_id: Optional[str] = None


@router.post("/{doc_id}/assign-project", dependencies=[Depends(require_auth)])
async def assign_project(doc_id: str, body: AssignIn, request: Request) -> dict:
    """Manually file a document under a project (and attach to ClickUp if mapped)."""
    try:
        res = _client().table("vula_filed_documents").select("*").eq("id", doc_id).limit(1).execute()
        rows = res.data or []
    except Exception as exc:
        return {"error": str(exc)}
    scope = authorized_tenant(request) or body.tenant_id
    if not rows or (scope and rows[0].get("tenant_id") != scope):
        return {"error": "Document not found."}
    doc = rows[0]
    from vula.commerce.service import canonical_project
    body.project = canonical_project(doc["tenant_id"], body.project) or body.project

    clickup_list_id, clickup_task_id = body.clickup_list_id, None
    if body.clickup_list_id and doc.get("file_url"):
        try:
            from vula.integrations.doc_filing import attach_into_project
            from vula.storage_links import fetch
            data = await fetch(doc["file_url"])
            att = await attach_into_project(
                doc["tenant_id"], body.project, body.clickup_list_id,
                doc.get("filename") or "document", data,
                doc.get("mime") or "application/octet-stream")
            clickup_list_id = att.get("clickup_list_id") or clickup_list_id
            clickup_task_id = att.get("clickup_task_id")
        except Exception as exc:
            log.warning("ClickUp attach (assign) failed: %s", exc)

    try:
        _client().table("vula_filed_documents").update({
            "project": body.project, "clickup_list_id": clickup_list_id,
            "clickup_task_id": clickup_task_id, "status": "filed",
        }).eq("id", doc_id).execute()
    except Exception as exc:
        return {"error": str(exc)}
    # Filed from the dashboard — nobody should still be asked about it on WhatsApp.
    from vula import open_questions
    open_questions.close_for(doc.get("tenant_id") or "", doc_id)

    # The committed bill/quote and the document's prices follow the document, and a BoQ sets
    # the project's contract value — as resolve_pending_document does for the WhatsApp answer.
    if doc.get("commerce_invoice_id"):
        try:
            (_client().table("commerce_invoices").update({"project": body.project})
             .eq("tenant_id", doc["tenant_id"]).eq("id", doc["commerce_invoice_id"]).execute())
        except Exception as exc:
            log.warning("project → commerce_invoices (assign) failed: %s", exc)
    if (doc.get("category") or "") == "Bill of Quantities (BOQ)":
        total_cents = (doc.get("fields") or {}).get("total_cents")
        if total_cents:
            from vula.commerce.service import upsert_project_boq
            upsert_project_boq(doc["tenant_id"], body.project, int(total_cents),
                               sections=(doc.get("fields") or {}).get("sections") or None)
    try:
        from vula.commerce.price_book import set_project
        set_project(doc["tenant_id"], doc_id, body.project)
    except Exception as exc:
        log.debug("price book project (assign) skipped: %s", exc)

    # Same two follow-ups the WhatsApp-reply resolution path already does (doc_filing.py's
    # resolve_pending_document) — this dashboard path was missing both, so a document assigned
    # here never contributed to the project's finances and never taught the auto-filer.
    learned = None
    try:
        from vula.integrations.doc_filing import learn_filing_rule
        learned = learn_filing_rule(doc["tenant_id"], doc.get("fields") or {}, body.project)
    except Exception as exc:
        log.debug("learn filing rule (assign) skipped: %s", exc)
    try:
        from vula.integrations.finances import post_finance_from_doc
        post_finance_from_doc(doc["tenant_id"], body.project, doc.get("fields") or {},
                              doc.get("doc_id"), doc.get("filename") or "",
                              doc.get("summary") or "", doc.get("category") or "")
    except Exception as exc:
        log.debug("finance post (assign) skipped: %s", exc)

    return {"id": doc_id, "project": body.project, "clickup_attached": bool(clickup_task_id),
            "learned_signals": learned}


@router.post("/{tenant_id}/media")
async def upload_media(
    tenant_id: str, file: UploadFile = File(...),
    customer_phone: Optional[str] = Form(None), project: Optional[str] = Form(None),
    caption: Optional[str] = Form(None),
) -> dict:
    """'Just store this' — a photo, form, or any file with nothing to extract, stored straight
    into the documents library with no OCR/KB-ingest attempted (unlike the Smart Scanner upload
    path). Optionally tagged to a customer and/or project. Reuses file_document's storage/dedup
    machinery — same durable copy, same table, just category='media' and no data-extraction step."""
    try:
        from vula.integrations.doc_filing import file_document
        data = await file.read()
        row = await file_document(
            tenant_id, filename=file.filename or "upload", data=data,
            content_type=file.content_type or "application/octet-stream",
            category="media", summary=caption or "", source="dashboard",
            filed_by=f"dashboard:{tenant_id}", project=project,
            customer_phone=customer_phone or None,
        )
        return {"document": row}
    except Exception as exc:
        log.warning("media upload failed for %s: %s", tenant_id, exc)
        return {"error": str(exc)}
