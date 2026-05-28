"""
vula/api/whatsapp.py

Vula WhatsApp inbound webhook.

Meta sends inbound messages here via POST.
GET is the verification handshake Meta calls once when you register the webhook.

Flow:
  Client sends WhatsApp → Meta Graph API → POST /v1/whatsapp/webhook
  → extract phone + message text / media
  → look up tenant by phone number in Supabase
  → detect field-ops intent (DONE / APPROVE / REJECT / photo)
  → if field-ops intent: update task/sign-off state, notify project team
  → otherwise: run RAG query against tenant knowledge base and reply

Threading:
  Each (tenant_id, phone, project_id) tuple is a separate conversation thread.
  A contractor on 3 projects has 3 independent threads.
  project_id is resolved from the contractor's active task context.
"""
from __future__ import annotations

import logging
import re
from typing import Optional

import httpx
from fastapi import APIRouter, HTTPException, Query, Request

from config import settings

logger = logging.getLogger(__name__)

router = APIRouter(tags=["whatsapp"])

# Intent keywords (case-insensitive)
_DONE_RE = re.compile(r"^\s*(done|yes|complete|completed|finish|finished|klaar)\s*$", re.IGNORECASE)
_APPROVE_RE = re.compile(r"^\s*approve[d]?\s*(.*)$", re.IGNORECASE)
_REJECT_RE = re.compile(r"^\s*reject\s*(.*)$", re.IGNORECASE)


# ─── Meta verification handshake ─────────────────────────────────────────────

@router.get("/webhook")
async def verify_webhook(
    hub_mode: str = Query(alias="hub.mode", default=""),
    hub_verify_token: str = Query(alias="hub.verify_token", default=""),
    hub_challenge: str = Query(alias="hub.challenge", default=""),
) -> int:
    """Meta calls this once to verify your webhook URL."""
    if hub_mode == "subscribe" and hub_verify_token == settings.whatsapp_verify_token:
        logger.info("WhatsApp webhook verified")
        return int(hub_challenge)
    raise HTTPException(status_code=403, detail="Webhook verification failed")


# ─── Inbound message handler ─────────────────────────────────────────────────

@router.post("/webhook")
async def receive_message(request: Request) -> dict:
    """Receive inbound WhatsApp messages from Meta."""
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON")

    for entry in body.get("entry", []):
        for change in entry.get("changes", []):
            value = change.get("value", {})
            messages = value.get("messages", [])
            for msg in messages:
                phone = msg.get("from", "")
                msg_id = msg.get("id", "")
                msg_type = msg.get("type", "")

                if msg_type == "text":
                    text = msg.get("text", {}).get("body", "").strip()
                    if phone and text:
                        await _handle_message(phone, text, msg_id)

                elif msg_type == "document":
                    doc = msg.get("document") or {}
                    media_id = doc.get("id", "")
                    filename = doc.get("filename", "")
                    mime_type = doc.get("mime_type", "")
                    if phone and media_id:
                        await _handle_document_ingest(phone, media_id, filename, mime_type)

                elif msg_type in ("image", "video"):
                    media_id = (msg.get(msg_type) or {}).get("id", "")
                    caption = (msg.get(msg_type) or {}).get("caption", "")
                    if phone and media_id:
                        await _handle_media(phone, media_id, caption, msg_id)

    return {"status": "ok"}


# ─── Message routing ─────────────────────────────────────────────────────────

