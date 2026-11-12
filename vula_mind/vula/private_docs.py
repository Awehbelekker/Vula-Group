"""vula/private_docs.py — documents (and a bank account) private to one team member.

12 Nov 2026 (Ian, DIGG): "Private account is kept for Richard only, if he wants to ask questions about
it; this is his data and only visible to him." Richard had sent his own FNB statements (…5256) on
WhatsApp; they were filed as DIGG documents, so the owner could see them in Documents and Vula could
quote them to anyone at DIGG.

- A private document lives in vula_private_documents (migration 261), never in vula_filed_documents:
  Documents, reports, project files and client links read the business's table, so none of them can
  include one by accident. Its knowledge-base chunks carry private_to = the member's id, and every
  search leaves them out unless it is answering that member (vula/ingestion/pipeline.py::kb_viewer).
- A bank account marked private to a member (commerce_bank_accounts.private_to): its statements are
  never read into the business's bank lines; they are filed privately for that member.
- A document filed for the business before it was known to be private is moved (vula_private_moves).

Before migration 261 every function here is a harmless no-op.
"""
from __future__ import annotations

import logging
import re
from typing import Any, Dict, List, Optional

log = logging.getLogger(__name__)


def _client():
    from vula.commerce.service import _client as c
    return c()


def member_id_for_phone(tenant_id: str, phone: Optional[str]) -> Optional[str]:
    """The team member writing from `phone`, or None (a customer, or the lookup failed)."""
    if not phone:
        return None
    try:
        from vula import team_index
        m = team_index.member_for_phone(tenant_id, phone)
        return str(m["id"]) if m and m.get("id") else None
    except Exception:
        return None


def _drop_business_copies(tenant_id: str, doc_id: Optional[str]) -> None:
    """The document's summary/fields also sit in the business's extractions; remove them."""
    if not doc_id:
        return
    try:
        _client().table("vula_document_extractions").delete().eq("tenant_id", tenant_id).eq("doc_id", doc_id).execute()
    except Exception as exc:
        log.warning("extraction copy not removed for %s/%s: %s", tenant_id, doc_id, exc)


def private_accounts(tenant_id: str) -> Dict[str, str]:
    """{account number: member id} for the business's private accounts. {} before migration 261."""
    try:
        rows = (_client().table("commerce_bank_accounts").select("account_number,private_to")
                .eq("tenant_id", tenant_id).execute().data or [])
    except Exception:
        return {}
    return {r["account_number"]: str(r["private_to"]) for r in rows if r.get("private_to") and r.get("account_number")}


def owner_of(tenant_id: str, *texts: Any) -> Optional[str]:
    """The member a document is private to, when it names one of the business's private accounts."""
    accounts = private_accounts(tenant_id)
    if not accounts:
        return None
    blob = " ".join(str(t) for t in texts if t)
    digits = set(re.findall(r"\d{8,16}", re.sub(r"(?<=\d)[ \-](?=\d)", "", blob)))
    return next((m for n, m in accounts.items() if n in digits), None)


async def store(tenant_id: str, member_id: str, *, filename: str, doc_id: Optional[str],
                category: str = "", summary: str = "", fields: Optional[dict] = None,
                source: str = "", file_url: Optional[str] = None, content_hash: Optional[str] = None) -> Dict[str, Any]:
    """File a document privately for `member_id`, and make its knowledge-base chunks theirs alone."""
    row = {"tenant_id": tenant_id, "member_id": str(member_id), "filename": filename, "doc_id": doc_id,
           "category": category, "summary": summary, "fields": fields or {}, "source": source,
           "file_url": file_url, "content_hash": content_hash}
    saved: Dict[str, Any] = {}
    try:
        saved = (_client().table("vula_private_documents").insert(row).execute().data or [{}])[0]
    except Exception as exc:
        log.warning("private document not saved for %s: %s", tenant_id, exc)
    hidden = True
    if doc_id:
        from vula.ingestion.pipeline import VulaIngestionPipeline
        hidden = await VulaIngestionPipeline(tenant_id=tenant_id).store.set_private(tenant_id, doc_id, member_id)
        _drop_business_copies(tenant_id, doc_id)
    return {**row, **saved, "status": "private", "private": True, "hidden": hidden}


async def move(tenant_id: str, filed_document_id: str, member_id: str) -> Dict[str, Any]:
    """Move a document filed for the business to one member's private documents. The knowledge chunks
    are made private first; the business's copy is removed only once that is confirmed."""
    db = _client()
    rows = (db.table("vula_filed_documents").select("*").eq("tenant_id", tenant_id)
            .eq("id", filed_document_id).limit(1).execute().data or [])
    if not rows:
        return {"error": "not found"}
    d = rows[0]
    if d.get("doc_id"):
        from vula.ingestion.pipeline import VulaIngestionPipeline
        if not await VulaIngestionPipeline(tenant_id=tenant_id).store.set_private(tenant_id, d["doc_id"], member_id):
            return {"error": "knowledge chunks not made private yet"}
    db.table("vula_private_documents").insert({
        "tenant_id": tenant_id, "member_id": str(member_id), "filename": d.get("filename") or "",
        "doc_id": d.get("doc_id"), "file_url": d.get("file_url"), "category": d.get("category"),
        "summary": d.get("summary"), "fields": d.get("fields") or {}, "source": d.get("source"),
        "content_hash": d.get("content_hash")}).execute()
    db.table("vula_filed_documents").delete().eq("tenant_id", tenant_id).eq("id", filed_document_id).execute()
    _drop_business_copies(tenant_id, d.get("doc_id"))
    left = (db.table("vula_filed_documents").select("id").eq("id", filed_document_id).limit(1).execute().data or [])
    return {"error": "business copy still there"} if left else {"ok": True, "filename": d.get("filename")}


async def process_moves() -> int:
    """Carry out the queued moves (vula_private_moves). Returns how many were done."""
    try:
        todo = (_client().table("vula_private_moves").select("*").is_("done_at", "null")
                .limit(100).execute().data or [])
    except Exception:
        return 0                                   # before migration 261
    done = 0
    for t in todo:
        try:
            res = await move(t["tenant_id"], str(t["filed_document_id"]), str(t["member_id"]))
        except Exception as exc:
            res = {"error": str(exc)[:200]}
        patch = {"done_at": "now()", "error": None} if res.get("ok") or res.get("error") == "not found" \
            else {"error": res.get("error")}
        try:
            _client().table("vula_private_moves").update(patch).eq("id", t["id"]).execute()
        except Exception:
            pass
        done += 1 if res.get("ok") else 0
    if todo:
        log.info("private documents: %s of %s moves done", done, len(todo))
    return done


def list_for(tenant_id: str, member_id: str) -> List[Dict[str, Any]]:
    """One member's private documents (newest first) — only ever for that member."""
    if not member_id:
        return []
    try:
        return (_client().table("vula_private_documents").select("id,filename,category,summary,created_at,source")
                .eq("tenant_id", tenant_id).eq("member_id", str(member_id))
                .order("created_at", desc=True).limit(500).execute().data or [])
    except Exception:
        return []
