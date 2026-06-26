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

import hashlib
import hmac
import logging
import re
from typing import Optional

import httpx
from fastapi import APIRouter, Header, HTTPException, Query, Request
from fastapi.responses import PlainTextResponse

from config import settings

logger = logging.getLogger(__name__)

router = APIRouter(tags=["whatsapp"])

# Intent keywords (case-insensitive)
_DONE_RE = re.compile(r"^\s*(done|yes|complete|completed|finish|finished|klaar)\s*$", re.IGNORECASE)
_APPROVE_RE = re.compile(r"^\s*approve[d]?\s*(.*)$", re.IGNORECASE)
_REJECT_RE = re.compile(r"^\s*reject\s*(.*)$", re.IGNORECASE)
_DELETE_RE = re.compile(r"^\s*(delete|stop|unsubscribe|opt[\s-]?out)\s*$", re.IGNORECASE)

# ── Number → tenant router ────────────────────────────────────────────────────
# Maps a Meta phone_number_id (the number a person messaged) to the tenant that
# owns it, plus how that line behaves:
#   "commerce"  → seafood/product ordering flow (open to the public)
#   "knowledge" → the tenant's AI model answers questions (open to anyone on
#                  this line — the number IS the tenant's dedicated line)
# Add a number here (or, later, in vula_whatsapp_accounts) to put it live.
# This is the single source of truth — "add a number, point it at a tenant".
_NUMBER_ROUTING: dict[str, tuple[str, str]] = {
    "1124076000792176": ("off-the-hook", "commerce"),   # +27 67 363 6081 — OTH orders bot (live)
    "1180015145200511": ("digg-demo",    "knowledge"),  # +27 66 566 9387 — DIGG assistant
}


def _resolve_number_route(phone_number_id: str) -> tuple[str | None, str | None]:
    """Resolve (tenant_id, mode) for the number a message came in on."""
    route = _NUMBER_ROUTING.get(phone_number_id)
    return route if route else (None, None)

# Commerce order keywords — triggers seafood ordering flow
_ORDER_RE = re.compile(
    r"(order|buy|get|want|fish|snoek|yellowtail|kabeljou|kob|crayfish|prawn|mussel|calamari|box|catch|fresh|seafood|voel|vis)",
    re.IGNORECASE,
)

# Idempotency cache for inbound message IDs
_processed_msg_ids: list[str] = []
_MAX_PROCESSED_IDS = 1000


# ─── Meta verification handshake ─────────────────────────────────────────────

@router.get("/webhook")
async def verify_webhook(
    hub_mode: str = Query(alias="hub.mode", default=""),
    hub_verify_token: str = Query(alias="hub.verify_token", default=""),
    hub_challenge: str = Query(alias="hub.challenge", default=""),
) -> PlainTextResponse:
    """Meta calls this once to verify your webhook URL.

    Meta expects the challenge echoed back verbatim as plain text — it is an
    opaque string, not always numeric, so don't cast it to int.
    """
    if hub_mode == "subscribe" and hub_verify_token == settings.whatsapp_verify_token:
        logger.info("WhatsApp webhook verified")
        return PlainTextResponse(hub_challenge)
    raise HTTPException(status_code=403, detail="Webhook verification failed")


# ─── Inbound message handler ─────────────────────────────────────────────────