async def _handle_message(phone: str, text: str, msg_id: str) -> None:
    """Route an inbound text message — field-ops intent or RAG fallback."""
    logger.info("WhatsApp inbound from %s: %s", phone, text[:80])

    tenant_id = await _tenant_for_phone(phone)
    if not tenant_id:
        await _send_reply(phone, (
            "Hi! I'm Vula, your construction AI. "
            "I couldn't find an account linked to this number. "
            "Contact your Vula representative to get set up."
        ))
        return

    # Check for field-ops intents first
    if _DONE_RE.match(text):
        await _handle_task_complete(phone, tenant_id)
        return

    approve_m = _APPROVE_RE.match(text)
    if approve_m:
        notes = approve_m.group(1).strip()
        await _handle_sign_off_reply(phone, tenant_id, "approved", notes)
        return

    reject_m = _REJECT_RE.match(text)
    if reject_m:
        notes = reject_m.group(1).strip()
        await _handle_sign_off_reply(phone, tenant_id, "rejected", notes)
        return

    # Regular RAG conversation — per-project thread if contractor is known
    project_id = _active_project_for_phone(phone)
    thread_key = f"{phone}:{project_id}" if project_id else phone

    from vula.chat.history import get_db
    db = get_db()
    db.save(tenant_id, thread_key, "user", text)
    history = db.format_for_prompt(tenant_id, thread_key, limit=5)
    reply = await _rag_reply(tenant_id, text, conversation_history=history)
    db.save(tenant_id, thread_key, "assistant", reply)
    await _send_reply(phone, reply)


async def _handle_document_ingest(phone: str, media_id: str, filename: str, mime_type: str) -> None:
    """Ingest a document sent by a tenant into their knowledge base.

    Any registered tenant can WhatsApp a PDF, Word, or Excel file and it
    will be automatically ingested.  No dashboard needed.
    """
    logger.info("WhatsApp document from %s: %s (%s)", phone, filename, mime_type)

    tenant_id = await _tenant_for_phone(phone)
    if not tenant_id:
        await _send_reply(
            phone,
            "I couldn't find a Vula account linked to this number. "
            "Contact your Vula representative to get set up."
        )
        return

    # Only ingest known document types
    _INGESTABLE_MIMES = {
        "application/pdf",
        "application/msword",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "application/vnd.ms-excel",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "text/plain",
        "text/csv",
    }
    if mime_type and mime_type not in _INGESTABLE_MIMES:
        await _send_reply(
            phone,
            f"I can't ingest '{filename or 'that file'}' yet. "
            f"Send PDFs, Word docs, Excel files, or plain text."
        )
        return

    await _send_reply(
        phone,
        f"Got it! Ingesting '{filename or 'your document'}' into your knowledge base. "
        f"This takes 1-3 minutes. I'll confirm when it's ready."
    )

    try:
        # Download from Meta
        local_path = await _download_document(media_id, tenant_id, filename, mime_type)
        if not local_path:
            await _send_reply(phone, f"Sorry, I couldn't download '{filename}'. Please try again.")
            return

        # Ingest via pipeline
        from vula.ingestion.pipeline import VulaIngestionPipeline
        pipeline = VulaIngestionPipeline(tenant_id=tenant_id)
        result = await pipeline.ingest_file(local_path)

        if result.status == "done":
            await _send_reply(
                phone,
                f"✅ '{result.filename}' ingested — {result.chunks_stored} knowledge chunks added. "
                f"I can now answer questions about this document."
            )
        else:
            await _send_reply(
                phone,
                f"There was a problem ingesting '{result.filename}': {result.error or 'unknown error'}. "
                f"Please try again or contact your Vula rep."
            )
    except Exception as exc:
        logger.error("Document ingest failed for %s: %s", phone, exc)
        await _send_reply(phone, f"Something went wrong ingesting '{filename}'. Please try again.")


async def _download_document(media_id: str, tenant_id: str, filename: str, mime_type: str):
    """Download a document from Meta Graph API and save to the upload directory."""
    if not settings.whatsapp_token:
        logger.warning("WHATSAPP_TOKEN not set — cannot download media")
        return None

    # Determine file extension from mime type
    _MIME_EXT = {
        "application/pdf": ".pdf",
        "application/msword": ".doc",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document": ".docx",
        "application/vnd.ms-excel": ".xls",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": ".xlsx",
        "text/plain": ".txt",
        "text/csv": ".csv",
    }
    ext = _MIME_EXT.get(mime_type, "")
    if filename and not ext:
        ext = "." + filename.rsplit(".", 1)[-1] if "." in filename else ""
    safe_name = (filename or f"whatsapp_{media_id}").replace("/", "_").replace("\\", "_")
    if ext and not safe_name.endswith(ext):
        safe_name += ext

    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            # Step 1: get download URL
            info = await client.get(
                f"{settings.whatsapp_api_url}/{media_id}",
                headers={"Authorization": f"Bearer {settings.whatsapp_token}"},
            )
            info.raise_for_status()
            download_url = info.json().get("url", "")
            if not download_url:
                return None

            # Step 2: download content
            dl = await client.get(
                download_url,
                headers={"Authorization": f"Bearer {settings.whatsapp_token}"},
            )
            dl.raise_for_status()

        dest_dir = settings.upload_dir / tenant_id
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest = dest_dir / safe_name
        dest.write_bytes(dl.content)
        logger.info("Document saved: %s (%d bytes)", dest, len(dl.content))
        return dest

    except Exception as exc:
        logger.error("Document download failed for media_id=%s: %s", media_id, exc)
        return None


