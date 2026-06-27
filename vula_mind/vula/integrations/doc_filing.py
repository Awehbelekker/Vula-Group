"""
vula/integrations/doc_filing.py — Smart document filing.

When a tenant WhatsApps a doc/photo, Vula reads + classifies it (whatsapp.py
`_analyze_document`) and ingests it into the KB. This module adds the *filing*
layer: match the document to a project, store a durable copy + a queryable record
in `vula_documents`, and — when the project maps to a ClickUp list — attach the
file into ClickUp (one "📎 Project Documents" task per list).

Project taxonomy: the tenant's ClickUp lists first, field-ops `project_id`s as
fallback. No confident match → the caller asks the user on WhatsApp.
"""
from __future__ import annotations

import logging
import re
import uuid
from typing import Optional

logger = logging.getLogger(__name__)

# Tokens too generic to identify a project.
_STOPWORDS = {
    "team", "space", "list", "phase", "with", "clickup", "started", "get",
    "the", "and", "branch", "project", "documents", "document", "hub", "site",
    "first", "second", "fix", "work", "stage", "stages", "general",
}
_MIN_TOKEN_LEN = 4
_GENERIC_SEGMENTS = {"team space", "space", "list", "get started with clickup"}


def _client():
    from vula.commerce import service as commerce_service
    return commerce_service._client()


def _tokens(text: str) -> set[str]:
    """Distinctive lowercase tokens (≥4 chars, no stopwords) from free text."""
    raw = re.split(r"[^a-z0-9]+", (text or "").lower())
    return {t for t in raw if len(t) >= _MIN_TOKEN_LEN and t not in _STOPWORDS and not t.isdigit()}


def _project_label(list_name: str) -> str:
    """Derive a human project label from a (possibly nested) ClickUp list name,
    e.g. 'Team Space / HPC_Bokaap / Phase 1 — Site' → 'HPC_Bokaap'."""
    segs = [s.strip() for s in str(list_name).split("/") if s.strip()]
    segs = [s for s in segs if s.lower() not in _GENERIC_SEGMENTS]
    non_phase = [s for s in segs if not s.lower().startswith("phase")]
    if non_phase:
        return non_phase[0]
    return segs[0] if segs else str(list_name)


def _clickup_candidates(tenant_id: str) -> list[tuple[str, str]]:
    """(list_id, list_name) for the tenant's ClickUp lists, or []."""
    try:
        from vula.clickup.credentials import get_tenant_clickup_creds
        creds = get_tenant_clickup_creds(tenant_id)
        if not creds:
            return []
        lids = creds.get("list_ids") or {}
        return [(k, v) for k, v in lids.items() if k != "default" and v]
    except Exception:
        return []


def _field_projects(tenant_id: str) -> list[str]:
    """Distinct field-ops project_ids for the tenant, or []."""
    try:
        res = (_client().table("vula_field_tasks").select("project_id")
               .eq("tenant_id", tenant_id).limit(500).execute())
        return sorted({r["project_id"] for r in (res.data or []) if r.get("project_id")})
    except Exception:
        return []


def match_project(tenant_id: str, text: str) -> Optional[dict]:
    """Match free text (caption + summary + extracted fields) to a project.

    Returns {project, clickup_list_id, confidence} or None when nothing matches.
    ClickUp lists are tried first; field-ops projects are the fallback.
    """
    text_tokens = _tokens(text)
    if not text_tokens:
        return None

    best = None  # (score, -len(label), project, list_id)
    for lid, lname in _clickup_candidates(tenant_id):
        overlap = _tokens(lname) & text_tokens
        if not overlap:
            continue
        label = _project_label(lname)
        cand = (len(overlap), -len(label), label, lid)
        if best is None or cand > best:
            best = cand
    if best:
        return {"project": best[2], "clickup_list_id": best[3],
                "confidence": "high" if best[0] >= 2 else "medium"}

    # Fallback: field-ops project ids (no ClickUp list to attach to).
    for pid in _field_projects(tenant_id):
        if _tokens(pid) & text_tokens:
            return {"project": pid, "clickup_list_id": None, "confidence": "medium"}
    return None


def project_examples(tenant_id: str, n: int = 3) -> list[str]:
    """A few project labels to prompt the user with when asking which project."""
    labels: list[str] = []
    for _, lname in _clickup_candidates(tenant_id):
        lab = _project_label(lname)
        if lab not in labels:
            labels.append(lab)
        if len(labels) >= n:
            break
    if not labels:
        labels = _field_projects(tenant_id)[:n]
    return labels


