"""Business profile — dashboard endpoints (vula/commerce/business_profile.py).

Mounted under /v1/commerce, so /v1/commerce/{tenant_id}/admin/... is covered by the tenant guard
(server._TENANT_GUARD_RES): only a member of that tenant (or master / the API key) gets here."""
from __future__ import annotations

import logging
from typing import List, Optional

from fastapi import APIRouter, File, HTTPException, UploadFile

from vula.commerce import business_profile as bp

log = logging.getLogger(__name__)

router = APIRouter(tags=["business-profile"])


@router.get("/{tenant_id}/admin/business-profile")
async def get_profile(tenant_id: str) -> dict:
    return bp.status(tenant_id)


@router.put("/{tenant_id}/admin/business-profile")
async def put_profile(tenant_id: str, body: dict) -> dict:
    answers = (body or {}).get("answers")
    if not isinstance(answers, dict):
        raise HTTPException(status_code=400, detail="answers must be an object")
    # 2026-10-17: the owner's list of answers customers may see (optional; see business_profile.py).
    public_keys = (body or {}).get("public_keys")
    if public_keys is not None and not (isinstance(public_keys, list)
                                        and all(isinstance(k, str) for k in public_keys)):
        raise HTTPException(status_code=400, detail="public_keys must be a list of question keys")
    res = await bp.save_answers(tenant_id, answers, by="dashboard", public_keys=public_keys)
    if not res.get("saved"):
        raise HTTPException(status_code=500, detail=res.get("error") or "Couldn't save")
    return res


# ── drafts: what Vula read from the website or the business's documents ─────────────────────
# The owner used to be able to confirm these only in the WhatsApp interview, so a business
# with no WhatsApp number could never accept them and typed every answer by hand.

@router.post("/{tenant_id}/admin/business-profile/drafts")
async def review_drafts(tenant_id: str, body: dict) -> dict:
    """Keep or throw away drafted answers: {"accept": [keys], "dismiss": [keys]}. An accepted
    draft becomes the answer (and goes into the profile documents Vula answers from); a
    dismissed one is deleted. Returns the profile as it now stands."""
    accept = (body or {}).get("accept") or []
    dismiss = (body or {}).get("dismiss") or []
    if not (isinstance(accept, list) and isinstance(dismiss, list)
            and all(isinstance(k, str) for k in accept + dismiss)):
        raise HTTPException(status_code=400, detail="accept and dismiss must be lists of question keys")
    if not accept and not dismiss:
        raise HTTPException(status_code=400, detail="Nothing to keep or dismiss.")
    accepted: List[str] = []
    if accept:
        res = await bp.accept_drafts(tenant_id, accept, by="dashboard")
        if not res.get("saved"):
            raise HTTPException(status_code=500, detail=res.get("error") or "Couldn't save")
        accepted = res.get("accepted") or []
    dismissed = bp.discard_drafts(tenant_id, dismiss) if dismiss else 0
    return {**bp.status(tenant_id), "accepted": accepted, "dismissed": dismissed}


# ── set up from the business's own documents (vula/commerce/profile_from_docs.py) ───────────

@router.post("/{tenant_id}/admin/setup-from-documents")
async def setup_from_documents(tenant_id: str, files: List[UploadFile] = File(...)) -> dict:
    """Upload company documents (profile, terms, price list, menu, FAQ). Each is filed in the
    knowledge base like any upload, and read for the business profile answers still open — which
    are DRAFTED for the owner to check, never saved as answers. Runs in the background; poll
    GET …/setup-from-documents for progress and GET …/business-profile for the drafts."""
    from config import settings
    from vula.api import server as srv
    from vula.commerce import profile_from_docs as pfd
    from vula.commerce.background_tasks import run_background
    from vula.uploads import safe_upload_path

    srv.validate_tenant(tenant_id)
    why = pfd.reject_reason([f.filename or "" for f in files])
    if why:
        raise HTTPException(status_code=400, detail=why)
    if pfd.already_running(tenant_id):
        raise HTTPException(status_code=409, detail="I'm still reading the last documents — give me a minute.")

    tenant_dir = srv.UPLOAD_DIR / tenant_id
    tenant_dir.mkdir(parents=True, exist_ok=True)
    saved, skipped = [], []
    for f in files:
        content = await f.read()
        if len(content) > settings.max_file_mb * 1024 * 1024:
            skipped.append({"name": f.filename, "reason": f"over {settings.max_file_mb}MB"})
            continue
        if not content:
            skipped.append({"name": f.filename, "reason": "empty file"})
            continue
        path = safe_upload_path(tenant_dir, f.filename)
        path.write_bytes(content)
        saved.append({"name": f.filename, "path": str(path), "mime": f.content_type})
    if not saved:
        raise HTTPException(status_code=400, detail="None of those files could be used: "
                            + "; ".join(f"{s['name']} ({s['reason']})" for s in skipped))

    async def file_it(tid: str, path, mime: Optional[str]) -> None:
        # Exactly what POST /ingest does for one upload: into the knowledge base, then filed.
        from vula.ingestion.pipeline import VulaIngestionPipeline
        result = await VulaIngestionPipeline(tenant_id=tid).ingest_file(path)
        if result.status in ("success", "done"):
            await srv._analyze_and_file_upload(tid, result, path, mime)

    run_background(tenant_id, "setup_from_documents", pfd.run(tenant_id, saved, file_it=file_it))
    return {"status": "queued", "files": [s["name"] for s in saved], "skipped": skipped,
            "message": f"Reading {len(saved)} document{'s' if len(saved) != 1 else ''}. "
                       "What I find will be listed for you to check."}


@router.get("/{tenant_id}/admin/setup-from-documents")
async def setup_from_documents_status(tenant_id: str) -> dict:
    """How the last upload went: per document, whether it was read and how many answers it
    gave — and the drafts now waiting for the owner."""
    from vula.commerce import profile_from_docs as pfd
    return {"status": pfd.status(tenant_id), "drafts": bp.status(tenant_id).get("drafts") or {}}