async def _handle_media(phone: str, media_id: str, caption: str, msg_id: str) -> None:
    """Handle an inbound photo/document — save as task evidence."""
    logger.info("WhatsApp media from %s, id=%s", phone, media_id)

    tenant_id = await _tenant_for_phone(phone)
    if not tenant_id:
        return

    from vula.models.field_ops import get_field_ops_db, _normalise_phone
    db = get_field_ops_db()
    contractor = db.get_contractor_by_phone(phone)
    if not contractor:
        await _send_reply(phone, "Thanks for the photo! Ask your site manager to register you in Vula so it can be linked to your task.")
        return

    # Find the contractor's active task awaiting sign-off or in-progress
    active_tasks = db.get_tasks_for_contractor(
        contractor.id, status="in_progress"
    ) or db.get_tasks_for_contractor(contractor.id, status="awaiting_sign_off")

    if not active_tasks:
        await _send_reply(phone, "Thanks for the photo! I don't see an active task for you right now. Ask your site manager to assign you a task.")
        return

    task = active_tasks[0]

    # Download media from Meta and save locally
    photo_url = await _download_media(media_id, contractor.id, task.id)
    evidence = db.save_evidence(task.id, contractor.id, photo_url, caption)

    photo_count = db.count_evidence(task.id)
    db.update_task_status(task.id, "awaiting_sign_off")

    await _send_reply(
        phone,
        f"Photo received ({photo_count} total for '{task.title}'). "
        f"Sent to your site manager for review. Reply DONE when you've submitted all photos."
    )

    # Notify the architect/site manager
    team = db.get_project_team(task.project_id)
    managers = [m for m in team if m["role"] in ("architect", "site_manager")]
    for manager in managers:
        await _send_reply(
            manager["phone"],
            f"📸 {contractor.name} submitted photo {photo_count} for task '{task.title}' "
            f"(project {task.project_id}).\n"
            f"Reply APPROVE or REJECT <reason> to sign off."
        )


async def _handle_task_complete(phone: str, tenant_id: str) -> None:
    """Mark the contractor's current in-progress task as awaiting sign-off."""
    from vula.models.field_ops import get_field_ops_db
    db = get_field_ops_db()
    contractor = db.get_contractor_by_phone(phone)
    if not contractor:
        await _send_reply(phone, "I couldn't find your contractor profile. Ask your site manager to register you in Vula.")
        return

    active_tasks = db.get_tasks_for_contractor(contractor.id, status="in_progress")
    if not active_tasks:
        active_tasks = db.get_tasks_for_contractor(contractor.id, status="pending")

    if not active_tasks:
        await _send_reply(phone, "I don't see any active tasks assigned to you. Ask your site manager to check your assignments.")
        return

    task = active_tasks[0]
    db.update_task_status(task.id, "awaiting_sign_off")

    await _send_reply(
        phone,
        f"Task '{task.title}' marked as complete. "
        f"Your site manager has been notified and will sign it off. "
        f"Send photos of the finished work if required."
    )

    # Notify managers
    team = db.get_project_team(task.project_id)
    managers = [m for m in team if m["role"] in ("architect", "site_manager")]
    for manager in managers:
        await _send_reply(
            manager["phone"],
            f"✅ {contractor.name} completed task '{task.title}' (project {task.project_id}).\n"
            f"Reply APPROVE or REJECT <reason>."
        )