@router.post("/webhook")
async def receive_message(
    request: Request,
    x_hub_signature_256: Optional[str] = Header(None, alias="X-Hub-Signature-256"),
) -> dict:
    """Receive inbound WhatsApp messages from Meta."""
    # 1. Verify signature if app secret is configured
    raw_body = await request.body()
    if settings.vula_fb_app_secret:
        if not x_hub_signature_256:
            logger.warning("Rejecting WhatsApp POST: missing X-Hub-Signature-256")
            raise HTTPException(status_code=403, detail="Missing signature")

        # Signature is "sha256=HEX_DIGEST"
        try:
            expected_sig = x_hub_signature_256.split("=")[1]
        except IndexError:
            raise HTTPException(status_code=403, detail="Malformed signature")

        actual_sig = hmac.new(
            settings.vula_fb_app_secret.encode(),
            raw_body,
            hashlib.sha256
        ).hexdigest()

        if not hmac.compare_digest(actual_sig, expected_sig):
            logger.warning("Rejecting WhatsApp POST: HMAC mismatch")
            raise HTTPException(status_code=403, detail="Invalid signature")

    # 2. Parse JSON
    try:
        import json
        body = json.loads(raw_body)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON")

    for entry in body.get("entry", []):
        for change in entry.get("changes", []):
            value = change.get("value", {})
            messages = value.get("messages", [])
            # Diagnostic: log what each webhook event actually carries so we can
            # tell real messages apart from status callbacks during testing.
            _pnid = value.get("metadata", {}).get("phone_number_id", "")
            logger.info(
                "WA webhook: field=%s pnid=%s messages=%d statuses=%d",
                change.get("field", ""), _pnid,
                len(messages), len(value.get("statuses", [])),
            )
            for msg in messages:
                phone = msg.get("from", "")
                msg_id = msg.get("id", "")
                msg_type = msg.get("type", "")

                # 3. Idempotency check
                if msg_id:
                    if msg_id in _processed_msg_ids:
                        logger.debug("Skipping duplicate WhatsApp message: %s", msg_id)
                        continue
                    _processed_msg_ids.append(msg_id)
                    if len(_processed_msg_ids) > _MAX_PROCESSED_IDS:
                        _processed_msg_ids.pop(0)

                # Route by the number the person messaged → (tenant, mode)
                phone_number_id = value.get("metadata", {}).get("phone_number_id", "")
                route_tenant, route_mode = _resolve_number_route(phone_number_id)
                commerce_tenant = route_tenant if route_mode == "commerce" else None

                if msg_type == "text":
                    text = msg.get("text", {}).get("body", "").strip()
                    if phone and text:
                        if route_mode == "commerce":
                            # Number is a shop line → ordering flow
                            await _handle_commerce_message(phone, text, msg_id, route_tenant)
                        elif route_mode == "knowledge":
                            # Number is a tenant's assistant line → that tenant's model
                            await _handle_message(phone, text, msg_id, route_tenant_id=route_tenant)
                        else:
                            # Unmapped number → fall back to sender-based lookup
                            await _handle_message(phone, text, msg_id)

                elif msg_type == "interactive" and commerce_tenant:
                    # Handle list/button replies from WhatsApp catalog menu
                    interactive = msg.get("interactive", {})
                    reply_id = (
                        interactive.get("list_reply", {}).get("id")
                        or interactive.get("button_reply", {}).get("id")
                        or ""
                    )
                    reply_title = (
                        interactive.get("list_reply", {}).get("title")
                        or interactive.get("button_reply", {}).get("title")
                        or ""
                    )
                    if phone and reply_id:
                        await _handle_commerce_interactive(phone, reply_id, reply_title, msg_id, commerce_tenant)

                elif msg_type == "document":
                    doc = msg.get("document") or {}
                    media_id = doc.get("id", "")
                    filename = doc.get("filename", "")
                    mime_type = doc.get("mime_type", "")
                    if phone and media_id:
                        await _handle_document_ingest(
                            phone, media_id, filename, mime_type, route_tenant_id=route_tenant
                        )

                elif msg_type in ("image", "video"):
                    media = msg.get(msg_type) or {}
                    media_id = media.get("id", "")
                    caption = media.get("caption", "")
                    mime_type = media.get("mime_type", "image/jpeg")
                    if phone and media_id:
                        if route_mode == "knowledge":
                            # Dedicated knowledge line → ingest the image into the KB
                            fname = caption or f"image-{msg_id}.jpg"
                            await _handle_document_ingest(
                                phone, media_id, fname, mime_type, route_tenant_id=route_tenant
                            )
                        else:
                            # Shared line → treat as field-ops task evidence
                            await _handle_media(phone, media_id, caption, msg_id)

    return {"status": "ok"}


# ─── Message routing ─────────────────────────────────────────────────────────

async def _handle_message(phone: str, text: str, msg_id: str, route_tenant_id: Optional[str] = None) -> None:
    """Route an inbound text message.

    Two ways the tenant is resolved:
      • route_tenant_id set → the message came in on a tenant's *dedicated*
        line (number→tenant router). Anyone messaging that line talks to that
        tenant's model — no per-sender registration needed.
      • route_tenant_id None → fall back to looking the *sender* up in
        tenant_phones / field_ops (shared line).

    Routing priority within a tenant:
      1. Field-ops intents (DONE / APPROVE / REJECT).
      2. KB / RAG.
    """
    logger.info("WhatsApp inbound from %s: %s", phone, text[:80])

    if route_tenant_id:
        # Dedicated tenant line — the number identifies the tenant, so grant
        # this sender full assistant access on that tenant's model.
        tenant_id = route_tenant_id
        role = "admin"
        contractor = None
    else:
        # Resolve tenant + role from the tenant_phones table
        from vula.models.tenants import get_tenant_db
        lookup = get_tenant_db().lookup_by_phone_with_role(phone)
        tenant_id = lookup["tenant_id"] if lookup else None
        role = lookup["role"] if lookup else None  # admin | staff | viewer | None

        # Also check field_ops contractors table (they won't be in tenant_phones)
        from vula.models.field_ops import get_field_ops_db
        field_db = get_field_ops_db()
        contractor = field_db.get_contractor_by_phone(phone) if not tenant_id else None

        # If phone is completely unknown, reply once and stop
        if not tenant_id and not contractor:
            await _send_reply(phone, (
                "Hi! I'm Vula, your construction AI. "
                "I couldn't find an account linked to this number. "
                "Contact your site manager to get set up."
            ))
            return

        # Resolve tenant_id from contractor if needed
        if not tenant_id and contractor:
            tenant_id = contractor.tenant_id

    # ── Data deletion / opt-out (POPIA + Meta requirement) ───────────────────
    if _DELETE_RE.match(text):
        await _handle_data_deletion(phone, tenant_id)
        return

    # ── Field-ops intents (any phone, no role check needed) ──────────────────
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

    # ── KB / RAG — staff and admin only ─────────────────────────────────────
    # Contractors who didn't trigger a field-ops intent get a gentle redirect
    # rather than full KB access.
    if role is None:
        # Phone came from contractors table only — no KB access
        await _send_reply(
            phone,
            "Hi! I can only help you with your tasks. "
            "Reply DONE when you've finished a task, or send a photo as evidence. "
            "For anything else, contact your site manager."
        )
        return

    if role == "viewer":
        await _send_reply(phone, "You have read-only access. Contact your admin to upgrade.")
        return

    # admin and staff get full RAG
    project_id = _active_project_for_phone(phone)
    thread_key = f"{phone}:{project_id}" if project_id else phone

    from vula.chat.history import get_db
    db = get_db()
    db.save(tenant_id, thread_key, "user", text)
    history = db.format_for_prompt(tenant_id, thread_key, limit=5)
    reply = await _rag_reply(tenant_id, text, conversation_history=history)
    db.save(tenant_id, thread_key, "assistant", reply)
    # Pass tenant_id so the reply is sent FROM the tenant's own number
    # (per-tenant creds in vula_whatsapp_accounts), not the shared test line.
    await _send_reply(phone, reply, tenant_id=tenant_id)

    # Auto-learn: feed substantive Q&A back into the KB so Vula gets smarter.
    # Gated behind AUTO_LEARN_FROM_CHATS (default off) — each learned exchange
    # costs an embedding call. Enable when budget allows.
    import os as _os
    if role == "admin" and _os.environ.get("AUTO_LEARN_FROM_CHATS", "false").lower() == "true":
        await _maybe_learn_from_exchange(tenant_id, text, reply)


