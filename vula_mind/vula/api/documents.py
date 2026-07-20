"""
vula/api/documents.py — Vula Documents library (filed documents by project).

    GET  /v1/documents/{tenant}/filed?project=   → filed documents (grouped client-side)
    GET  /v1/documents/{tenant}/projects          → project labels for the assign dropdown
    POST /v1/documents/{id}/assign-project        → manually file an Unfiled document

Filed records are written by the WhatsApp ingest path (vula/integrations/doc_filing.py).
"""
from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter
from pydantic import BaseModel

log = logging.getLogger(__name__)
router = APIRouter(tags=["documents"])


def _client():
    from vula.commerce import service as commerce_service
    return commerce_service._client()


@router.get("/{tenant_id}/filed")
async def list_filed(
    tenant_id: str, project: Optional[str] = None, commerce_invoice_id: Optional[str] = None,
) -> dict:
    """Filed documents for a tenant (newest first), optionally filtered by project or by the
    commerce_invoices row they were promoted into (migration 102's bridge column) — lets the
    Invoices tab link an inbound invoice back to the real scanned/emailed document it came from."""
    try:
        q = (_client().table("vula_filed_documents").select("*")
             .eq("tenant_id", tenant_id).order("created_at", desc=True).limit(500))
        if project:
            q = q.eq("project", project)
        if commerce_invoice_id:
            q = q.eq("commerce_invoice_id", commerce_invoice_id)
        rows = q.execute().data or []
    except Exception as exc:
        log.warning("documents list failed (run migration 015?): %s", exc)
        rows = []
    return {"tenant_id": tenant_id, "documents": rows, "count": len(rows)}


@router.get("/{tenant_id}/projects")
async def projects(tenant_id: str) -> dict:
    """Project labels (ClickUp lists + field-ops) for the assign-project dropdown."""
    out: list[dict] = []
    try:
        from vula.integrations.doc_filing import _clickup_candidates, _project_label, _field_projects
        seen = set()
        for lid, lname in _clickup_candidates(tenant_id):
            label = _project_label(lname)
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
    clickup_list_id: Optional[str] = None


@router.post("/{doc_id}/assign-project")
async def assign_project(doc_id: str, body: AssignIn) -> dict:
    """Manually file a document under a project (and attach to ClickUp if mapped)."""
    try:
        res = _client().table("vula_filed_documents").select("*").eq("id", doc_id).limit(1).execute()
        rows = res.data or []
    except Exception as exc:
        return {"error": str(exc)}
    if not rows:
        return {"error": "Document not found."}
    doc = rows[0]

    clickup_list_id, clickup_task_id = body.clickup_list_id, None
    if body.clickup_list_id and doc.get("file_url"):
        try:
            import httpx
            from vula.integrations.doc_filing import attach_into_project
            async with httpx.AsyncClient(timeout=60.0) as client:
                fb = await client.get(doc["file_url"])
                fb.raise_for_status()
                data = fb.content
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