async def _handle_sign_off_reply(phone: str, tenant_id: str, decision: str, notes: str) -> None:
    """Handle APPROVE / REJECT from a site manager or architect.

    Anyone registered in a project with role architect/site_manager/quantity_surveyor
    can approve.  Regular contractors are redirected to reply DONE instead.
    """
    from vula.models.field_ops import get_field_ops_db
    db = get_field_ops_db()

    # Check whether this phone has any manager role on any project
    manager_roles = db.get_manager_roles_for_phone(phone)
    if not manager_roles:
        # Either not registered at all, or a plain contractor
        contractor = db.get_contractor_by_phone(phone)
        if contractor:
            await _send_reply(
                phone,
                "Only site managers and architects can approve tasks. "
                "Reply DONE to mark your own work complete."
            )
        else:
            await _send_reply(
                phone,
                "I couldn't find your profile. Ask your site manager to add you to the project in Vula."
            )
        return

    # Find the most recent task awaiting sign-off in a project they manage
    pending = db.get_tasks_awaiting_signoff_for_manager(phone)
    if not pending:
        await _send_reply(
            phone,
            "No tasks are currently awaiting your sign-off. "
            "Check the Vula dashboard for the latest project status."
        )
        return

    task = pending[0]
    task_id        = task["task_id"]
    task_title     = task["title"]
    contractor_phone = task["contractor_phone"]
    contractor_name  = task["contractor_name"]

    db.record_sign_off(task_id, phone, decision, notes)

    if decision == "approved":
        await _send_reply(phone, f"Task '{task_title}' approved ✅. {contractor_name} has been notified.")
        if contractor_phone:
            await _send_reply(
                contractor_phone,
                f"Your work on '{task_title}' has been approved by your site manager. "
                f"Well done! Check the Vula app for your next task."
            )
    else:
        reason = notes or "no reason given"
        await _send_reply(phone, f"Task '{task_title}' rejected. {contractor_name} has been notified.")
        if contractor_phone:
            await _send_reply(
                contractor_phone,
                f"Your work on '{task_title}' needs attention: {reason}. "
                f"Please fix and reply DONE when ready for re-inspection."
            )


def _active_project_for_phone(phone: str) -> Optional[str]:
    """Return the project_id of the contractor's most recently active task, if any."""
    try:
        from vula.models.field_ops import get_field_ops_db
        db = get_field_ops_db()
        contractor = db.get_contractor_by_phone(phone)
        if not contractor:
            return None
        tasks = db.get_tasks_for_contractor(contractor.id, status="in_progress")
        if not tasks:
            tasks = db.get_tasks_for_contractor(contractor.id, status="pending")
        return tasks[0].project_id if tasks else None
    except Exception:
        return None


# ─── Media download ───────────────────────────────────────────────────────────

async def _download_media(media_id: str, contractor_id: str, task_id: str) -> str:
    """Download a media file from Meta Graph API and save to evidence dir."""
    if not settings.whatsapp_token:
        return f"media://{media_id}"  # placeholder when not configured

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            # Get the download URL
            resp = await client.get(
                f"{settings.whatsapp_api_url}/{media_id}",
                headers={"Authorization": f"Bearer {settings.whatsapp_token}"},
            )
            resp.raise_for_status()
            media_url = resp.json().get("url", "")
            if not media_url:
                return f"media://{media_id}"

            # Download the file
            dl = await client.get(
                media_url,
                headers={"Authorization": f"Bearer {settings.whatsapp_token}"},
            )
            dl.raise_for_status()

        from vula.models.field_ops import get_field_ops_db
        evidence_dir = get_field_ops_db().evidence_dir / task_id
        evidence_dir.mkdir(parents=True, exist_ok=True)

        content_type = dl.headers.get("content-type", "image/jpeg")
        ext = content_type.split("/")[-1].split(";")[0].strip() or "jpg"
        path = evidence_dir / f"{media_id}.{ext}"
        path.write_bytes(dl.content)
        return str(path)

    except Exception as exc:
        logger.error("Media download failed for %s: %s", media_id, exc)
        return f"media://{media_id}"