async def _maybe_learn_from_exchange(tenant_id: str, question: str, answer: str) -> None:
    """Ingest a valuable Q&A exchange into the tenant KB as a learned fact.

    Filters out greetings, errors, and trivial exchanges. Only durable,
    information-rich answers become permanent knowledge.
    """
    # Skip if the answer is an error/fallback or too short to be useful
    _SKIP = ("having trouble", "couldn't find", "don't have enough",
             "i can only help", "read-only access")
    low = answer.lower()
    if any(s in low for s in _SKIP) or len(answer) < 120 or len(question) < 15:
        return
    # Skip pure greetings/commands
    if question.strip().lower() in ("hi", "hello", "hey", "thanks", "thank you", "ok", "okay"):
        return

    try:
        from vula.ingestion.pipeline import VulaIngestionPipeline
        import hashlib
        pipeline = VulaIngestionPipeline(tenant_id=tenant_id)
        learned = f"Q: {question}\n\nA: {answer}"
        doc_id = "learned_" + hashlib.md5(question.lower().encode()).hexdigest()[:12]
        await pipeline.ingest_text(
            content=learned,
            filename=f"{doc_id}.txt",
            doc_id=doc_id,
        )
        logger.info("Learned from exchange for tenant %s (%s)", tenant_id, doc_id)
    except Exception as exc:
        logger.debug("Auto-learn skipped for %s: %s", tenant_id, exc)