async def file_document(
    tenant_id: str, *, filename: str, data: Optional[bytes], content_type: str,
    category: str = "", summary: str = "", fields: Optional[dict] = None,
    doc_id: str = "", source: str = "whatsapp", filed_by: str = "",
    project: Optional[str] = None, clickup_list_id: Optional[str] = None,
    status: str = "filed",
) -> dict:
    """Store a durable copy + a `vula_documents` row, and (if a ClickUp list is
    given and we have bytes) attach the file into ClickUp. Returns the row dict
    plus `clickup_task_id` when attached.
    """
    file_url = None
    if data:
        safe = re.sub(r"[^A-Za-z0-9._-]+", "_", filename or "document")
        object_path = f"{tenant_id}/{uuid.uuid4().hex}_{safe}"
        try:
            from vula.api.whatsapp import _upload_to_storage
            file_url = _upload_to_storage("documents", object_path, data, content_type)
        except Exception as exc:
            logger.warning("Document storage upload failed: %s", exc)

    clickup_task_id = None
    if clickup_list_id and data:
        try:
            from vula.clickup import service as clickup_service
            res = await clickup_service.attach_file_to_list(
                tenant_id, clickup_list_id, filename, data, content_type)
            clickup_task_id = res.get("task_id")
        except Exception as exc:
            logger.warning("ClickUp attach failed for %s: %s", filename, exc)

    row = {
        "tenant_id": tenant_id, "project": project, "clickup_list_id": clickup_list_id,
        "clickup_task_id": clickup_task_id, "category": category, "summary": summary,
        "fields": fields or {}, "filename": filename, "file_url": file_url,
        "mime": content_type, "doc_id": doc_id, "source": source,
        "status": status, "filed_by": filed_by,
    }
    try:
        ins = _client().table("vula_documents").insert(row).execute()
        if ins.data:
            row["id"] = ins.data[0].get("id")
    except Exception as exc:
        logger.warning("vula_documents insert failed (run migration 015?): %s", exc)
    return row


async def resolve_pending_document(tenant_id: str, phone: str, text: str) -> Optional[dict]:
    """If `phone` has a recent pending_project document, treat `text` as the
    project answer: match it, update the row (project + ClickUp), re-download the
    stored file and attach it into ClickUp. Returns a result dict or None.

    Returns: None (no pending doc), {"unmatched": True, "filename": ...},
    or {"filed": True, "project": ..., "clickup": bool, "filename": ...}.
    """
    try:
        from datetime import datetime, timedelta, timezone
        cutoff = (datetime.now(timezone.utc) - timedelta(minutes=30)).isoformat()
        res = (_client().table("vula_documents").select("*")
               .eq("tenant_id", tenant_id).eq("filed_by", phone)
               .eq("status", "pending_project").gte("created_at", cutoff)
               .order("created_at", desc=True).limit(1).execute())
        rows = res.data or []
    except Exception:
        return None
    if not rows:
        return None
    doc = rows[0]

    # "skip" → leave it unfiled (still stored + in KB).
    if text.strip().lower() in ("skip", "none", "no project", "unfiled", "leave it"):
        try:
            _client().table("vula_documents").update({"status": "filed"}) \
                .eq("id", doc["id"]).execute()
        except Exception:
            pass
        return {"skipped": True, "filename": doc.get("filename")}

    match = match_project(tenant_id, text)
    if not match:
        # Don't hijack an unrelated question — only re-ask for short answers.
        if len(text) > 60 or text.strip().endswith("?"):
            return None
        return {"unmatched": True, "filename": doc.get("filename")}

    clickup_task_id = None
    if match.get("clickup_list_id") and doc.get("file_url"):
        try:
            import httpx
            from vula.clickup import service as clickup_service
            async with httpx.AsyncClient(timeout=60.0) as client:
                fb = await client.get(doc["file_url"])
                fb.raise_for_status()
                data = fb.content
            r = await clickup_service.attach_file_to_list(
                tenant_id, match["clickup_list_id"], doc.get("filename") or "document",
                data, doc.get("mime") or "application/octet-stream")
            clickup_task_id = r.get("task_id")
        except Exception as exc:
            logger.warning("ClickUp attach (pending resolve) failed: %s", exc)

    try:
        _client().table("vula_documents").update({
            "project": match["project"], "clickup_list_id": match.get("clickup_list_id"),
            "clickup_task_id": clickup_task_id, "status": "filed",
        }).eq("id", doc["id"]).execute()
    except Exception as exc:
        logger.warning("Pending doc update failed: %s", exc)

    return {"filed": True, "project": match["project"],
            "clickup": bool(clickup_task_id), "filename": doc.get("filename")}