# ─── Tenant lookup ────────────────────────────────────────────────────────────

async def _tenant_for_phone(phone: str) -> Optional[str]:
    """Look up a tenant by their WhatsApp number.

    Tries Supabase first (when configured with a real URL).
    Falls back to the local SQLite tenant registry — this lets the full
    stack run locally without any cloud dependency.
    """
    from vula.models.tenants import get_tenant_db

    _PLACEHOLDER = "your-project.supabase.co"
    supabase_live = (
        settings.supabase_url
        and settings.supabase_service_key
        and _PLACEHOLDER not in settings.supabase_url
    )

    if supabase_live:
        normalised = phone.lstrip("+").replace(" ", "").replace("-", "")
        variants = {normalised}
        if normalised.startswith("27") and len(normalised) == 11:
            variants.add("0" + normalised[2:])
        elif normalised.startswith("0") and len(normalised) == 10:
            variants.add("27" + normalised[1:])

        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                for variant in variants:
                    resp = await client.get(
                        f"{settings.supabase_url}/rest/v1/vula_tenants"
                        f"?whatsapp=eq.{variant}&select=tenant_id&limit=1",
                        headers={
                            "apikey": settings.supabase_service_key,
                            "Authorization": f"Bearer {settings.supabase_service_key}",
                        },
                    )
                    rows = resp.json() if resp.is_success else []
                    if rows:
                        return rows[0]["tenant_id"]
        except Exception as exc:
            logger.error("Supabase tenant lookup failed for %s: %s — falling back to local", phone, exc)

    # Local SQLite fallback (always active)
    tenant_id = get_tenant_db().lookup_by_phone(phone)
    if tenant_id:
        logger.debug("Tenant resolved from local registry: %s → %s", phone, tenant_id)
    return tenant_id


# ─── RAG reply ────────────────────────────────────────────────────────────────

async def _rag_reply(tenant_id: str, question: str, conversation_history: str = "") -> str:
    """Run the question through the tenant's RAG pipeline."""
    try:
        from vula.ingestion.pipeline import VulaIngestionPipeline
        from vula.training.content import TRAINING_TENANT_ID

        pipeline = VulaIngestionPipeline(tenant_id=tenant_id)
        sources = await pipeline.query(question, top_k=5)

        if sources:
            return await pipeline.answer(
                question,
                context_label="business documents",
                conversation_history=conversation_history,
            )

        training = VulaIngestionPipeline(tenant_id=TRAINING_TENANT_ID)
        training_sources = await training.query(question, top_k=3)
        if training_sources:
            logger.info("Falling back to training KB for tenant %s", tenant_id)
            return await training.answer(
                question,
                context_label="construction knowledge base",
                conversation_history=conversation_history,
            )

        return (
            "I don't have enough information in your documents to answer that yet. "
            "Try uploading more files at app.vula.ai or ask your Vula rep."
        )
    except Exception as exc:
        logger.error("RAG pipeline error for tenant %s: %s", tenant_id, exc)
        return "I'm having trouble accessing your knowledge base right now. Please try again in a few minutes."


# ─── Outbound send (reused by field_ops API too) ──────────────────────────────

async def _send_reply(to: str, message: str) -> bool:
    """Send a WhatsApp text message via the Meta Graph API."""
    if not settings.whatsapp_token or not settings.whatsapp_phone_id:
        logger.info("WhatsApp not configured — skipping reply to %s", to)
        return False

    number = to.lstrip("+").replace(" ", "").replace("-", "")
    if number.startswith("0"):
        number = "27" + number[1:]

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(
                f"{settings.whatsapp_api_url}/{settings.whatsapp_phone_id}/messages",
                headers={
                    "Authorization": f"Bearer {settings.whatsapp_token}",
                    "Content-Type": "application/json",
                },
                json={
                    "messaging_product": "whatsapp",
                    "to": number,
                    "type": "text",
                    "text": {"body": message[:4096]},
                },
            )
            resp.raise_for_status()
            logger.info("WhatsApp reply sent to %s", to)
            return True
    except Exception as exc:
        logger.error("WhatsApp reply failed to %s: %s", to, exc)
        return False