async def _handle_document_ingest(
    phone: str, media_id: str, filename: str, mime_type: str,
    route_tenant_id: Optional[str] = None,
) -> None:
    """Ingest a document/image sent by a tenant into their knowledge base.

    On a tenant's dedicated line (route_tenant_id set) anyone may upload — the
    number identifies the tenant. Otherwise the sender must be a registered
    admin. PDFs, Word, Excel, plain text, and images are accepted.
    """
    logger.info("WhatsApp document from %s: %s (%s)", phone, filename, mime_type)

    if route_tenant_id:
        # Dedicated tenant line — the number is the tenant; treat as admin.
        tenant_id = route_tenant_id
        role = "admin"
    else:
        from vula.models.tenants import get_tenant_db
        lookup = get_tenant_db().lookup_by_phone_with_role(phone)
        if not lookup:
            await _send_reply(
                phone,
                "I couldn't find a Vula account linked to this number. "
                "Contact your Vula representative to get set up."
            )
            return

        tenant_id = lookup["tenant_id"]
        role = lookup["role"]

        if role != "admin":
            await _send_reply(
                phone,
                "Only admins can upload documents. "
                "Ask your Vula admin to upload this for you."
            )
            return

    # Only ingest known document/image types
    _INGESTABLE_MIMES = {
        "application/pdf",
        "application/msword",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "application/vnd.ms-excel",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "text/plain",
        "text/csv",
        "image/jpeg",
        "image/png",
        "image/webp",
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

        # Ingest via pipeline — always adds to KB so Vula learns from every doc
        from vula.ingestion.pipeline import VulaIngestionPipeline
        pipeline = VulaIngestionPipeline(tenant_id=tenant_id)
        result = await pipeline.ingest_file(local_path)

        if result.status not in ("success", "done"):
            await _send_reply(
                phone,
                f"There was a problem ingesting '{result.filename}': {result.error or 'unknown error'}. "
                f"Please try again or contact your Vula rep."
            )
            return

        # For PDFs/images that look like invoices or receipts, also attempt
        # auto-scan → commit to books. Only for admin role.
        scan_msg = ""
        if role == "admin" and local_path.suffix.lower() in (".pdf", ".jpg", ".jpeg", ".png"):
            try:
                import base64, httpx as _httpx
                from config import settings as _s

                # Read file as base64 for the vision scanner
                img_b64 = base64.b64encode(local_path.read_bytes()).decode()

                async with _httpx.AsyncClient(timeout=60.0) as client:
                    scan_resp = await client.post(
                        f"http://localhost:{_s.api_port}/v1/commerce/{tenant_id}/admin/scan",
                        headers={"X-API-Key": _s.api_key, "Content-Type": "application/json"},
                        json={"image_base64": img_b64, "doc_type": "auto"},
                    )
                    if scan_resp.status_code == 200:
                        scan_data = scan_resp.json().get("extracted", {})
                        doc_type = scan_data.get("doc_type", "")
                        total = scan_data.get("total_cents", 0) or 0

                        if doc_type in ("receipt", "invoice", "delivery_note") and total > 0:
                            # Auto-commit to books
                            commit_resp = await client.post(
                                f"http://localhost:{_s.api_port}/v1/commerce/{tenant_id}/admin/scan/commit",
                                headers={"X-API-Key": _s.api_key, "Content-Type": "application/json"},
                                json={"extracted": scan_data, "auto_commit": True},
                            )
                            if commit_resp.status_code == 200:
                                commit_data = commit_resp.json()
                                scan_msg = f"\n\n📊 {commit_data.get('message', '')}"
            except Exception as scan_exc:
                logger.debug("Auto-scan skipped for %s: %s", filename, scan_exc)

        # Deep analysis: classify by CONTENT + pull structured fields → backend.
        analysis = await _analyze_document(tenant_id, result.filename, local_path)
        if analysis:
            doc_category = analysis["category"]
            # Allocate the structured info into the Vula backend (best-effort).
            try:
                from vula.commerce import service as commerce_service
                commerce_service._client().table("vula_document_extractions").insert({
                    "tenant_id": tenant_id,
                    "filename": result.filename,
                    "category": doc_category,
                    "summary": analysis.get("summary", ""),
                    "fields": analysis.get("fields", {}),
                    "doc_id": result.doc_id,
                    "source": "whatsapp",
                }).execute()
            except Exception as exc:
                logger.debug("Extraction store skipped (run migration 011?): %s", exc)
            breakdown = _format_extraction(analysis)
            summary = analysis.get("summary", "")
            msg = (
                f"✅ Filed '{result.filename}' as *{doc_category}* — "
                f"{result.chunks_stored} chunks added."
            )
            if summary:
                msg += f"\n\n📄 {summary}"
            if breakdown:
                msg += f"\n\n{breakdown}"
            msg += f"\n\nAsk me anything about it.{scan_msg}"
            await _send_reply(phone, msg, tenant_id)
        else:
            # Fallback to keyword classification if deep analysis was unavailable
            doc_category = _classify_document(result.filename, local_path)
            await _send_reply(
                phone,
                f"✅ Filed '{result.filename}' as *{doc_category}* — "
                f"{result.chunks_stored} knowledge chunks added. "
                f"I can now answer questions about it.{scan_msg}",
                tenant_id,
            )
    except Exception as exc:
        logger.error("Document ingest failed for %s: %s", phone, exc)
        await _send_reply(phone, f"Something went wrong ingesting '{filename}'. Please try again.")


_DOC_CATEGORIES = [
    "Fee Proposal / Schedule", "Contract / Agreement", "Bill of Quantities (BOQ)",
    "Quote / Estimate", "Invoice", "Drawing / Plan", "Specification",
    "Meeting Minutes", "Programme / Schedule", "Report", "Tender Document",
    "General Document",
]


async def _analyze_document(tenant_id: str, filename: str, local_path) -> Optional[dict]:
    """Deep-analyze an uploaded document: read its content, classify it, and
    pull out the structured fields worth keeping in the backend.

    Returns {"category", "summary", "fields"} or None if analysis fails.
    Best-effort — the document is already in the KB regardless.
    """
    # 1. Extract text using the same parser the ingestion pipeline uses
    try:
        from vula.ingestion.pipeline import VulaIngestionPipeline
        pipeline = VulaIngestionPipeline(tenant_id=tenant_id)
        pages = await pipeline.parser.parse(local_path)
        text = "\n".join(t for _, t in pages).strip()[:6000]
    except Exception as exc:
        logger.debug("Doc analyze: parse failed for %s: %s", filename, exc)
        return None
    if not text:
        return None

    # 2. One LLM call → category + summary + structured fields
    try:
        import json as _json
        import litellm
        from core.llm_router import resolve_generation_route
        litellm.drop_params = True
        model, api_key, api_base = await resolve_generation_route()

        cats = ", ".join(_DOC_CATEGORIES)
        resp = await litellm.acompletion(
            model=model,
            messages=[
                {"role": "system", "content":
                    "You are Vula's document analyst for a South African construction/business. "
                    "Read the document and return STRICT JSON only (no prose) with keys: "
                    f"category (one of: {cats}), summary (1-2 sentences), and fields (an object of "
                    "the key structured data for that category — e.g. invoice: vendor, invoice_no, "
                    "date, subtotal, vat, total; BOQ/quote: client, total, and items as a list of "
                    "{description, qty, unit, rate, amount}; fee proposal: client, stages, total; "
                    "contract: parties, value, dates. Money as numbers in ZAR. Use null when unknown)."},
                {"role": "user", "content": f"Filename: {filename}\n\nDocument:\n{text}\n\nJSON:"},
            ],
            temperature=0.1,
            max_tokens=900,
            api_key=api_key,
            api_base=api_base,
        )
        raw = (resp.choices[0].message.content or "").strip()
        raw = re.sub(r"^```(?:json)?|```$", "", raw, flags=re.MULTILINE).strip()
        data = _json.loads(raw)
        cat = data.get("category") or "General Document"
        if cat not in _DOC_CATEGORIES:
            cat = "General Document"
        return {
            "category": cat,
            "summary": (data.get("summary") or "").strip(),
            "fields": data.get("fields") or {},
        }
    except Exception as exc:
        logger.warning("Doc analyze LLM failed for %s: %s", filename, exc)
        return None


def _format_extraction(analysis: dict) -> str:
    """Turn a structured analysis into a short WhatsApp breakdown."""
    lines = []
    fields = analysis.get("fields") or {}
    for k, v in fields.items():
        if v is None or v == "" or k == "items":
            continue
        if isinstance(v, (dict, list)):
            continue
        label = k.replace("_", " ").title()
        lines.append(f"• {label}: {v}")
    items = fields.get("items")
    if isinstance(items, list) and items:
        lines.append(f"• Line items: {len(items)}")
    return "\n".join(lines[:8])


def _classify_document(filename: str, path) -> str:
    """Classify an uploaded document so it's filed under the right category.

    Looks at the filename and the first part of the extracted text for
    construction/business document signals.
    """
    name = (filename or "").lower()
    sample = ""
    try:
        if path.suffix.lower() in (".txt", ".csv", ".md"):
            sample = path.read_text(encoding="utf-8", errors="ignore")[:1500].lower()
    except Exception:
        pass
    blob = name + " " + sample

    rules = [
        ("Fee Proposal / Schedule", ["fee proposal", "fee schedule", "fee estimate", "sacap fee", "professional fee"]),
        ("Contract / Agreement",    ["jbcc", "agreement", "contract", "procsa", "appointment", "terms and conditions"]),
        ("Bill of Quantities (BOQ)",["bill of quantities", "boq", "quantities", "measured"]),
        ("Quote / Estimate",        ["quotation", "quote", "estimate", "pricing"]),
        ("Invoice",                 ["invoice", "tax invoice", "vat no", "amount due"]),
        ("Drawing / Plan",          ["drawing", "floor plan", "elevation", "section", "site plan", ".dwg"]),
        ("Specification",           ["specification", "spec", "scope of works", "scope of work"]),
        ("Meeting Minutes",         ["minutes", "site meeting", "progress meeting"]),
        ("Programme / Schedule",    ["programme", "gantt", "construction schedule", "critical path"]),
        ("Report",                  ["report", "assessment", "inspection"]),
        ("Tender Document",         ["tender", "rfp", "rfq", "bid"]),
    ]
    for label, keywords in rules:
        if any(k in blob for k in keywords):
            return label
    return "General Document"


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

    from vula.models.field_ops import get_field_ops_db
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
    db.save_evidence(task.id, contractor.id, photo_url, caption)

    photo_count = db.count_evidence(task.id)
    db.update_task_status(task.id, "awaiting_sign_off")

    # Vision: does the photo match the contractor's task for the day?
    task_desc = getattr(task, "description", "") or ""
    assessment = await _assess_evidence_photo(photo_url, task.title, task_desc)

    await _send_reply(
        phone,
        f"Photo received ({photo_count} total for '{task.title}'). "
        f"Sent to your site manager for review. Reply DONE when you've submitted all photos."
    )

    # Notify the architect/site manager — with the AI's assessment
    team = db.get_project_team(task.project_id)
    managers = [m for m in team if m["role"] in ("architect", "site_manager")]
    assess_line = f"\n\n🔍 AI check: {assessment}" if assessment else ""
    for manager in managers:
        await _send_reply(
            manager["phone"],
            f"📸 {contractor.name} submitted photo {photo_count} for task '{task.title}' "
            f"(project {task.project_id}).{assess_line}\n"
            f"Reply APPROVE or REJECT <reason> to sign off."
        )


async def _handle_data_deletion(phone: str, tenant_id: Optional[str]) -> None:
    """Handle a DELETE / STOP / opt-out request — POPIA + Meta compliance.

    Removes the requester's data: tenant_phones entry, chat history, and
    flags the request. Confirms back to the user.
    """
    logger.info("Data deletion request from %s (tenant=%s)", phone, tenant_id)
    removed = []

    # Remove from tenant_phones registry
    try:
        from vula.models.tenants import get_tenant_db
        db = get_tenant_db()
        db.remove_phone(phone)
        removed.append("contact record")
    except Exception as exc:
        logger.warning("tenant_phones removal failed for %s: %s", phone, exc)

    # Clear chat history
    try:
        from vula.chat.history import get_db
        chat_db = get_db()
        if tenant_id:
            chat_db.clear(tenant_id, phone)
            removed.append("message history")
    except Exception as exc:
        logger.warning("chat history removal failed for %s: %s", phone, exc)

    await _send_reply(
        phone,
        "✅ Your data deletion request has been received. "
        f"We've removed your {', '.join(removed) if removed else 'records'} from Vula. "
        "Any remaining data will be deleted within 30 days per POPIA. "
        "For a full deletion or questions, email hello@vula.co.za."
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


async def _assess_evidence_photo(photo_path: str, task_title: str, task_desc: str) -> str:
    """Vision-check a contractor's evidence photo against their task.

    Returns a one-line assessment (starts with ✅ or ⚠️) or "" if vision is
    unavailable. Best-effort — never blocks the evidence flow.
    """
    if not photo_path or photo_path.startswith("media://"):
        return ""
    try:
        from pathlib import Path as _Path
        p = _Path(photo_path)
        if not p.exists() or p.suffix.lower() not in (".jpg", ".jpeg", ".png", ".webp"):
            return ""

        from core.llm_router import resolve_cloud_vision_route
        route = resolve_cloud_vision_route()
        if not route:
            return ""
        model, api_key, api_base = route

        import base64
        import litellm
        litellm.drop_params = True
        img_b64 = base64.b64encode(p.read_bytes()).decode()
        desc = f"{task_title}. {task_desc}".strip(". ")
        resp = await litellm.acompletion(
            model=model,
            messages=[{
                "role": "user",
                "content": [
                    {"type": "text", "text":
                        "A construction site worker submitted this photo as proof of work for the "
                        f"task: \"{desc}\". Does the photo plausibly show THAT work in progress or "
                        "complete? Reply in ONE short line: start with ✅ if it looks consistent or "
                        "⚠️ if it may not match, then a brief reason (what you see)."},
                    {"type": "image_url",
                     "image_url": {"url": f"data:image/jpeg;base64,{img_b64}"}},
                ],
            }],
            temperature=0.1,
            max_tokens=120,
            api_key=api_key,
            api_base=api_base,
        )
        return (resp.choices[0].message.content or "").strip()
    except Exception as exc:
        logger.debug("Evidence photo assessment failed: %s", exc)
        return ""


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
    """Answer a question — routes through the multi-agent runner.

    The agent uses HRM to pick the right skill(s): KB recall, web research
    (SA tenders, company info, live prices), architecture planning, etc.,
    runs them in parallel, and merges. Falls back to plain RAG if the agent
    errors, then to the shared construction KB.
    """
    # 1. Try the full multi-agent runner (research + memory + all skills)
    try:
        from core.agent_runner import get_agent_runner
        runner = get_agent_runner()
        result = await runner.run(
            question=question,
            tenant_id=tenant_id,
            conversation_history=conversation_history,
            max_branches=1,    # cost cap: 1 LLM call per WhatsApp reply
            max_tokens=500,    # speed cap: concise WhatsApp answers generate faster
            top_k=3,           # speed cap: fewer KB chunks = faster retrieval
        )
        if result.final_answer and result.final_answer.strip():
            logger.info(
                "Agent answered tenant=%s skill=%s confidence=%.2f",
                tenant_id, result.skill_used, result.confidence,
            )
            return result.final_answer
    except Exception as exc:
        logger.warning("Agent runner failed for %s, falling back to RAG: %s", tenant_id, exc)

    # 2. Fallback: plain tenant RAG, then shared construction KB
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
            "I don't have enough information to answer that yet. "
            "Try uploading more files in your Vula dashboard or rephrase the question."
        )
    except Exception as exc:
        logger.error("RAG pipeline error for tenant %s: %s", tenant_id, exc)
        return "I'm having trouble accessing your knowledge base right now. Please try again in a few minutes."


# ─── Outbound send (reused by field_ops API too) ──────────────────────────────

_wa_creds_cache: dict[str, dict] = {}


async def _get_tenant_wa_creds(tenant_id: str) -> dict | None:
    """
    Fetch WhatsApp credentials for a tenant from Supabase.
    Falls back to env vars for backwards compatibility.
    Results cached in-process (invalidated on next startup).
    """
    if tenant_id in _wa_creds_cache:
        return _wa_creds_cache[tenant_id]

    try:
        from supabase import create_client
        client = create_client(
            settings.supabase_url,
            settings.supabase_service_role_key or settings.supabase_service_key,
        )
        result = (
            client.table("vula_whatsapp_accounts")
            .select("phone_number_id,access_token,status")
            .eq("tenant_id", tenant_id)
            .eq("status", "connected")
            .maybe_single()
            .execute()
        )
        if result.data and result.data.get("access_token"):
            creds = {
                "token": result.data["access_token"],
                "phone_id": result.data["phone_number_id"],
            }
            _wa_creds_cache[tenant_id] = creds
            return creds
    except Exception as exc:
        logger.debug("Supabase WA creds lookup failed for %s: %s", tenant_id, exc)

    # Fallback: env vars (used during initial setup before Embedded Signup is configured)
    if settings.whatsapp_token and settings.whatsapp_phone_id:
        return {"token": settings.whatsapp_token, "phone_id": settings.whatsapp_phone_id}

    return None


async def _send_reply(to: str, message: str, tenant_id: str = "") -> bool:
    """
    Send a WhatsApp text message via the Meta Graph API.
    Credentials resolved per-tenant from Supabase, falling back to env vars.
    """
    creds = await _get_tenant_wa_creds(tenant_id) if tenant_id else None
    if not creds:
        # Try global env var fallback
        if settings.whatsapp_token and settings.whatsapp_phone_id:
            creds = {"token": settings.whatsapp_token, "phone_id": settings.whatsapp_phone_id}
        else:
            logger.info("WhatsApp not configured — skipping reply to %s", to)
            return False

    number = to.lstrip("+").replace(" ", "").replace("-", "")
    if number.startswith("0"):
        number = "27" + number[1:]

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(
                f"https://graph.facebook.com/v19.0/{creds['phone_id']}/messages",
                headers={
                    "Authorization": f"Bearer {creds['token']}",
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


async def _send_invoice_document(
    to: str,
    pdf_bytes: bytes,
    filename: str,
    caption: str = "",
    tenant_id: str = "",
) -> bool:
    """Send a PDF as a WhatsApp document via the Meta Graph API.

    The PDF is first uploaded to Meta's media endpoint, then delivered as a
    ``document`` message referencing the returned media id — this avoids needing
    a publicly reachable URL. Credentials are resolved per-tenant from Supabase,
    falling back to env vars, exactly like ``_send_reply``.
    """
    creds = await _get_tenant_wa_creds(tenant_id) if tenant_id else None
    if not creds:
        if settings.whatsapp_token and settings.whatsapp_phone_id:
            creds = {"token": settings.whatsapp_token, "phone_id": settings.whatsapp_phone_id}
        else:
            logger.info("WhatsApp not configured — skipping document to %s", to)
            return False

    number = to.lstrip("+").replace(" ", "").replace("-", "")
    if number.startswith("0"):
        number = "27" + number[1:]

    base = f"https://graph.facebook.com/v19.0/{creds['phone_id']}"
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            # 1. Upload the PDF to Meta and obtain a media id
            upload = await client.post(
                f"{base}/media",
                headers={"Authorization": f"Bearer {creds['token']}"},
                data={"messaging_product": "whatsapp", "type": "application/pdf"},
                files={"file": (filename, pdf_bytes, "application/pdf")},
            )
            upload.raise_for_status()
            media_id = upload.json().get("id")
            if not media_id:
                logger.error("WhatsApp media upload returned no id for %s", to)
                return False

            # 2. Send the document message referencing the uploaded media
            document: dict = {"id": media_id, "filename": filename}
            if caption:
                document["caption"] = caption[:1024]
            resp = await client.post(
                f"{base}/messages",
                headers={
                    "Authorization": f"Bearer {creds['token']}",
                    "Content-Type": "application/json",
                },
                json={
                    "messaging_product": "whatsapp",
                    "to": number,
                    "type": "document",
                    "document": document,
                },
            )
            resp.raise_for_status()
            logger.info("WhatsApp document sent to %s", to)
            return True
    except Exception as exc:
        logger.error("WhatsApp document send failed to %s: %s", to, exc)
        return False


# ─── Commerce ordering flow ───────────────────────────────────────────────────

async def _handle_commerce_message(phone: str, text: str, msg_id: str, tenant_id: str) -> None:
    """Route inbound messages for commerce tenants (e.g. Off the Hook customers).

    Greetings get the interactive welcome menu. Everything else is handled by the
    commerce_assistant AI skill (tool-calling agent over products/cart/orders,
    grounded with the tenant knowledge base and persisted multi-turn memory).
    n8n remains a last-resort fallback if the skill is unavailable.
    """
    text_lower = text.lower().strip()

    # 1. Greeting — send welcome + menu
    greeting_words = {"hi", "hello", "hallo", "hey", "howzit", "good morning", "goeie dag"}
    if any(text_lower.startswith(w) for w in greeting_words):
        await _send_commerce_welcome(phone, tenant_id)
        return

    # 2. Supplier Intake — OTH-07 logic
    supplier_keywords = {"supply", "sell", "catch", "supplier", "verskaf", "fish for you"}
    if any(k in text_lower for k in supplier_keywords):
        reply = (
            "Thanks for reaching out! 🐟 We're always looking for quality suppliers. "
            "Please complete our intake form here: https://offthehook.co.za/suppliers "
            "Our team will review it and get back to you."
        )
        await _send_reply(phone, reply, tenant_id)
        # Log lead to Supabase (assuming table exists or using a generic log)
        try:
            from vula.commerce import service as commerce_service
            commerce_service._client().table("supplier_leads").insert({
                "tenant_id": tenant_id,
                "phone": phone,
                "raw_text": text,
                "source": "whatsapp"
            }).execute()
        except Exception:
            pass
        return

    # Owner/staff → admin agent (run the shop); customers → shopping agent.
    if _is_tenant_owner(tenant_id, phone):
        if await _run_commerce_admin(phone, text, tenant_id):
            return
        # Admin agent failed → fall through to the customer assistant.

    handled = await _run_commerce_assistant(phone, text, tenant_id)
    if not handled:
        # Skill unavailable/failed — fall back to n8n, then a holding reply.
        await _forward_to_n8n_commerce(phone, text, msg_id, tenant_id)


def _is_tenant_owner(tenant_id: str, phone: str) -> bool:
    """Is this phone an owner/staff of the tenant (→ admin agent)?

    Source of truth is the per-tenant team registry in vula.api.yoco
    (_TENANT_TEAM). Adding a tenant's owner/staff there enables the admin agent
    for them automatically — the same mechanism for every tenant.
    """
    try:
        from vula.api.yoco import _TENANT_TEAM
    except Exception:
        return False

    def _digits(p: str) -> str:
        n = "".join(ch for ch in (p or "") if ch.isdigit())
        return "27" + n[1:] if n.startswith("0") else n

    target = _digits(phone)
    for _name, team_phone, role in _TENANT_TEAM.get(tenant_id, []):
        if _digits(team_phone) == target and role in ("owner", "operations", "staff", "admin"):
            return True
    return False


async def _run_commerce_admin(phone: str, text: str, tenant_id: str) -> bool:
    """Drive the commerce_admin skill (owner running the shop) with memory."""
    try:
        from core.skills.base import SkillInput
        from core.skills.loader import get_skill
        from vula.commerce import service as commerce_service
    except Exception as exc:  # pragma: no cover — import guard
        logger.warning("commerce_admin unavailable: %s", exc)
        return False

    history = ""
    session_id: Optional[str] = None
    try:
        session = await commerce_service.get_or_create_session(
            tenant_id, session_key=f"admin:{phone}", channel="whatsapp", customer_phone=phone
        )
        session_id = session["id"]
        history = commerce_service.format_history(
            await commerce_service.get_recent_messages(tenant_id, session_id, limit=12)
        )
    except Exception as exc:
        logger.debug("Admin session/history load failed (non-fatal): %s", exc)

    skill = get_skill("commerce_admin")
    output = await skill(
        SkillInput(
            question=text, tenant_id=tenant_id, conversation_history=history,
            metadata={"session_id": f"admin:{phone}", "customer_phone": phone},
        )
    )
    if not output.success or not output.answer:
        logger.warning("commerce_admin returned no answer: %s", output.error)
        return False

    await _send_reply(phone, output.answer, tenant_id)
    if session_id:
        try:
            await commerce_service.append_message(tenant_id, session_id, "user", text)
            await commerce_service.append_message(tenant_id, session_id, "assistant", output.answer)
        except Exception:
            pass
    return True


async def _run_commerce_assistant(phone: str, text: str, tenant_id: str) -> bool:
    """Drive the commerce_assistant skill with persisted conversation memory.

    Returns True if a reply was sent, False if the skill could not produce one.
    """
    try:
        from core.skills.base import SkillInput
        from core.skills.loader import get_skill
        from vula.commerce import service as commerce_service
    except Exception as exc:  # pragma: no cover — import guard
        logger.warning("commerce_assistant unavailable: %s", exc)
        return False

    # Load (or create) the session and recent history for multi-turn memory.
    history = ""
    session_id: Optional[str] = None
    try:
        session = await commerce_service.get_or_create_session(
            tenant_id, session_key=phone, channel="whatsapp", customer_phone=phone
        )
        session_id = session["id"]
        history = commerce_service.format_history(
            await commerce_service.get_recent_messages(tenant_id, session_id, limit=12)
        )
    except Exception as exc:
        logger.debug("Commerce session/history load failed (non-fatal): %s", exc)

    skill = get_skill("commerce_assistant")
    output = await skill(
        SkillInput(
            question=text,
            tenant_id=tenant_id,
            conversation_history=history,
            metadata={"session_id": phone, "customer_phone": phone},
        )
    )

    if not output.success:
        logger.warning("commerce_assistant returned no answer: %s", output.error)
        return False

    await _send_reply(phone, output.answer, tenant_id)

    # Persist this turn so the next message has context.
    if session_id:
        try:
            await commerce_service.append_message(tenant_id, session_id, "user", text)
            await commerce_service.append_message(tenant_id, session_id, "assistant", output.answer)
        except Exception as exc:
            logger.debug("Commerce message persistence failed (non-fatal): %s", exc)

    return True


async def _handle_commerce_interactive(
    phone: str, reply_id: str, reply_title: str, msg_id: str, tenant_id: str
) -> None:
    """Handle interactive list/button replies from the WhatsApp product catalog."""
    # Forward to n8n — it maintains the conversation state and draft order
    await _forward_to_n8n_commerce(phone, reply_id, msg_id, tenant_id, reply_title=reply_title)


async def _send_commerce_welcome(phone: str, tenant_id: str) -> None:
    """Send the Off the Hook welcome message with interactive category list."""
    if not settings.whatsapp_token:
        return

    # Per-tenant phone number IDs — Off the Hook uses its own dedicated number
    _TENANT_PHONE_IDS: dict[str, str] = {
        "off-the-hook": "1124076000792176",  # +27 67 363 6081 (system-user WABA)
    }
    phone_number_id = _TENANT_PHONE_IDS.get(tenant_id) or settings.whatsapp_phone_number_id
    if not phone_number_id:
        await _send_reply(phone, (
            "Welcome to Off the Hook! 🐟\n\n"
            "Cape Town's freshest daily catch, door to door.\n\n"
            "What are you looking for?\n"
            "1. Linefish (yellowtail, snoek, kob)\n"
            "2. Shellfish & prawns\n"
            "3. Crayfish\n"
            "4. Box deals\n"
            "5. Smoked fish\n\n"
            "Reply with a number or product name, or visit offthehook.co.za"
        ))
        return

    async with httpx.AsyncClient(timeout=10.0) as client:
        await client.post(
            f"https://graph.facebook.com/v19.0/{phone_number_id}/messages",
            headers={"Authorization": f"Bearer {settings.whatsapp_token}"},
            json={
                "messaging_product": "whatsapp",
                "to": phone,
                "type": "interactive",
                "interactive": {
                    "type": "list",
                    "header": {"type": "text", "text": "Off the Hook 🐟"},
                    "body": {"text": "Cape Town's freshest catch, door to door.\n\nWhat are you after today?"},
                    "footer": {"text": "Free delivery on orders over R500"},
                    "action": {
                        "button": "View menu",
                        "sections": [
                            {
                                "title": "Today's catch",
                                "rows": [
                                    {"id": "cat_linefish", "title": "Linefish", "description": "Yellowtail, snoek, kob, red roman"},
                                    {"id": "cat_shellfish", "title": "Shellfish & prawns", "description": "Tiger prawns, mussels, calamari"},
                                    {"id": "cat_crayfish", "title": "Crayfish", "description": "West Coast rock lobster"},
                                    {"id": "cat_box_deal", "title": "Box deals", "description": "Braai box, weekly catch box"},
                                    {"id": "cat_smoked", "title": "Smoked fish", "description": "Hot-smoked yellowtail, snoek pâté"},
                                ],
                            }
                        ],
                    },
                },
            },
        )


async def _forward_to_n8n_commerce(
    phone: str, text: str, msg_id: str, tenant_id: str, reply_title: str = ""
) -> None:
    """Forward message to n8n for AI-powered order processing."""
    n8n_base = getattr(settings, "n8n_webhook_base", None)
    if not n8n_base:
        await _send_reply(phone, "On it! Our team will be in touch shortly. 🐟")
        return

    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            await client.post(
                f"{n8n_base}/whatsapp-inbound",
                json={
                    "phone": phone,
                    "text": text,
                    "reply_title": reply_title,
                    "msg_id": msg_id,
                    "tenant_id": tenant_id,
                },
            )
    except Exception as exc:
        logger.warning("n8n commerce forward failed (non-fatal): %s", exc)
        await _send_reply(phone, "Got it! We'll be in touch in a few minutes. 🐟")
