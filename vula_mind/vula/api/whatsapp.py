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
from typing import Any, Dict, Optional

import httpx
from fastapi import APIRouter, Header, HTTPException, Query, Request
from fastapi.responses import PlainTextResponse

from config import settings
from core.transcribe import transcribe_audio
from core.verification import strip_caveat

logger = logging.getLogger(__name__)

router = APIRouter(tags=["whatsapp"])

# Intent keywords (case-insensitive)
_DONE_RE = re.compile(r"^\s*(done|complete|completed|finish|finished|klaar)\s*$", re.IGNORECASE)
# Note: 'yes' intentionally not a DONE trigger — too ambiguous with approvals/chat.
# Tolerant of common misspellings: aprove, approve, approove, authorise, accept…
_APPROVE_RE = re.compile(
    r"^\s*(?:approv\w*|aprrov\w*|aprov\w*|aproov\w*|authoriz\w*|authoris\w*|accept\w*|goedgekeur)\b\s*(.*)$",
    re.IGNORECASE,
)
_REJECT_RE = re.compile(
    r"^\s*(?:reject\w*|rejct\w*|declin\w*|deny|denied|afgekeur)\b\s*(.*)$",
    re.IGNORECASE,
)
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
    "1216487374874418": ("off-the-hook", "commerce"),   # +27 79 178 3933 — OTH orders bot (live; replaced +27 67 363 6081)
    "1124076000792176": ("off-the-hook", "commerce"),   # +27 67 363 6081 — retired, kept routed during transition
    "1180015145200511": ("digg-demo",    "knowledge"),  # +27 66 566 9387 — DIGG assistant
}


_ROUTE_CACHE: dict[str, tuple[float, tuple[str, str]]] = {}
_ROUTE_TTL = 120.0

# Last agent confidence for the current request (set in _rag_reply, read for escalation).
import contextvars as _contextvars
_LAST_CONF = _contextvars.ContextVar("vula_last_conf", default=1.0)


def _resolve_number_route(phone_number_id: str) -> tuple[str | None, str | None]:
    """Resolve (tenant_id, mode) for the number a message came in on.

    Static map first (hot path for known numbers), then DB (vula_whatsapp_accounts)
    so a NEW tenant's number routes with no code change — cached to keep it off the
    per-message hot path."""
    route = _NUMBER_ROUTING.get(phone_number_id)
    if route:
        return route
    import time as _t
    hit = _ROUTE_CACHE.get(phone_number_id)
    if hit and (_t.time() - hit[0]) < _ROUTE_TTL:
        return hit[1]
    try:
        from vula.commerce import service as cs
        rows = (cs._client().table("vula_whatsapp_accounts")
                .select("tenant_id,route_mode").eq("phone_number_id", phone_number_id)
                .limit(1).execute().data or [])
        if rows:
            resolved = (rows[0]["tenant_id"], rows[0].get("route_mode") or "commerce")
            _ROUTE_CACHE[phone_number_id] = (_t.time(), resolved)
            return resolved
    except Exception as exc:
        logger.debug("number route DB lookup skipped: %s", exc)
    return (None, None)

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
                # Diagnostic: surface the exact inbound type + media presence so we can see
                # why a voice note isn't transcribing (audio branch expects type=="audio").
                logger.info("WA msg type=%s from=%s keys=%s", msg_type, phone, list(msg.keys()))

                # 3. Idempotency check — in-memory fast path, then a DURABLE cross-worker claim.
                # The in-memory list is per uvicorn worker (WEB_CONCURRENCY=2) and wiped on
                # restart, so a Meta redelivery (slow reply → webhook timeout → retry) hitting
                # the OTHER worker was processed again — confirmed 3x on 2026-07-16 (double
                # escalation-relay, double voice reply, double Timbuktu reply). Inserting the
                # msg_id into vula_wa_msg_dedup (migration 071) is atomic: the primary key
                # rejects the second insert, whichever worker/container it lands on.
                if msg_id:
                    if msg_id in _processed_msg_ids:
                        logger.debug("Skipping duplicate WhatsApp message: %s", msg_id)
                        continue
                    _processed_msg_ids.append(msg_id)
                    if len(_processed_msg_ids) > _MAX_PROCESSED_IDS:
                        _processed_msg_ids.pop(0)
                    try:
                        from vula.commerce import service as _dedup_cs
                        _dedup_cs._client().table("vula_wa_msg_dedup").insert(
                            {"msg_id": msg_id}).execute()
                    except Exception as _dedup_exc:
                        _s = str(_dedup_exc)
                        if "23505" in _s or "duplicate" in _s.lower():
                            logger.info("Skipping duplicate WhatsApp message (cross-worker): %s", msg_id)
                            continue
                        # Table missing / transient DB error → fail OPEN (in-memory dedup still
                        # applies) so messages are never silently dropped before migration 071.
                        logger.debug("durable dedup unavailable (run migration 071?): %s", _dedup_exc)

                # Route by the number the person messaged → (tenant, mode)
                phone_number_id = value.get("metadata", {}).get("phone_number_id", "")
                route_tenant, route_mode = _resolve_number_route(phone_number_id)
                commerce_tenant = route_tenant if route_mode == "commerce" else None

                # Suspended tenant (master's Suspend action, vula_tenant_config.active=false) —
                # drop silently rather than reply, same as a disconnected line would behave.
                if route_tenant:
                    from vula.api import tenants as _tenants
                    if not _tenants.is_active(route_tenant):
                        logger.info("Dropping inbound WA message for suspended tenant %s", route_tenant)
                        continue

                # Blue-tick it and show "typing…" before we start work. Placed after the dedup
                # and suspended-tenant guards (so a dropped message never gets false ticks) and
                # before every handler below — all of which do reply, which is Meta's own
                # condition for showing the indicator at all.
                if msg_id and route_tenant:
                    await _mark_read_and_typing(msg_id, route_tenant)

                try:
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

                    elif msg_type == "interactive":
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
                        # 2026-08-25: commerce_admin's confirm/cancel buttons (see
                        # ConfirmationRequired / _send_wa_buttons) — works on ANY route_mode
                        # (the owner's admin session exists on both commerce- and knowledge-mode
                        # tenants), so this is checked before the commerce_tenant-only branch
                        # below, using route_tenant rather than commerce_tenant.
                        if phone and reply_id and (
                            reply_id.startswith("admin_confirm:") or reply_id.startswith("admin_cancel:")
                        ) and route_tenant:
                            await _handle_admin_confirm_reply(phone, reply_id, route_tenant)
                        elif phone and reply_id and route_tenant and reply_id.startswith("docdir:"):
                            # Owner classifying an ambiguous document (see ask_document_kind) —
                            # supplier bill / our invoice / an expense.
                            await _handle_document_kind_reply(phone, reply_id, route_tenant)
                        elif phone and reply_id and route_tenant and (
                            reply_id.startswith("learn_keep:") or reply_id.startswith("learn_bin:")
                        ):
                            # Owner reviewing an answer Vula just learned from a handoff
                            # (migration 150) — like the admin confirm buttons, no LLM involved.
                            await _handle_learn_review_reply(phone, reply_id, route_tenant)
                        elif phone and reply_id and commerce_tenant:
                            # Handle list/button replies from WhatsApp catalog menu
                            await _handle_commerce_interactive(phone, reply_id, reply_title, msg_id, commerce_tenant)

                    elif msg_type == "order" and commerce_tenant:
                        # A customer checked out via WhatsApp's native product catalog cart —
                        # see _handle_native_order / scripts/sync_meta_catalog.py.
                        order = msg.get("order") or {}
                        if phone and order.get("product_items"):
                            await _handle_native_order(phone, order, commerce_tenant)

                    elif msg_type == "audio":
                        # Voice note → transcribe → route the text like a normal message.
                        audio = msg.get("audio") or {}
                        media_id = audio.get("id", "")
                        mime_type = audio.get("mime_type", "audio/ogg")
                        if phone and media_id:
                            await _handle_voice_note(
                                phone, media_id, mime_type, msg_id, route_mode, route_tenant
                            )

                    elif msg_type == "location":
                        # Shared pin (e.g. delivery address step) → a Maps link, routed through the
                        # same text pipeline so onboarding/order flows capture it like typed text.
                        # Geo coverage (migration 098): if the tenant has a shop pin + radius, the
                        # exact distance verdict is appended so the assistant answers "do you
                        # deliver here?" deterministically — Marketplace-style.
                        loc = msg.get("location") or {}
                        lat, lng = loc.get("latitude"), loc.get("longitude")
                        if phone and lat is not None and lng is not None:
                            label = loc.get("name") or loc.get("address") or ""
                            maps_url = f"https://maps.google.com/?q={lat},{lng}"
                            text = f"{label + ' — ' if label else ''}📍 {maps_url}"
                            try:
                                from vula.commerce import geo as _geo
                                cov = _geo.coverage(route_tenant or "", float(lat), float(lng)) if route_tenant else None
                                if cov:
                                    verdict = "WITHIN" if cov["covered"] else "OUTSIDE"
                                    text += (f"\n(delivery check: this pin is {cov['distance_km']} km from "
                                             f"{cov['origin_label']} — {verdict} the {cov['radius_km']:g} km "
                                             f"delivery radius)")
                            except Exception as _exc:
                                logger.debug("pin coverage check skipped: %s", _exc)
                            if route_mode == "commerce":
                                await _handle_commerce_message(phone, text, msg_id, route_tenant)
                            elif route_mode == "knowledge":
                                await _handle_message(phone, text, msg_id, route_tenant_id=route_tenant)
                            else:
                                await _handle_message(phone, text, msg_id)

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
                            # A sales_rep captioning a photo with an instruction (e.g. "log as
                            # meeting", "add this contact") means the CAPTION, not the photo
                            # itself — route it through the same rep-scoped agent a text message
                            # would reach (_sender_is_sales_rep / _run_commerce_admin above).
                            # Confirmed real gap (2026-08-26): without this, a captioned photo
                            # always fell through to generic document filing regardless of what
                            # the caption asked for — log_meeting was never reachable from an
                            # image message at all.
                            # 2026-08-26, same day: a rep asked "could you see the architect
                            # details in the picture?" and Vula asked them to re-share it — the
                            # caption alone reached the agent, never the photo's actual content.
                            # Now also runs one vision-description pass (best-effort, real photo
                            # content — a business card's printed text, a sign, a document —
                            # never invented) and gives the agent both, so "research this"/
                            # "who is this" work the same way "log as meeting" already does.
                            if msg_type == "image" and caption.strip() and route_tenant and await _sender_is_sales_rep(phone, route_tenant):
                                description = await _describe_photo_for_rep(media_id)
                                effective_text = caption
                                if description:
                                    effective_text = f"{caption}\n\n[What's in the photo: {description}]"
                                if await _run_commerce_admin(phone, effective_text, route_tenant):
                                    continue
                            # An explicit receipt/expense caption routes to the books even for a
                            # contractor (site crews buy materials + claim) — otherwise a registered
                            # contractor's photo is treated as task evidence first.
                            cap_l = (caption or "").lower()
                            expense_intent = any(k in cap_l for k in _EXPENSE_WORDS)
                            handled = False
                            if not expense_intent:
                                handled = await _handle_media(phone, media_id, caption, msg_id)
                            # An UNCAPTIONED photo from a rep that wasn't a receipt used to be
                            # filed silently as a document — so a screenshot of a company's
                            # details, or a business card snapped between meetings, vanished
                            # into storage with no acknowledgement and nothing logged. Reps are
                            # busy and often send a photo with no caption at all (Ian,
                            # 2026-09-02). Read it and ASK, rather than guessing or filing mute.
                            #
                            # Deliberately placed AFTER _handle_media: an uncaptioned receipt
                            # must still reach the books through the existing scanner, which is
                            # a real working flow and must not be diverted to the agent.
                            if (not handled and msg_type == "image" and not caption.strip()
                                    and route_tenant
                                    and await _sender_is_sales_rep(phone, route_tenant)):
                                description = await _describe_photo_for_rep(media_id)
                                if description:
                                    prompt = (
                                        f"[The rep sent this photo with no caption. "
                                        f"What's in the photo: {description}]\n\n"
                                        f"Tell them briefly what you can see, then ask what "
                                        f"they'd like done with it — save it as a contact, log "
                                        f"a meeting, file it against a project — unless one is "
                                        f"obviously right, in which case do that and say so. "
                                        f"Never invent details that aren't visible in the photo."
                                    )
                                    if await _run_commerce_admin(phone, prompt, route_tenant):
                                        continue
                            if not handled:
                                if route_mode == "knowledge" or route_tenant or expense_intent:
                                    # Tenant line (or an expense photo) → ingest + auto-log expenses.
                                    fname = caption or f"image-{msg_id}.jpg"
                                    await _handle_document_ingest(
                                        phone, media_id, fname, mime_type, route_tenant_id=route_tenant
                                    )
                                else:
                                    await _send_reply(phone, (
                                        "Thanks for the photo! Ask your site manager to register "
                                        "you in Vula so it can be linked to your task."
                                    ))
                except Exception as _dispatch_exc:
                    logger.error("WA message dispatch failed (type=%s phone=%s): %s", msg_type, phone, _dispatch_exc)
                    try:
                        from vula.commerce import service as _fail_cs
                        _fail_cs._client().table("vula_webhook_failures").insert({
                            "tenant_id": route_tenant, "phone": phone, "msg_type": msg_type,
                            "error": str(_dispatch_exc)[:500],
                        }).execute()
                    except Exception as _log_exc:
                        logger.debug("webhook failure log skipped (run migration 099?): %s", _log_exc)

            # Delivery/read status callbacks (outbound) → broadcast analytics.
            for s in value.get("statuses", []):
                wamid, st = s.get("id"), s.get("status")
                err = None
                if s.get("errors"):
                    e0 = s["errors"][0]
                    err = (e0.get("title") or e0.get("message") or str(e0))[:200]
                if wamid and st:
                    logger.info("WA status callback: wamid=%s status=%s err=%s", wamid, st, err)
                    try:
                        from vula.api.commerce import record_message_status
                        record_message_status(wamid, st, err)
                    except Exception as exc:
                        logger.debug("status update skipped: %s", exc)

    return {"status": "ok"}


# ─── Message routing ─────────────────────────────────────────────────────────

async def _maybe_helper_escalation_answer(phone: str, text: str) -> bool:
    """If this phone is a helper (e.g. Staci) with an open escalation, their message
    IS the answer — relay it to the customer and learn it for next time.

    Returns True if the message was consumed as an answer. Runs on EVERY inbound path
    (knowledge and commerce) so a helper can reply on whichever tenant line they were
    pinged from — not just the knowledge assistant's line.
    """
    try:
        from vula import escalation as esc
        open_esc = esc.open_escalation_for_helper(phone)
    except Exception:
        open_esc = None
    if not (open_esc and text.strip()):
        return False
    if _DONE_RE.match(text) or _APPROVE_RE.match(text) or _REJECT_RE.match(text):
        return False
    # A bare greeting is never the answer to a customer's question — confirmed live 2026-07-16:
    # a helper's unrelated "Hi" got relayed to a customer as "About your question — Hi" and
    # stored as a learned answer. Let greetings fall through to normal routing instead.
    if re.match(r"^\s*(hi|hello|hallo|hey|howzit|good\s*morning|goeie\s*dag|ok(ay)?|thanks|dankie)\s*[!.👋🙂]*\s*$",
                text, re.IGNORECASE):
        return False
    # A helper's own NEW question is not an answer either — confirmed live 2026-07-29: a helper
    # sitting on a stale (but not yet 48h-expired) escalation from an unrelated earlier message
    # asked their own genuine question, and it got swallowed as "the answer," relayed to the
    # WRONG person (the original asker of the unrelated old question), while the helper's real
    # question was never actually answered. A trailing '?', or a common question-starter with no
    # closing punctuation, means they're asking — let it fall through to normal routing instead.
    if text.strip().endswith("?") or re.match(
        r"^\s*(what|how|why|where|when|who|which|can you|could you|do you|does|is there|are there)\b",
        text, re.IGNORECASE,
    ):
        return False
    info = esc.answer_escalation(open_esc, text.strip())
    if not info:
        return True  # already answered by a concurrent delivery — consume silently, no double relay
    tid = info["tenant_id"]
    relay = await _voice_the_relay(tid, info.get("question") or "", text.strip())
    await _send_reply(info["customer_phone"], relay, tenant_id=tid)

    # Record the relayed answer as an AGENT turn so voice_profile can learn the business's real
    # tone from it (vula/commerce/voice_profile.py::_whatsapp_agent_replies reads role='agent').
    # 2026-09-01: the handoff relay never wrote back to the conversation at all, so the one place
    # a real human answer enters a customer chat contributed nothing to tone learning — only 1
    # role='agent' message existed platform-wide. Best-effort; never block the relay.
    try:
        from vula.commerce import service as _cs
        await _cs.append_message(tid, info["customer_phone"], "agent", relay)
    except Exception as exc:
        logger.debug("agent-turn record skipped for escalation relay: %s", exc)

    # One-tap review before this answer is ever reused for someone else (migration 150).
    learned_id = info.get("learned_id")
    if learned_id:
        creds = await _get_tenant_wa_creds(tid)
        sent = False
        if creds:
            sent = await _send_wa_buttons(
                creds, phone,
                (f"✅ Sent to the customer.\n\nShould I reuse this next time someone asks "
                 f"\"{(info.get('question') or '').strip()[:120]}\"?"),
                [{"id": f"learn_keep:{learned_id}", "title": "Yes, reuse it"},
                 {"id": f"learn_bin:{learned_id}", "title": "No, just once"}],
            )
        if not sent:
            await _send_reply(phone, "✅ Sent to the customer.", tenant_id=tid)
    else:
        await _send_reply(phone, "✅ Sent to the customer.", tenant_id=tid)
    return True


async def ask_document_kind(tenant_id: str, invoice_id: str, supplier: str,
                            total_cents: int, doc_type: str = "invoice") -> bool:
    """Ask the owner whether an ambiguous document is a supplier bill, a client invoice, or an
    expense — because we genuinely cannot tell.

    2026-09-03: the scan-commit path can only see who ISSUED a document; there is no bill-to
    field. When the issuer is neither this business nor a known supplier, the direction is a
    coin flip — a new supplier, or a client whose invoice is being imported in bulk. Filing it
    silently put 51 of off-the-hook's own sales invoices (R32,307.97) on the wrong side of the
    books; flagging it with needs_review is no better on its own, because the only consumer of
    that flag requires a supplier match, which by definition this case does not have.

    The document may arrive by email or dashboard upload with nobody watching, so the question
    goes to the owner on WhatsApp regardless of how it came in. Returns False when there is
    nobody to ask, so the caller keeps its filed-but-flagged fallback rather than losing it.
    """
    try:
        from vula.commerce.approvals import tenant_admin_approvers
        approvers = await tenant_admin_approvers(tenant_id)
    except Exception as exc:
        logger.debug("document-kind ask skipped (no approvers): %s", exc)
        return False
    if not approvers:
        return False

    who = (supplier or "").strip() or "an unnamed party"
    body = (f"📄 I filed a {doc_type} from *{who}* for *R{total_cents/100:,.2f}*, but I'm not "
            f"sure what it is — I don't recognise them as a supplier, and it wasn't issued by "
            f"you.\n\nWhich is it?")
    sent_any = False
    for a in approvers[:2]:                      # the owner, not the whole team
        creds = await _get_tenant_wa_creds(tenant_id)
        if not creds:
            break
        sent = await _send_wa_buttons(creds, a["phone"], body, [
            {"id": f"docdir:supplier:{invoice_id}", "title": "Supplier bill"},
            {"id": f"docdir:client:{invoice_id}", "title": "Our invoice"},
            {"id": f"docdir:expense:{invoice_id}", "title": "An expense"},
        ])
        sent_any = sent_any or sent
    return sent_any


async def _handle_document_kind_reply(phone: str, reply_id: str, tenant_id: str) -> None:
    """A tap on the Supplier bill / Our invoice / An expense buttons (see ask_document_kind)."""
    from vula.commerce import service as cs
    try:
        _, kind, invoice_id = reply_id.split(":", 2)
    except ValueError:
        return
    try:
        rows = (cs._client().table("commerce_invoices").select("*")
                .eq("tenant_id", tenant_id).eq("id", invoice_id).limit(1).execute().data or [])
    except Exception as exc:
        logger.warning("document-kind lookup failed for %s: %s", invoice_id, exc)
        rows = []
    if not rows:
        await _send_reply(phone, "That document isn't around to reclassify any more. 🙏", tenant_id)
        return
    inv = rows[0]
    amount = f"R{(inv.get('total_cents') or 0)/100:,.2f}"
    who = inv.get("supplier") or "that party"

    if kind == "expense":
        # Deliberately NOT auto-converted: moving money between an invoice and an expense record
        # changes the books, and doing it silently from a button tap is exactly the kind of
        # invisible write this whole pass has been correcting. Flag it and say so plainly.
        try:
            cs._client().table("commerce_invoices").update(
                {"needs_review": True, "notes": "Owner says: expense, not an invoice"}
            ).eq("id", invoice_id).eq("tenant_id", tenant_id).execute()
        except Exception as exc:
            logger.warning("expense reclassify flag failed for %s: %s", invoice_id, exc)
        await _send_reply(phone, (
            f"👍 Noted — {amount} from {who} is an expense, not an invoice. I've marked it for "
            f"the books rather than moving it myself, so nothing shifts without you seeing it."
        ), tenant_id)
        return

    direction = "inbound" if kind == "supplier" else "outbound"
    try:
        cs._client().table("commerce_invoices").update(
            {"direction": direction, "needs_review": False}
        ).eq("id", invoice_id).eq("tenant_id", tenant_id).execute()
    except Exception as exc:
        logger.warning("document direction update failed for %s: %s", invoice_id, exc)
        await _send_reply(phone, "Couldn't file that just now, sorry.", tenant_id)
        return
    if direction == "inbound":
        await _send_reply(phone, (
            f"👍 Filed as a *supplier bill* — {amount} owed to {who}. It'll show in what's due "
            f"to go out."), tenant_id)
    else:
        await _send_reply(phone, (
            f"👍 Filed as *your invoice* — {amount} owed to you. It'll count as money in."),
            tenant_id)


async def _handle_learn_review_reply(phone: str, reply_id: str, tenant_id: str) -> None:
    """A tap on the Keep/Bin buttons for an answer Vula learned from a handoff (migration 150).

    Only an APPROVED answer is ever reused for another customer — see
    escalation.find_learned_answer. Both learned answers found in production on 2026-09-01 were
    wrong, which is why this gate exists at all.
    """
    from vula import escalation as esc
    action, _, learned_id = reply_id.partition(":")
    if not learned_id:
        return
    row = esc.get_learned_answer(learned_id)
    if not row or row.get("tenant_id") != tenant_id:
        await _send_reply(phone, "That one's no longer around to review. 🙏", tenant_id)
        return
    if action == "learn_keep":
        ok = esc.approve_learned_answer(learned_id, approved_by=phone)
        await _send_reply(phone, (
            "👍 Saved — I'll answer that one myself next time."
            if ok else "Couldn't save that just now, sorry."
        ), tenant_id)
    else:
        esc.reject_learned_answer(learned_id)
        await _send_reply(phone, "👌 Noted — I'll ask you again next time rather than guess.",
                          tenant_id)


async def _voice_the_relay(tenant_id: str, question: str, answer: str) -> str:
    """Put the helper's answer into the business's own voice before it reaches the customer.

    2026-09-01: the relay was a bare f"About your question — {raw text}", so a customer got the
    helper's internal shorthand verbatim. Rewrites using the tenant's persona_prompt, the same
    tone source commerce_assistant._system_prompt honours. Falls back to the original wording on
    any failure — a clumsy relay is far better than none, and this must never lose an answer.

    The rewrite is explicitly forbidden from adding facts: it may only re-word what the helper
    actually said.
    """
    original = f"About your question — {answer}"
    try:
        persona = ""
        try:
            from vula.api.tenants import get_config
            persona = (get_config(tenant_id) or {}).get("persona_prompt") or ""
        except Exception:
            persona = ""

        import litellm
        from core.llm_router import resolve_generation_route
        litellm.drop_params = True
        model, api_key, api_base = await resolve_generation_route(task_type="escalation_relay")
        resp = await litellm.acompletion(
            model=model, api_key=api_key, api_base=api_base, max_tokens=220, temperature=0.3,
            messages=[{"role": "user", "content": (
                "A customer asked a business a question. A member of staff wrote the answer in "
                "shorthand. Rewrite it as the business's own short, warm WhatsApp reply to the "
                "customer.\n\n"
                "RULES — these matter more than style:\n"
                "- Use ONLY what the staff member actually said. Add no facts, prices, times, "
                "or promises of your own.\n"
                "- If the staff answer is vague, stay vague. Do not invent specifics.\n"
                "- Keep it to 2 sentences or fewer. No greeting preamble, no sign-off.\n"
                f"{'- Match this tone: ' + persona if persona else ''}\n\n"
                f"Customer asked: {question}\n"
                f"Staff answered: {answer}\n\n"
                "Reply to the customer:"
            )}],
        )
        out = (resp.choices[0].message.content or "").strip()
        return out or original
    except Exception as exc:
        logger.debug("relay voicing skipped, sending verbatim: %s", exc)
        return original


async def _maybe_escalate_and_learn(tenant_id: str, phone: str, text: str,
                                    reply: str, confidence: Optional[float] = None) -> str:
    """If `reply` shows the agent couldn't answer, reuse a learned answer, else ask a
    human helper on WhatsApp and hold the customer with a friendly note.

    Returns the reply to actually send. Shared by the knowledge and commerce paths so
    escalate-and-learn works for every tenant. Commerce has no confidence signal, so it
    escalates purely on the "I don't know / can't help" text patterns in should_escalate.
    """
    try:
        from vula import escalation as esc
        if not esc.should_escalate(reply, confidence, customer_text=text):
            return reply
        learned = esc.find_learned_answer(tenant_id, text)
        if learned:
            return learned
        row = esc.create_escalation(tenant_id, phone, text)
        if row:
            # Flag it to the helper when the customer's OWN message reads frustrated — a
            # confidently-wrong-or-unhelpful reply to an upset customer used to never surface
            # this to staff at all, since escalation only looked at the bot's own confidence.
            prefix = ("😠 This customer sounds frustrated — please prioritise this one.\n\n"
                     if esc.customer_seems_frustrated(text) else "❓ ")
            await _send_reply(
                row["helper_phone"],
                f"{prefix}A customer asked:\n\n\"{text.strip()}\"\n\nReply to this message with "
                f"the answer — I'll send it to them and remember it.",
                tenant_id=tenant_id,
            )
            return "Thanks for your question! Let me check with the team and get right back to you. 🙏"
    except Exception as exc:
        logger.debug("escalation skipped: %s", exc)
    return reply


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

    # ── Escalation answer: if this phone is a helper with an open escalation, their
    # message IS the answer — relay it to the customer and learn it for next time.
    if await _maybe_helper_escalation_answer(phone, text):
        return

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

        # Suspended tenant, resolved via shared-line phone lookup (the dedicated-line case
        # is already caught in receive_message before dispatch, using route_tenant directly).
        if tenant_id:
            from vula.api import tenants as _tenants
            if not _tenants.is_active(tenant_id):
                logger.info("Dropping inbound WA message for suspended tenant %s", tenant_id)
                return

    # ── Data deletion / opt-out (POPIA + Meta requirement) ───────────────────
    if _DELETE_RE.match(text):
        await _handle_data_deletion(phone, tenant_id)
        return

    # Deterministic, IN-FLIGHT conversational state — checked BEFORE the sales_rep agent below.
    # 2026-07-29: a sales_rep's plain reply to "which project is that receipt for?" (e.g. "HPC")
    # or a correction ("it was on the company card") was being swallowed by the sales_rep gate's
    # free-form tool-calling loop, which — having no awareness of a pending expense-allocation
    # or bank-review question — hallucinated an unrelated tool call (draft a follow-up email,
    # log a meeting, save a contact) instead of falling through. These deterministic checks know
    # exactly what reply they're waiting for, so they must win over the general-purpose agent.
    if tenant_id:
        _alloc = await _maybe_allocate_pending_expense(tenant_id, phone, text)
        if _alloc:
            await _send_reply(phone, _alloc, tenant_id)
            return
        # Answering a bank-review question ("R720 to Lonese — what's this?").
        _rev = await _maybe_bank_review_answer(tenant_id, phone, text)
        if _rev:
            await _send_reply(phone, _rev, tenant_id)
            return
        # A team member managing their own notification prefs ("stop follow-up emails").
        from vula.integrations.notify import handle_preference_command
        _pref = handle_preference_command(tenant_id, phone, text)
        if _pref:
            await _send_reply(phone, _pref, tenant_id)
            return

    # ── sales_rep team member → the rep-scoped commerce admin agent, not the generic
    # construction knowledge-base assistant below. This function (_handle_message) is the
    # ONLY dispatch path for "knowledge"-mode tenant lines (e.g. digg-demo) — a tenant using
    # that mode never reaches _handle_commerce_message, where commerce_admin normally lives, so
    # without this a sales_rep team member added to a knowledge-mode tenant would be silently
    # invisible here regardless of their vula_team_members role. Deliberately scoped to ONLY
    # the "sales_rep" role (not owner/staff/admin/manager) so the existing RAG/KB behaviour a
    # tenant's real recognized team already relies on here is completely unchanged.
    if tenant_id and await _sender_is_sales_rep(phone, tenant_id):
        if await _run_commerce_admin(phone, text, tenant_id):
            return
        # Admin agent failed → fall through to the normal KB/RAG flow below.

    # ── Field-ops intents (any phone, no role check needed) ──────────────────
    if _DONE_RE.match(text):
        await _handle_task_complete(phone, tenant_id)
        return

    approve_m = _APPROVE_RE.match(text)
    if approve_m:
        notes = approve_m.group(1).strip()
        # Generic approval engine first (invoices, multi-approver), then field-ops.
        from vula.commerce.approvals import record_decision
        if not await record_decision(phone, "approved", notes):
            await _handle_sign_off_reply(phone, tenant_id, "approved", notes)
        return

    reject_m = _REJECT_RE.match(text)
    if reject_m:
        notes = reject_m.group(1).strip()
        from vula.commerce.approvals import record_decision
        if not await record_decision(phone, "rejected", notes):
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
            "For anything else, contact your site manager.",
            tenant_id,
        )
        return

    if role == "viewer":
        await _send_reply(phone, "You have read-only access. Contact your admin to upgrade.", tenant_id)
        return

    # ── Pending document filing: if a doc is awaiting a project, treat this
    # message as the answer (file it + attach to ClickUp), else fall through.
    try:
        from vula.integrations.doc_filing import resolve_pending_document
        pending = await resolve_pending_document(tenant_id, phone, text)
    except Exception:
        pending = None
    if pending is not None:
        if pending.get("filed"):
            note = f"✅ Filed '{pending['filename']}' under *{pending['project']}*."
            if pending.get("clickup"):
                note += " Added to ClickUp."
            await _send_reply(phone, note, tenant_id=tenant_id)
        elif pending.get("skipped"):
            await _send_reply(
                phone, f"👍 Left '{pending['filename']}' unfiled — you can file it "
                f"anytime from the dashboard.", tenant_id=tenant_id)
        else:  # unmatched
            candidates = pending.get("candidates") or []
            if candidates:
                msg = ("That could be more than one project — " + " / ".join(candidates) +
                      ". Reply with the exact project name, or 'skip' to leave it unfiled.")
            else:
                msg = ("I couldn't match that to a project. Reply with the exact "
                      "project name, or 'skip' to leave it unfiled.")
            await _send_reply(phone, msg, tenant_id=tenant_id)
        return

    # admin and staff get full RAG
    project_id = _active_project_for_phone(phone)
    thread_key = f"{phone}:{project_id}" if project_id else phone

    from vula.chat.history import get_db
    db = get_db()
    db.save(tenant_id, thread_key, "user", text)
    history = db.format_for_prompt(tenant_id, thread_key, limit=12)

    # Orchestration memory bridge: knowledge-mode (HRM) and commerce-mode routing stay
    # separate (a WhatsApp number is one or the other, never both), but everyone reaching
    # this branch is staff/admin — same population _run_commerce_admin serves. Mirror this
    # turn into commerce_conversation_messages under the identical admin:{phone} session-key
    # convention commerce already uses, so a tenant who later gets both modes bridged isn't
    # starting from a context vacuum. Best-effort, never blocks the actual reply.
    commerce_session_id = None
    try:
        from vula.commerce import service as _commerce_bridge_service
        _bridge_session = await _commerce_bridge_service.get_or_create_session(
            tenant_id, session_key=f"admin:{phone}", channel="whatsapp", customer_phone=phone
        )
        commerce_session_id = _bridge_session["id"]
        await _commerce_bridge_service.append_message(tenant_id, commerce_session_id, "user", text)
    except Exception as exc:
        logger.debug("commerce-history bridge (user turn) skipped: %s", exc)

    # Make the chat project-aware: if the message references a known project,
    # prepend its context (client, applicable standards, team) so the answer scopes
    # to that project and cites the right codes.
    try:
        from vula.integrations.project_context import project_context_block
        pc = project_context_block(tenant_id, text)
        if pc:
            history = f"{pc}\n\n{history}" if history else pc
    except Exception as exc:
        logger.debug("project context skipped: %s", exc)

    try:
        from vula.integrations.metering import set_request_tenant
        set_request_tenant(tenant_id)
    except Exception:
        pass
    reply = await _rag_reply(tenant_id, text, conversation_history=history, phone=phone)

    # ── Escalate-and-learn: if the agent isn't confident, reuse a learned answer,
    # else ask a human helper on WhatsApp and hold the customer with a friendly note.
    reply = await _maybe_escalate_and_learn(tenant_id, phone, text, reply, _LAST_CONF.get())

    db.save(tenant_id, thread_key, "assistant", reply)
    if commerce_session_id:
        try:
            from vula.commerce import service as _commerce_bridge_service
            await _commerce_bridge_service.append_message(
                tenant_id, commerce_session_id, "assistant", reply)
        except Exception as exc:
            logger.debug("commerce-history bridge (assistant turn) skipped: %s", exc)
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
            source_type="learned",   # excluded from factual retrieval (low authority)
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
                "Ask your Vula admin to upload this for you.",
                tenant_id,
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
            f"Send PDFs, Word docs, Excel files, or plain text.",
            tenant_id,
        )
        return

    # Send from the TENANT's number (omitting tenant_id falls back to the global line,
    # which 400s for senders not on its allow-list). Tell them what actually happens next.
    if (mime_type or "").startswith("image/"):
        ack = ("📸 Got it — reading that now. If it's a receipt or invoice I'll book it "
               "straight into your books; anything else gets filed. Give me a minute…")
    else:
        ack = (f"📄 Got it — processing '{filename or 'your document'}'. Bank statements get "
               "reconciled, invoices go to your books, and everything is filed so you can ask "
               "me about it. This takes 1-3 minutes.")
    await _send_reply(phone, ack, tenant_id)

    try:
        # Download from Meta
        local_path = await _download_document(media_id, tenant_id, filename, mime_type)
        if not local_path:
            await _send_reply(phone, f"Sorry, I couldn't download '{filename}'. Please try again.", tenant_id)
            return

        # Redelivery guard (2026-08-27) — confirmed live: WhatsApp/Meta can redeliver the same
        # document, and since Vula's own auto-generated filename bakes in a processing-time
        # timestamp, each redelivery looked like a "new" file to every downstream check, running
        # the full (expensive) vision-scan + KB-ingestion + a confusing burst of near-identical
        # replies 3-4 times for one real document. Content hash is the only reliable "is this the
        # same file" signal; check it BEFORE any real work starts, not just at the filing step
        # (migration 143 closes that later, narrower gap too, but this stops the wasted work).
        try:
            import hashlib as _hashlib_dedup
            from datetime import datetime as _dt, timedelta as _td, timezone as _tz
            content_hash = _hashlib_dedup.sha256(local_path.read_bytes()).hexdigest()
            from vula.commerce import service as _svc_dedup
            cutoff = (_dt.now(_tz.utc) - _td(minutes=10)).isoformat()
            recent_dup = (_svc_dedup._client().table("vula_filed_documents").select("id")
                          .eq("tenant_id", tenant_id).eq("content_hash", content_hash)
                          .gte("created_at", cutoff).limit(1).execute().data or [])
            if recent_dup:
                await _send_reply(phone, "♻️ Already processed this one a moment ago — skipped the duplicate.", tenant_id)
                return
        except Exception as exc:
            logger.debug("document redelivery guard skipped for %s: %s", filename, exc)

        # Ingest via pipeline — always adds to KB so Vula learns from every doc
        from vula.ingestion.pipeline import VulaIngestionPipeline
        pipeline = VulaIngestionPipeline(tenant_id=tenant_id)
        result = await pipeline.ingest_file(local_path)

        if result.status not in ("success", "done"):
            await _send_reply(
                phone,
                f"There was a problem ingesting '{result.filename}': {result.error or 'unknown error'}. "
                f"Please try again or contact your Vula rep.",
                tenant_id,
            )
            return

        # Bank statement PDF? → run bank reconciliation, NOT the invoice scanner (a statement
        # has "Tax Invoice" printed on it and would book junk into the books).
        if local_path.suffix.lower() == ".pdf":
            try:
                from vula.commerce import bank_rec
                head = bank_rec.extract_pdf_text(
                    local_path, bank_rec.get_statement_password(tenant_id))[:4000].lower()
            except Exception:
                head = ""
            if "statement" in head and any(k in head for k in
                                           ("opening balance", "closing balance", "transaction history")):
                rec = await bank_rec.ingest_statement(tenant_id, local_path, source_file=local_path.name)
                if rec.get("error"):
                    await _send_reply(phone, f"🏦 That looks like a bank statement, but I couldn't "
                                             f"process it: {rec['error']}", tenant_id)
                else:
                    msg = (f"🏦 Bank statement processed: *{rec.get('parsed', 0)} transactions*.\n"
                           f"✅ {rec.get('matched_invoices', 0)} matched to invoices"
                           f" · 🧾 {rec.get('matched_expenses', 0)} matched to receipts"
                           f" · 👷 {rec.get('matched_workers', 0)} worker payments")
                    if rec.get("extraction_reconciled") is False:
                        msg += ("\n⚠️ The running balances on this statement didn't add up — "
                                "please double-check the figures in your 🏦 Bank tab.")
                    ni = rec.get("needs_input", 0)
                    if ni:
                        # Start the review right here in the chat — one item at a time.
                        from vula.commerce import bank_review
                        q = bank_review.start_review(tenant_id)
                        if q:
                            msg += f"\n\n{ni} I couldn't allocate — let's sort them here:\n\n{q}"
                        else:
                            msg += (f"\n❓ {ni} need a quick review in your 🏦 Bank tab.")
                    else:
                        msg += "\nEverything allocated — see the 🏦 Bank tab."
                    await _send_reply(phone, msg, tenant_id)
                return

        # PDFs that look like supplier invoices → auto-scan → commit to books (photos are
        # handled as expense claims below, off the reliable deep-analysis fields).
        scan_msg = ""
        # True once the vision-scan shortcut below has already committed this PDF to
        # commerce_invoices/expenses — tells _file_uploaded_document not to commit it again
        # (e.g. via a deep-analysis "Quote / Estimate" category the shortcut doesn't cover).
        already_committed = False
        # Set whenever an expense claim gets created below (either scan path) — its value is
        # the exact receipt_doc_id that claim was stored under, used after filing to backfill
        # commerce_expenses.receipt_url with the real durable Storage URL (previously always
        # NULL: the claim is created here, BEFORE the storage upload below produces a real URL,
        # and nothing ever linked them back — confirmed live 2026-08-26 against real claims).
        expense_claim_img_ref = None
        if role == "admin" and local_path.suffix.lower() == ".pdf":
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

                        if doc_type in ("receipt", "petty_cash") and total > 0:
                            # A receipt = money spent for the business → log as an EXPENSE CLAIM
                            # (who paid it, categorised, VAT split, allocated to a project).
                            expense_claim_img_ref = result.doc_id
                            scan_msg = await _log_expense_claim(
                                tenant_id, phone, scan_data, result.doc_id)
                        elif doc_type in ("invoice", "delivery_note") and total > 0:
                            # Supplier invoice/delivery note → commit to books as before.
                            commit_resp = await client.post(
                                f"http://localhost:{_s.api_port}/v1/commerce/{tenant_id}/admin/scan/commit",
                                headers={"X-API-Key": _s.api_key, "Content-Type": "application/json"},
                                json={"extracted": scan_data, "auto_commit": True},
                            )
                            if commit_resp.status_code == 200:
                                commit_data = commit_resp.json()
                                scan_msg = f"\n\n📊 {commit_data.get('message', '')}"
                                already_committed = True
            except Exception as scan_exc:
                logger.debug("Auto-scan skipped for %s: %s", filename, scan_exc)

        # Deep analysis: classify by CONTENT + pull structured fields → backend.
        analysis = await _analyze_document(tenant_id, result.filename, local_path)
        if analysis:
            doc_category = analysis["category"]
            summary = analysis.get("summary", "")
            fields = analysis.get("fields", {})

            # A PHOTO of a business card = the OCR-text pass above reliably reads the email
            # (a distinct @-pattern OCRs cleanly) but consistently misses the phone number
            # (small print, varied formatting, icons) — confirmed live 2026-08-27. Same fix as
            # the receipt-scan shortcut: one direct vision call on the actual image, preferring
            # its result for each field over the weaker OCR-text one.
            research_note = ""
            if doc_category == "Business Card" and local_path.suffix.lower() in (".jpg", ".jpeg", ".png", ".webp"):
                vision_fields = await _scan_business_card_photo(str(local_path))
                if vision_fields:
                    for key in ("name", "company", "title", "phone", "email"):
                        if vision_fields.get(key):
                            fields[key] = vision_fields[key]
                # Genuinely nothing printed for phone/email on the card itself → best-effort web
                # search, clearly flagged as researched rather than presented as if it were on
                # the card (same "Can you do web search" pattern already proven finding real
                # contact details).
                if not fields.get("phone") or not fields.get("email"):
                    found = await _research_missing_contact_details(fields.get("name") or "", fields.get("company") or "")
                    added = []
                    if found.get("phone") and not fields.get("phone"):
                        fields["phone"] = found["phone"]
                        added.append("phone")
                    if found.get("email") and not fields.get("email"):
                        fields["email"] = found["email"]
                        added.append("email")
                    if added:
                        research_note = (f"\n🔎 Found their {' and '.join(added)} via a web "
                                         f"search — please double-check it's correct.")

            # Same fix class, 2026-08-27: a photo of a building's exterior/signage — the address
            # is exactly the small/distant/angled street-sign text the OCR-text pass is
            # unreliable on. Direct vision read overrides; if a business name is legible but the
            # address isn't, fall back to a web search for it (never invented).
            if doc_category == "Site / Building Photo" and local_path.suffix.lower() in (".jpg", ".jpeg", ".png", ".webp"):
                site_fields = await _scan_site_photo(str(local_path))
                if site_fields:
                    for key in ("address", "business_name", "notes"):
                        if site_fields.get(key):
                            fields[key] = site_fields[key]
                if fields.get("business_name") and not fields.get("address"):
                    found_addr = await _research_missing_address(fields["business_name"])
                    if found_addr.get("address"):
                        fields["address"] = found_addr["address"]
                        research_note = ("\n🔎 Found their address via a web search — please "
                                         "double-check it's correct.")

            # Normalise a business-card phone ONCE, here — before it's shown in the WhatsApp
            # breakdown, stored in vula_document_extractions, or written to commerce_contacts
            # below, so the number displayed to the rep always matches what actually got saved
            # (previously the confirmation showed the raw OCR digits while the saved contact
            # had the normalised 27-prefixed number — two different values for the same field).
            if doc_category in _CONTACT_DOC_CATEGORIES and fields.get("phone"):
                _p = "".join(c for c in fields["phone"] if c.isdigit())
                fields["phone"] = ("27" + _p[1:]) if _p.startswith("0") else _p

            # WhatsApp gives uncaptioned photos an opaque wamid-based filename (see the
            # `caption or f"image-{msg_id}.jpg"` fallback where inbound images are first named)
            # — replace it with something a human can actually recognise once we know what the
            # document IS. Mutating result.filename here (not a local var) so every downstream
            # use — this confirmation message, vula_document_extractions, and file_document()'s
            # stored filename — picks up the friendly name for free.
            result.filename = _friendly_document_name(doc_category, fields, result.filename)

            # Keep the structured extraction (legacy table, best-effort).
            try:
                from vula.commerce import service as commerce_service
                commerce_service._client().table("vula_document_extractions").insert({
                    "tenant_id": tenant_id,
                    "filename": result.filename,
                    "category": doc_category,
                    "summary": summary,
                    "fields": fields,
                    "doc_id": result.doc_id,
                    "source": "whatsapp",
                }).execute()
            except Exception as exc:
                logger.debug("Extraction store skipped (run migration 011?): %s", exc)
            breakdown = _format_extraction(analysis) + research_note

            # A PHOTO of a receipt/invoice = money spent for the business → expense claim.
            # ONE strong vision call reads the actual image (the OCR-text → small-model path
            # hallucinated totals). Delivery notes are filed, never booked as money.
            is_img = local_path.suffix.lower() in (".jpg", ".jpeg", ".png", ".webp")
            if not scan_msg and is_img:
                fin = await _scan_financial_photo(str(local_path))
                dtp = (fin or {}).get("doc_type")
                total_r = _num((fin or {}).get("total"))
                if fin and dtp in ("receipt", "invoice") and total_r > 0:
                    vat_r = _num(fin.get("vat"))
                    # Sanity: SA VAT is 15/115 of the gross (~13%). An impossible figure means a
                    # misread — drop it and let the books compute it from the category instead.
                    if not (0 <= vat_r <= total_r * 0.16):
                        vat_r = 0
                    # Flatten to a clean, flat-scan-style crop when the vision call confidently
                    # found the receipt's 4 corners (2026-08-27) — overwritten in place BEFORE
                    # hashing, so the dedup hash, the storage upload, and the workbook's embedded
                    # slip photo all pick up the cleaned-up version automatically, with no
                    # separate wiring. Best-effort: a bad/missing corner detection is a no-op.
                    try:
                        from vula.commerce.image_prep import flatten_receipt
                        flattened = flatten_receipt(local_path.read_bytes(), fin.get("corners"))
                        local_path.write_bytes(flattened)
                    except Exception as exc:
                        logger.debug("receipt flatten skipped for %s: %s", filename, exc)
                    # Dedup on the image CONTENT — a re-sent photo has identical bytes even though
                    # it gets a new media id, so this catches re-sends AND Meta redeliveries.
                    import hashlib
                    from vula.commerce.party import resolve_party_name
                    img_ref = "img:" + hashlib.sha256(local_path.read_bytes()).hexdigest()[:40]
                    expense_claim_img_ref = img_ref
                    scan_msg = await _log_expense_claim(tenant_id, phone, {
                        "total_cents": round(total_r * 100),
                        "vat_cents": round(vat_r * 100),
                        "supplier": resolve_party_name(fin, order=("vendor", "supplier")),
                        "date": fin.get("date"),
                        "notes": summary,
                        "card_last4": fin.get("card_last4"),
                        "payment_method": fin.get("payment_method"),
                    }, img_ref)
                    cur = (fin.get("currency") or "").upper()
                    if scan_msg and cur and cur not in ("ZAR", "UNKNOWN"):
                        scan_msg += (f"\n⚠️ This looks like a *{cur}* amount, not rands — "
                                     "please check it in the Expenses tab.")
                elif fin and dtp == "payment_confirmation" and total_r > 0:
                    from vula.commerce import bank_rec
                    reply = bank_rec.stage_pop_for_review(
                        tenant_id, round(total_r * 100), fin.get("date"),
                        fin.get("reference"), fin.get("payee"), sender_phone=phone)
                    scan_msg = "\n\n" + reply
                elif fin and dtp == "delivery_note":
                    scan_msg = ("\n\n📦 That's a *delivery note* — filed with your documents "
                                "(no money booked). I'll match it against the supplier's invoice.")
                elif fin and dtp == "menu" and any(k in (filename or "").lower() for k in _EXPENSE_WORDS):
                    scan_msg = ("\n\n⚠️ That looks like a *menu/price list*, not a receipt — "
                                "nothing was booked.")
        else:
            # Fallback to keyword classification if deep analysis was unavailable
            doc_category = _classify_document(result.filename, local_path)
            summary, fields, breakdown = "", {}, ""

        # File the document: durable copy + project link + ClickUp attachment. Also books it
        # via the shared commit path (same as email/Smart Scanner) unless the vision-scan
        # shortcut above already committed it.
        file_note, filed_row = await _file_uploaded_document(
            tenant_id, phone, result, local_path, mime_type,
            doc_category, summary, fields, already_committed=already_committed,
        )
        if expense_claim_img_ref:
            await _backfill_receipt_url(tenant_id, expense_claim_img_ref, filed_row)

        # 2026-08-27 fix: _file_uploaded_document() degrades to ("", None) on ANY failure (e.g.
        # the real DB-constraint bug this same day, confirmed live on a gerflor billboard photo
        # — filing failed outright with Postgres error 42P10, yet the reply below still claimed
        # "✅ Filed" unconditionally). filed_row only has a real "id" when the write actually
        # persisted — check it before claiming success, so a genuine filing failure is reported
        # honestly instead of silently lying to the rep.
        filed_ok = bool(filed_row and filed_row.get("id"))

        if scan_msg and any(k in scan_msg for k in ("💳", "♻️", "📦", "⚠️", "📸")):
            # Money was booked (or deliberately not) — lead with THAT, not the filing mechanics.
            msg = scan_msg.strip() + (
                "\n\n🗂 The document itself is filed — ask me about it any time." if filed_ok
                else "\n\n⚠️ I couldn't file the document itself just now — please try resending it.")
        else:
            msg = (
                (f"✅ Filed '{result.filename}' as *{doc_category}* — " if filed_ok
                 else f"📖 Read '{result.filename}' as *{doc_category}* — ") +
                f"{result.chunks_stored} chunks added."
            )
            if summary:
                msg += f"\n\n📄 {summary}"
            if breakdown:
                msg += f"\n\n{breakdown}"
            if not filed_ok:
                msg += "\n\n⚠️ I understood it but couldn't file it just now — please try resending."
            elif file_note:
                msg += f"\n\n{file_note}"
            else:
                msg += "\n\nAsk me anything about it."
            msg += scan_msg
        await _send_reply(phone, msg, tenant_id)
    except Exception as exc:
        logger.error("Document ingest failed for %s: %s", phone, exc)
        await _send_reply(phone, f"Something went wrong ingesting '{filename}'. Please try again.", tenant_id)


def _num(v) -> float:
    """Best-effort parse a money value from a number or a string like 'R 1,234.56'."""
    if isinstance(v, (int, float)):
        return float(v)
    if not v:
        return 0.0
    try:
        return float(re.sub(r"[^\d.]", "", str(v).replace(",", "")) or 0)
    except Exception:
        return 0.0


async def _log_expense_claim(tenant_id: str, phone: str, scan_data: dict,
                             doc_id: Optional[str] = None) -> str:
    """A scanned receipt = money spent for the business → record an expense CLAIM (who paid, what
    category, VAT split, which project). Returns a short confirmation appended to the filing reply;
    asks for a project when the tenant runs projects and this one isn't allocated yet."""
    try:
        from vula.commerce import expenses
        total = int(scan_data.get("total_cents") or 0)
        vat = int(scan_data.get("vat_cents") or 0) or None
        supplier = scan_data.get("supplier") or None
        # Who paid + is it out-of-pocket? An owner/admin's own card = not reimbursable; a
        # contractor/staff member who bought materials on site = reimburse them.
        name, reimbursable = None, False
        try:
            from vula.models.tenants import get_tenant_db
            lk = get_tenant_db().lookup_by_phone_with_role(phone)
            if lk:
                name = lk.get("name")
                reimbursable = (lk.get("role") not in ("admin", "owner"))
        except Exception:
            pass
        if not name:
            try:
                from vula.models.field_ops import get_field_ops_db
                c = get_field_ops_db().get_contractor_by_phone(phone)
                if c:
                    name, reimbursable = getattr(c, "name", None), True
            except Exception:
                pass
        # Whose money? The card digits on the slip decide — a registered company card is
        # business money (never owed back); a different card is out of pocket.
        paid_with = expenses.resolve_paid_with(
            tenant_id, card_last4=scan_data.get("card_last4"),
            payment_method=scan_data.get("payment_method"))
        # Learned supplier→project rule first (from a previous manual allocation — same
        # learned-rules mechanism the document-filing path uses, including its 2026-08-08
        # cross-project-payee ambiguity fix), then fall back to loose text matching on the
        # receipt's own notes.
        project = None
        if supplier:
            try:
                from vula.integrations.doc_filing import lookup_learned_project
                learned = lookup_learned_project(tenant_id, {"supplier": supplier})
                if learned and not learned.get("ambiguous"):
                    project = learned.get("project")
            except Exception:
                pass
        if not project:
            project = expenses.match_project(tenant_id, scan_data.get("notes") or "")
        # Read-only supplier lookup — a receipt is a reimbursement event, not a bill, so it
        # never auto-creates a new supplier; it only links supplier_id when this genuinely
        # matches a supplier Vula already knows (same tiered match the Smart Scanner uses).
        supplier_id = None
        if supplier:
            try:
                from vula.commerce import service as commerce_service
                sm = await commerce_service.match_supplier(tenant_id, name=supplier)
                if sm and sm.get("auto_apply"):
                    supplier_id = (sm.get("supplier") or {}).get("id")
            except Exception:
                pass
        claim = await expenses.create_claim(
            tenant_id, amount_cents=total,
            description=(scan_data.get("notes") or supplier or "Receipt"),
            supplier=supplier, supplier_id=supplier_id, date=scan_data.get("date"), vat_cents=vat,
            project=project, paid_by=phone, paid_by_name=name, reimbursable=reimbursable,
            paid_with=paid_with, card_last4=scan_data.get("card_last4"),
            channel="whatsapp", receipt_doc_id=doc_id)
        if claim.get("duplicate"):
            where = f" (on {claim['project']})" if claim.get("project") else ""
            return (f"\n\n♻️ I've already logged this one — *R{total/100:,.2f}*"
                    f"{(' from ' + supplier) if supplier else ''}{where}. Skipped the duplicate.")

        # Purpose (petrol/clients/accommodation/other, migration 140/141) — cheap deterministic
        # vendor match first, LLM fallback only when that's inconclusive; only a genuinely
        # 'uncertain' result stays unclassified and asks, matching this platform's "auto-classify,
        # ask only when unsure" discipline.
        purpose_note = ""
        try:
            purpose = await expenses.classify_purpose_category(
                tenant_id, supplier or "", total, scan_data.get("notes") or "")
            if purpose in expenses.PURPOSE_CATEGORIES:
                expenses.set_purpose_category(tenant_id, claim["id"], purpose)
                claim["purpose_category"] = purpose
                purpose_note = (f"\n🗂️ Logged as *{purpose.title()}* — reply to change it "
                               f"if that's wrong.")
        except Exception as exc:
            logger.debug("purpose category classify skipped: %s", exc)

        cat = claim.get("category") or "expense"
        msg = f"\n\n💳 Logged as an expense: *R{total/100:,.2f}* → {cat}"
        if claim.get("vat_cents"):
            msg += f" (incl. VAT R{claim['vat_cents']/100:,.2f})"
        if claim.get("project"):
            msg += f", on *{claim['project']}*"
        if paid_with == "company_card":
            msg += " — paid on the *company card* (I'll match it to the bank statement)."
        elif paid_with == "personal":
            msg += f" — paid on a *personal card*, so it's owed back to {name or 'you'}. 👛"
        elif claim.get("reimbursable"):
            msg += f" — marked to reimburse {name or 'you'}. 👛"
        else:
            msg += "."
        # 2026-08-12 fix: these used to be if/elif — mutually exclusive, so whenever a project
        # allocation was also needed (the common case for a project-running tenant), the
        # payment-method question got silently dropped entirely, even with paid_with genuinely
        # unknown and registered company cards on file. The reply handler
        # (_maybe_allocate_pending_expense) already independently processes both answer types
        # in one incoming message, so asking both here is safe — a reply with just one piece of
        # info (e.g. "HPC") still resolves correctly, leaving the other question open.
        asks = []
        if not claim.get("project") and claim.get("needs_project"):
            asks.append("📍 Which project/site is this for? Reply with the site name, or 'none'.")
        if paid_with is None and expenses.list_cards(tenant_id):
            asks.append("💳 Was this the *company card* or *your own money*? Reply 'company' or 'own'.")
        if not claim.get("purpose_category"):
            asks.append("🗂️ What was this for — fuel, a client visit/meal, or accommodation? "
                        "Reply with the category.")
            # This is where the purpose question is FIRST asked; the re-prompt in
            # _maybe_allocate_pending_purpose is only ever a follow-up. Both have to record it,
            # or a genuine first answer would arrive with no record of the question and be
            # treated as a new topic.
            _note_purpose_prompt(phone)
        elif claim.get("purpose_category") == "petrol":
            # Only ever asked for petrol — a real KM logbook column, not a general expense field.
            asks.append("🚗 What's the odometer reading at this fill-up? Reply with the number.")
        if asks:
            msg += "\n" + "\n".join(asks)
        msg += purpose_note
        try:
            warn_line = expenses.budget_warning_line(tenant_id, phone)
            if warn_line:
                msg += warn_line
        except Exception as exc:
            logger.debug("expense budget warning skipped: %s", exc)
        return msg
    except Exception as exc:
        logger.warning("expense claim from receipt failed: %s", exc)
        return ""


async def _maybe_bank_review_answer(tenant_id: str, phone: str, text: str) -> Optional[str]:
    """If a bank-review question is outstanding AND the sender is on the tenant's team
    (never a customer), treat the message as the answer. Returns the reply or None."""
    try:
        from vula.integrations.notify import team_member_for_phone, _fallback_phone, _digits
        is_team = bool(team_member_for_phone(tenant_id, phone))
        if not is_team:
            fb = _fallback_phone(tenant_id)
            is_team = bool(fb and _digits(fb) == _digits(phone))
        if not is_team:
            from vula.models.tenants import get_tenant_db
            lk = get_tenant_db().lookup_by_phone_with_role(phone)
            is_team = bool(lk and lk.get("tenant_id") == tenant_id and lk.get("role") == "admin")
        if not is_team:
            return None
        from vula.commerce import bank_review
        return (await bank_review.handle_client_answer(tenant_id, text)
                or await bank_review.handle_answer(tenant_id, text))
    except Exception as exc:
        logger.debug("bank review answer skipped: %s", exc)
        return None


async def _maybe_allocate_pending_expense(tenant_id: str, phone: str, text: str) -> Optional[str]:
    """If this text is answering 'which project is that receipt for?', allocate the most recent
    unallocated WhatsApp expense claim from this sender. Returns a reply if handled, else None."""
    text = (text or "").strip()
    if not text or len(text) > 60:      # a project answer is short; long texts are real messages
        return None
    try:
        from vula.commerce import expenses, service
        low = text.lower()

        # Answering "company card or your own money?" on the latest unresolved claim.
        if any(k in low for k in ("company", "own", "personal", "my card", "my money", "cash")):
            rows = (service._client().table("commerce_expenses").select("*")
                    .eq("tenant_id", tenant_id).eq("paid_by", phone).eq("channel", "whatsapp")
                    .eq("status", "submitted").is_("paid_with", "null")
                    .order("updated_at", desc=True).limit(1).execute().data or [])
            if rows:
                claim = rows[0]
                is_company = "company" in low
                paid_with = "company_card" if is_company else ("cash" if "cash" in low else "personal")
                service._client().table("commerce_expenses").update(
                    {"paid_with": paid_with, "reimbursable": not is_company,
                     "updated_at": service._now()}).eq("id", claim["id"]).execute()
                amt = int(claim.get("amount_cents") or 0) / 100
                if is_company:
                    return (f"👍 Noted — that *R{amt:,.2f}* was on the company card. "
                            "I'll match it against the bank statement.")
                return (f"👍 Noted — *R{amt:,.2f}* was your own money, so it's marked "
                        "to be paid back to you. 👛")

        rows = (service._client().table("commerce_expenses").select("*")
                .eq("tenant_id", tenant_id).eq("paid_by", phone).eq("channel", "whatsapp")
                .eq("status", "submitted").is_("project", "null")
                .order("updated_at", desc=True).limit(1).execute().data or [])
        if rows:
            claim = rows[0]
            if low in ("none", "no", "no project", "personal", "office", "n/a", "na", "skip"):
                expenses.assign(tenant_id, claim["id"], project="")   # '' = resolved, not re-asked
                return "👍 Noted — no project. It's in your books."
            proj = expenses.match_project(tenant_id, text)
            if proj:
                expenses.assign(tenant_id, claim["id"], project=proj)
                return f"✅ Allocated that *R{int(claim.get('amount_cents') or 0)/100:,.2f}* expense to *{proj}*."

        # Odometer reading for a petrol claim (migration 142) — checked before the purpose
        # fallback since a bare number is unambiguous here and would otherwise risk being
        # misread by _maybe_allocate_pending_purpose as an "other" answer with numeric detail.
        odo_reply = await _maybe_allocate_pending_odometer(tenant_id, phone, text)
        if odo_reply is not None:
            return odo_reply

        # Last resort — a purpose-category question ("what was this for?") pending on one or
        # more uncertain claims. Only reached when the message didn't already resolve a
        # paid_with/project/odometer question above.
        return await _maybe_allocate_pending_purpose(tenant_id, phone, text)
    except Exception as exc:
        logger.debug("pending-expense allocate skipped: %s", exc)
        return None


# A message that is plainly a REQUEST, not an answer to "what was this receipt for?".
# 2026-09-02, real Gerflor transcript: with two receipts pending, the rep asked
# "Get company details and set reminder" TWICE and got the identical receipts listing back both
# times — their actual request never reached the agent at all. The pending-purpose handler
# swallowed every unparseable message and re-prompted, so the only way out of the conversation
# was to answer about the receipts. That is the "stuck in a loop" Ian reported.
_REQUEST_SHAPED = re.compile(
    r"^\s*(get|find|send|show|check|set|add|create|make|book|remind|call|email|draft|"
    r"list|update|cancel|what|where|when|who|which|why|how|can you|could you|please|"
    r"tell|give|look|search|research|explain|describe|open|start|pull|fetch|write|"
    r"forward|schedule|arrange|"
    r"kry|stuur|wys|maak|soek|skryf)\b",
    re.IGNORECASE,
)


# When we last asked THIS person "what was this receipt for?". Only a message arriving shortly
# after that question can plausibly be its answer.
#
# 2026-09-02: an allowlist of "request-shaped" verbs was the first attempt at this, and it broke
# again immediately — "Tell me taralay impressions" got the receipts prompt back because "tell"
# wasn't in the list. Any verb not thought of re-traps the user, so guessing at verbs is the
# wrong shape of solution entirely. Whether we JUST ASKED is the real signal, and it needs no
# vocabulary. In-memory by design: a restart forgets we asked, which fails toward letting
# messages through — the safe direction.
_purpose_prompted_at: dict = {}
_PURPOSE_PROMPT_WINDOW_S = 900.0     # 15 minutes


def _note_purpose_prompt(phone: str) -> None:
    import time as _t
    _purpose_prompted_at[phone] = _t.monotonic()


def _recently_asked_about_purpose(phone: str) -> bool:
    import time as _t
    last = _purpose_prompted_at.get(phone)
    return bool(last and (_t.monotonic() - last) < _PURPOSE_PROMPT_WINDOW_S)


# Second-person / object pronouns mean the message is aimed AT Vula, not describing a purchase.
_ADDRESSES_ASSISTANT = re.compile(r"\b(me|us|you|your|yours)\b", re.IGNORECASE)


def _looks_like_purpose_attempt(text: str, parsed: dict) -> bool:
    """True when the message plausibly IS an answer to the pending purpose question.

    Deliberately conservative in the direction of letting a message THROUGH: re-prompting over
    somebody's real request is the failure that traps them, whereas passing a genuine purpose
    reply to the agent merely means it gets answered conversationally and asked again later.
    """
    t = (text or "").strip()
    if not t:
        return False
    if parsed:
        return True            # at least one "1 fuel"-shaped pair — a real, if partial, attempt
    # Request-shape is checked FIRST: "Schedule a meeting" contains the category word
    # "meeting" but is plainly an instruction, and matching the category first would have
    # logged it as an expense purpose.
    if t.endswith("?") or _REQUEST_SHAPED.match(t):
        return False           # a question or an instruction — a different ask entirely
    # A purpose describes a THING BOUGHT ("printer ink for the office"); a request addresses
    # Vula ("tell me...", "send us..."). Addressing the assistant is the generic signal the
    # verb list kept missing — it catches the whole "<any verb> me/us/you" family at once,
    # including the "Tell me taralay impressions" that re-broke this inside the ask window.
    # "my"/"our" are deliberately absent: "petrol for my car" is a perfectly good purpose.
    if _ADDRESSES_ASSISTANT.search(t):
        return False
    from vula.commerce import expenses
    if expenses.match_purpose_category(t):
        return True            # names a real category ("fuel", "client lunch") — unambiguous
    # Otherwise assume it IS a purpose: a purpose is often free text with no category word at
    # all ("printer ink for the office"), so requiring a match here would reject real answers.
    return len(t) <= 60        # purposes are short; anything longer is prose, not a reason


async def _maybe_allocate_pending_purpose(tenant_id: str, phone: str, text: str) -> Optional[str]:
    """Resolve a 'what was this for?' reply against the sender's purpose_category-pending
    WhatsApp claims. A single pending claim takes a plain free-text answer; two or more pending
    at once (several receipts scanned back-to-back before a reply) need an indexed reply
    (\"1 fuel, 2 client lunch\") since a plain answer is ambiguous about which claim it means."""
    try:
        from vula.commerce import expenses, service
        rows = (service._client().table("commerce_expenses").select("*")
                .eq("tenant_id", tenant_id).eq("paid_by", phone).eq("channel", "whatsapp")
                .eq("status", "submitted").is_("purpose_category", "null")
                .order("updated_at", desc=True).execute().data or [])
        if not rows:
            return None

        # 2026-08-28: real incident — a claim's supplier ("Tony Beato Service Station")
        # deterministically matches expenses.classify_purpose_category_deterministic's petrol
        # regex, yet its purpose_category sat NULL in the DB (the initial auto-classify attempt
        # at claim-creation time must have hit a transient failure — silently swallowed there by
        # design). This function kept re-asking about it on every reply instead of ever
        # re-checking, so the rep never got a way out. Self-heal here: re-run the cheap,
        # no-LLM deterministic pass on every still-pending claim before doing anything else —
        # anything that now confidently matches gets classified immediately and drops out of
        # the pending list, rather than being asked about again.
        still_pending = []
        for row in rows:
            det = expenses.classify_purpose_category_deterministic(row.get("supplier") or "")
            if det:
                expenses.set_purpose_category(tenant_id, row["id"], det)
            else:
                still_pending.append(row)
        rows = still_pending
        if not rows:
            return None

        # A bare number never describes a purpose — most likely an odometer reading that has
        # nowhere else to land right now (e.g. no pending odometer question exists yet). Leave
        # it unresolved rather than forcing it into "other" with a numeric detail.
        if re.fullmatch(r"[\d,\s]+(?:kms?\.?)?", text, flags=re.IGNORECASE):
            return None

        if len(rows) == 1:
            claim = rows[0]
            cat = expenses.match_purpose_category(text)
            # With one claim pending, ANY free text used to be accepted as its purpose — so a
            # rep asking an unrelated question got their question filed as the expense reason
            # AND never got an answer. Same trap as the multi-pending path below.
            if not cat and not (_recently_asked_about_purpose(phone)
                                and _looks_like_purpose_attempt(text, {})):
                logger.info("single pending purpose left alone — we didn't just ask, or the "
                            "message isn't a purpose: %.60s", text)
                return None
            detail = None if cat else text
            cat = cat or "other"
            expenses.set_purpose_category(tenant_id, claim["id"], cat, detail=detail)
            amt = int(claim.get("amount_cents") or 0) / 100
            msg = f"👍 Logged that *R{amt:,.2f}* as *{cat.title()}*."
            if cat == "petrol":
                msg += "\n🚗 What's the odometer reading at this fill-up? Reply with the number."
            return msg

        # 2+ pending — require an indexed reply matching every pending claim, oldest→newest by
        # the same order they were listed in.
        pairs = re.findall(r"(\d+)\s*[:.\-]?\s*([^,\n]+)", text)
        n = len(rows)
        parsed: Dict[int, str] = {}
        if pairs:
            for idx_s, val in pairs:
                idx = int(idx_s)
                if 1 <= idx <= n and idx not in parsed:
                    parsed[idx] = val.strip()
        if len(parsed) == n:
            lines = []
            any_petrol = False
            for i, claim in enumerate(rows, start=1):
                cat = expenses.match_purpose_category(parsed[i])
                detail = None if cat else parsed[i]
                cat = cat or "other"
                expenses.set_purpose_category(tenant_id, claim["id"], cat, detail=detail)
                any_petrol = any_petrol or cat == "petrol"
                lines.append(f"R{int(claim.get('amount_cents') or 0)/100:,.2f} → {cat.title()}")
            result = "👍 Logged:\n" + "\n".join(lines)
            if any_petrol:
                result += "\n🚗 What's the odometer reading for the petrol one(s)? Reply with the number."
            return result

        # Couldn't parse a full indexed reply. If the message wasn't even an ATTEMPT at one,
        # it's a different request and must reach the agent — see _looks_like_purpose_attempt.
        if not (parsed or (_recently_asked_about_purpose(phone)
                           and _looks_like_purpose_attempt(text, parsed))):
            logger.info("pending-purpose prompt skipped — we didn't just ask, or the message "
                        "isn't a purpose reply: %.60s", text)
            return None
        listing = "\n".join(
            f"{i}) R{int(c.get('amount_cents') or 0)/100:,.2f} {c.get('supplier') or ''}".strip()
            for i, c in enumerate(rows, start=1))
        _note_purpose_prompt(phone)
        return (f"You've got {n} receipts I need the purpose for:\n{listing}\n\n"
                f"Reply like \"1 fuel, 2 client lunch\" so I know which is which.")
    except Exception as exc:
        logger.debug("pending purpose-category allocate skipped: %s", exc)
        return None


async def _maybe_allocate_pending_odometer(tenant_id: str, phone: str, text: str) -> Optional[str]:
    """Resolve 'what's the odometer reading at this fill-up?' against the sender's petrol
    claims still missing one. Same single-vs-multi-pending shape as
    _maybe_allocate_pending_purpose, keyed on odometer_km instead of purpose_category —
    multi-pending needs an indexed reply too (a bare number alone can't say WHICH fill-up)."""
    from vula.commerce import expenses
    try:
        from vula.commerce import service
        rows = (service._client().table("commerce_expenses").select("*")
                .eq("tenant_id", tenant_id).eq("paid_by", phone).eq("channel", "whatsapp")
                .eq("status", "submitted").eq("purpose_category", "petrol")
                .is_("odometer_km", "null").order("updated_at", desc=True).execute().data or [])
        if not rows:
            return None

        if len(rows) == 1:
            km = expenses.parse_odometer_reading(text)
            if km is None:
                return None
            claim = rows[0]
            expenses.set_odometer(tenant_id, claim["id"], km)
            amt = int(claim.get("amount_cents") or 0) / 100
            return f"👍 Noted — {km:,} km at that *R{amt:,.2f}* fill-up."

        # 2+ pending — an indexed reply ("1 45280, 2 46100") is the only unambiguous shape;
        # a bare number can't say which fill-up it belongs to.
        pairs = re.findall(r"(\d+)\D+(\d+)", text)
        n = len(rows)
        parsed: Dict[int, int] = {}
        for idx_s, km_s in pairs:
            idx, km = int(idx_s), expenses.parse_odometer_reading(km_s)
            if 1 <= idx <= n and idx not in parsed and km is not None:
                parsed[idx] = km
        if len(parsed) == n:
            lines = []
            for i, claim in enumerate(rows, start=1):
                expenses.set_odometer(tenant_id, claim["id"], parsed[i])
                lines.append(f"{parsed[i]:,} km → R{int(claim.get('amount_cents') or 0)/100:,.2f}")
            return "👍 Noted:\n" + "\n".join(lines)

        if not expenses.parse_odometer_reading(text) and not pairs:
            return None  # doesn't look like an odometer reply at all — leave it unresolved

        listing = "\n".join(
            f"{i}) R{int(c.get('amount_cents') or 0)/100:,.2f} {c.get('supplier') or ''} "
            f"({(c.get('date') or '')[:10]})".strip()
            for i, c in enumerate(rows, start=1))
        return (f"You've got {n} fill-ups I need the odometer reading for:\n{listing}\n\n"
                f"Reply like \"1 45280, 2 46100\" so I know which is which.")
    except Exception as exc:
        logger.debug("pending odometer allocate skipped: %s", exc)
        return None


async def _backfill_receipt_url(tenant_id: str, receipt_doc_id: str, filed_row: Optional[dict]) -> None:
    """Link a just-created expense claim back to its real, durable receipt photo URL.
    Confirmed live 2026-08-26: commerce_expenses.receipt_url was always NULL — the claim gets
    created (via _log_expense_claim, keyed by receipt_doc_id) BEFORE the storage upload below
    produces a real URL, and nothing ever wrote it back. Best-effort — a failure here never
    affects the claim/filing that already succeeded."""
    if not filed_row or not filed_row.get("file_url"):
        return
    try:
        from vula.commerce import service as _cs_receipt
        _cs_receipt._client().table("commerce_expenses").update(
            {"receipt_url": filed_row["file_url"]}
        ).eq("tenant_id", tenant_id).eq("receipt_doc_id", receipt_doc_id).execute()
    except Exception as exc:
        logger.debug("receipt_url backfill skipped for %s: %s", receipt_doc_id, exc)


async def _file_uploaded_document(tenant_id, phone, result, local_path, mime_type,
                                  category, summary, fields, already_committed=False,
                                  source="whatsapp"):
    """Match the document to a project and file it (durable copy + record + ClickUp).
    Also books it via the shared commit path (same as email/Smart Scanner) when it's a
    financial category the vision-scan shortcut hasn't already committed.
    Returns (note, row): note is a WhatsApp note ('Filed under X' or a 'which project?'
    question); row is the vula_filed_documents row (has the real durable file_url) — the
    caller uses this to backfill commerce_expenses.receipt_url when this same photo was
    already turned into an expense claim (see _log_expense_claim's call site above)."""
    try:
        from vula.integrations.doc_filing import (match_project, file_document, project_examples,
                                                  lookup_learned_project)
        data = local_path.read_bytes()
        ctype = mime_type or "application/octet-stream"

        # Customer linkage (migration 109) — a document is only auto-tagged to a customer when
        # it genuinely came FROM one (has real order history), not from the tenant's own staff
        # filing a supplier invoice (the common case for `filed_by`/`phone` here). A light,
        # single indexed lookup — not the full _aggregate_customers union, which is overkill for
        # a per-document "is this a known customer" check.
        customer_phone = None
        try:
            digits = "".join(c for c in (phone or "") if c.isdigit())
            digits = "27" + digits[1:] if digits.startswith("0") else digits
            from vula.commerce import service as _cs_doc
            has_orders = (_cs_doc._client().table("commerce_orders").select("id")
                          .eq("tenant_id", tenant_id).eq("customer_phone", digits)
                          .limit(1).execute().data or [])
            if has_orders:
                customer_phone = digits
        except Exception as exc:
            logger.debug("customer linkage check skipped: %s", exc)
        hint = " ".join([
            result.filename or "", summary or "",
            " ".join(str(v) for v in (fields or {}).values() if isinstance(v, (str, int, float))),
        ])
        # Learned rules first (from past corrections — same convergence as the email pipeline
        # in sync.py::_file_attachment), then the general matcher. Only a non-ambiguous match
        # with a resolved project auto-files — this used to auto-file on ANY truthy match,
        # including a single coincidental token, with no ambiguity check at all.
        match = lookup_learned_project(tenant_id, fields) or match_project(tenant_id, hint)
        # 2026-08-08 fix: confidence >= 0.6 required, not just "some project, not ambiguous".
        # The weakest tier — match_project()'s canonical-register substring fallback, confidence
        # 0.5, explicitly "no separate ambiguity check" (doc_filing.py:126-138) — used to count
        # as confident on its own, so a first-time/unknown payee with no learned history (e.g. a
        # payment notification to someone Vula has never filed for before) got silently
        # auto-filed on a loose guess instead of asking. Confirmed live: a cleaner's first-ever
        # proof of payment got silently filed under a project she'd never been linked to. A real
        # ClickUp/field-ops keyword match (0.6) or a previously-confirmed learned rule (1.0)
        # still auto-files with no added friction — only the weak fallback now asks.
        confident = bool(match and not match.get("ambiguous") and match.get("project")
                         and (match.get("confidence") or 0) >= 0.6)
        if confident:
            row = await file_document(
                tenant_id, filename=result.filename, data=data, content_type=ctype,
                category=category, summary=summary, fields=fields, doc_id=result.doc_id,
                source=source, filed_by=phone, project=match["project"],
                clickup_list_id=match.get("clickup_list_id"), status="filed",
                customer_phone=customer_phone,
            )
            note = f"📂 Filed under *{match['project']}*."
            if row.get("clickup_task_id"):
                note += " Added to ClickUp."
        else:
            # No confident match (none at all, or an unresolved tie) — store as pending and ask.
            row = await file_document(
                tenant_id, filename=result.filename, data=data, content_type=ctype,
                category=category, summary=summary, fields=fields, doc_id=result.doc_id,
                source=source, filed_by=phone, status="pending_project",
                customer_phone=customer_phone,
            )
            candidates = match.get("candidates") if match else None
            if candidates:
                note = ("📂 That could be more than one project — " + " / ".join(candidates) +
                        ". Reply with the exact project name (or 'skip').")
            else:
                ex = project_examples(tenant_id)
                hint_txt = f" (e.g. {', '.join(ex)})" if ex else ""
                note = (f"📂 Which project is this for?{hint_txt} "
                        f"Reply with the project name and I'll file it (or 'skip').")

        if not already_committed and category in _FINANCIAL_DOC_CATEGORIES:
            try:
                from vula.commerce import service as commerce_service
                # _analyze_document's extraction never sets doc_type (only the Smart Scanner's
                # vision-scan schema does) — commit_inbound_document defaults a missing doc_type
                # to "receipt", which would silently misfile every real invoice/quote as an
                # expense. Map the deep-analysis category across explicitly.
                commit_fields = dict(fields or {})
                commit_fields.setdefault("doc_type", _CATEGORY_TO_DOC_TYPE.get(category, "invoice"))
                await commerce_service.commit_inbound_document(
                    tenant_id, commit_fields, auto_commit=True, source=source,
                    filed_document_id=row.get("id") if row else None,
                    project=match["project"] if confident else None,
                    is_boq=(category == "Bill of Quantities (BOQ)"),
                )
            except Exception as exc:
                logger.warning("commit_inbound_document failed for %s: %s", result.filename, exc)

        if category in _CONTACT_DOC_CATEGORIES:
            try:
                from vula.commerce import service as commerce_service
                cf = fields or {}
                cname = (cf.get("name") or "").strip()
                if cname:
                    cphone = cf.get("phone") or ""  # already normalised above
                    from uuid import uuid4 as _uuid4
                    row_c = {
                        "tenant_id": tenant_id, "phone": cphone or f"nophone-{_uuid4().hex[:10]}",
                        "name": cname, "email": cf.get("email") or None,
                        "company": cf.get("company") or None, "title": cf.get("title") or None,
                        "source": "whatsapp_scan", "created_by": phone,
                    }
                    try:
                        commerce_service._client().table("commerce_contacts").upsert(
                            row_c, on_conflict="tenant_id,phone").execute()
                    except Exception as exc2:
                        if not any(k in str(exc2) for k in ("company", "title", "created_by")):
                            raise
                        for k in ("company", "title", "created_by"):
                            row_c.pop(k, None)
                        commerce_service._client().table("commerce_contacts").upsert(
                            row_c, on_conflict="tenant_id,phone").execute()
                    note = f"📇 Saved *{cname}*" + (f" ({cf['company']})" if cf.get("company") else "") + " to your contacts."
            except Exception as exc:
                logger.warning("Business card contact save failed for %s: %s", result.filename, exc)

        supplier_field = _SUPPLIER_CHECK_FIELD.get(category)
        if supplier_field:
            try:
                from datetime import datetime as _dt2
                from vula.commerce import service as commerce_service
                pf = fields or {}
                payee_name = (pf.get(supplier_field) or "").strip()
                if payee_name:
                    match = await commerce_service.match_supplier(
                        tenant_id, name=payee_name, tax_id=pf.get("tax_id"))
                    if match and match.get("auto_apply"):
                        supplier_note = f"🏢 *{payee_name}* is already on your supplier list."
                    else:
                        details = []
                        if pf.get("payee_bank"):
                            details.append(f"Bank: {pf['payee_bank']}")
                        if pf.get("payee_branch_code"):
                            details.append(f"Branch code: {pf['payee_branch_code']}")
                        if pf.get("tax_id"):
                            details.append(f"Tax/VAT no: {pf['tax_id']}")
                        await commerce_service.upsert_supplier(tenant_id, {
                            "name": payee_name,
                            "account_number": pf.get("payee_account_number"),
                            "tax_id": pf.get("tax_id"),
                            "notes": ("; ".join(details) + " — " if details else "") +
                                     f"added automatically from a {category.lower()} "
                                     f"forwarded {_dt2.now().strftime('%Y-%m-%d')}.",
                        })
                        supplier_note = f"🏢 Added *{payee_name}* to your supplier list."
                    note = (note + "\n\n" if note else "") + supplier_note
            except Exception as exc:
                logger.warning("Supplier auto-add from %s failed for %s: %s",
                              category, result.filename, exc)

        return note, row
    except Exception as exc:
        logger.warning("Document filing failed for %s: %s", getattr(result, "filename", "?"), exc)
        return "", None


# Caption keywords that mark a photo as "money I spent" → route to the expense/books path.
_EXPENSE_WORDS = ("expense", "receipt", "slip", "claim", "petty cash", "bought", "paid for", "till slip")

_DOC_CATEGORIES = [
    "Fee Proposal / Schedule", "Contract / Agreement", "Bill of Quantities (BOQ)",
    "Quote / Estimate", "Invoice", "Drawing / Plan", "Specification",
    "Meeting Minutes", "Programme / Schedule", "Report", "Tender Document",
    "Business Card", "Proof of Payment", "General Document",
    # 2026-08-24 (structured starter KB): this vocabulary was entirely trades/services-
    # skewed — a food/retail/health tenant's real documents mostly landed in "General
    # Document". Added categories each niche's KB actually needs, reusing this exact same
    # vocabulary (vula/commerce/starter_kb.py's business_type -> category mapping).
    "Menu / Price List", "Supplier Agreement", "Health & Safety Policy", "Booking Policy",
    # 2026-08-27: a photo of a building's exterior/signage (site visit, prospective client's
    # premises) had no home — fell into "General Document" with whatever the OCR-text pass
    # happened to guess. Same fix class as Business Card: a dedicated category + direct vision
    # scan, since the thing worth extracting (a street-sign address) is exactly the small/
    # angled/distant text the OCR-text pipeline is unreliable on.
    "Site / Building Photo",
]

# Business Card fields land straight in commerce_contacts (see the write-back hook in
# _file_uploaded_document) — separate from _FINANCIAL_DOC_CATEGORIES's money shape.
_CONTACT_DOC_CATEGORIES = {"Business Card"}

# 2026-08-17: a bank's own "Notification of Payment"/EFT confirmation PDF (the tenant paying a
# SUPPLIER, not a customer paying the tenant — the existing dtp=="payment_confirmation" photo
# path in _handle_document is the opposite direction and image-only, so PDFs like this never hit
# it). Confirmed real via DIGG's actual FNB payment notifications. Deliberately different from a
# scanned receipt's read-only supplier lookup (_log_expense_claim) — a reimbursement receipt
# isn't proof the business has an ongoing relationship with that payee, while a real EFT-to-
# supplier notification or a supplier's own invoice is. Extended same-day to cover real supplier
# invoices/quotes/BOQs too (same check-existing/add-if-not behavior, just keyed off the
# "supplier" field _FINANCIAL_DOC_CATEGORIES already extracts instead of "payee_name").
_SUPPLIER_CHECK_FIELD = {
    "Proof of Payment": "payee_name",
    "Invoice": "supplier",
    "Quote / Estimate": "supplier",
    "Bill of Quantities (BOQ)": "supplier",
}

# Categories whose `fields` are extracted in the Smart-Scanner-aligned money shape
# (supplier/tax_id/total_cents/vat_cents/line_items) and are therefore both arithmetic-
# checkable (extraction_quality.scan_quality_ok) and eligible for commit_inbound_document().
_FINANCIAL_DOC_CATEGORIES = {"Invoice", "Quote / Estimate", "Bill of Quantities (BOQ)"}

# commit_inbound_document()'s doc_type vocabulary ("invoice" | "quote" | "delivery_note" | ...)
# doesn't match _analyze_document's category labels — map across explicitly. A BOQ is priced
# and sent like a quote, so it maps to "quote" too (there's no separate doc_type for it).
_CATEGORY_TO_DOC_TYPE = {
    "Invoice": "invoice",
    "Quote / Estimate": "quote",
    "Bill of Quantities (BOQ)": "quote",
}


def _sanitize_json_string(raw: str) -> str:
    """LLMs sometimes emit literal control characters (a real newline/tab) inside a JSON
    string value instead of escaping them (\\n, \\t) — invalid per the JSON spec but a
    well-known model quirk (e.g. a multi-line "summary" or "notes" field), and the actual
    cause of "Expecting ',' delimiter" errors seen in production doc analysis. Walk the
    string once, tracking whether we're inside a quoted string (respecting existing escape
    sequences), and escape stray control characters only there — formatting/whitespace
    between tokens is left untouched."""
    out = []
    in_string = False
    escape = False
    for ch in raw:
        if in_string:
            if escape:
                out.append(ch)
                escape = False
            elif ch == "\\":
                out.append(ch)
                escape = True
            elif ch == '"':
                out.append(ch)
                in_string = False
            elif ch == "\n":
                out.append("\\n")
            elif ch == "\t":
                out.append("\\t")
            elif ch == "\r":
                out.append("\\r")
            else:
                out.append(ch)
        else:
            if ch == '"':
                in_string = True
            out.append(ch)
    return "".join(out)


async def _analyze_document(tenant_id: str, filename: str, local_path) -> Optional[dict]:
    """Deep-analyze an uploaded document: read its content, classify it, and
    pull out the structured fields worth keeping in the backend.

    Returns {"category", "summary", "fields"} or None if analysis fails.
    Best-effort — the document is already in the KB regardless.
    """
    try:
        from vula.integrations.metering import set_request_tenant
        set_request_tenant(tenant_id)
    except Exception:
        pass

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
        from core.llm_router import resolve_cheap_route, resolve_cloud_route
        from vula.commerce.extraction_quality import scan_quality_ok
        litellm.drop_params = True
        model, api_key, api_base = await resolve_cheap_route()

        cats = ", ".join(_DOC_CATEGORIES)
        _msgs = [
                {"role": "system", "content":
                    "You are Vula's document analyst for a South African construction/business. "
                    "Read the document and return STRICT JSON only (no prose) with keys: "
                    f"category (one of: {cats}), summary (1-2 sentences), and fields (an object of "
                    "the key structured data for that category). For Invoice, Quote / Estimate, or "
                    "Bill of Quantities (BOQ), fields MUST use this exact shape: "
                    '{"supplier": string|null, "tax_id": string|null, "date": "YYYY-MM-DD"|null, '
                    '"due_date": "YYYY-MM-DD"|null, "total_cents": integer|null, '
                    '"vat_cents": integer|null, "confidence": "high"|"medium"|"low", '
                    '"line_items": [{"description": string, "quantity": number, '
                    '"unit_price_cents": integer|null, "total_cents": integer|null}]} — money in '
                    "CENTS (Rands × 100), never Rand floats — this is the same shape/units the "
                    "Smart Scanner already uses, so both pipelines can be verified and booked the "
                    "same way. For Business Card, fields MUST use this exact shape: "
                    '{"name": string|null, "company": string|null, "title": string|null, '
                    '"phone": string|null, "email": string|null}. For Proof of Payment (a bank '
                    "own payment notification/EFT confirmation showing money paid OUT to someone "
                    "- NOT a customer proving they paid the business), fields MUST use this exact "
                    'shape: {"payer": string|null, "payee_name": string|null, "payee_bank": '
                    'string|null, "payee_branch_code": string|null, "payee_account_number": '
                    'string|null, "amount_cents": integer|null, "reference": string|null, '
                    '"trace_id": string|null, "date": "YYYY-MM-DD"|null} - money in CENTS. '
                    "For Site / Building Photo (a photo of a building's exterior/signage, not a "
                    "document held up to the camera), fields MUST use this exact shape: "
                    '{"address": string|null, "business_name": string|null, "notes": '
                    "string|null}. For every other category (fee "
                    "proposal, contract, drawing, specification, etc.), use whatever key "
                    "structured data best fits — e.g. fee proposal: client, stages, total; "
                    "contract: parties, value, dates. Use null when unknown."},
                {"role": "user", "content": f"Filename: {filename}\n\nDocument:\n{text}\n\nJSON:"},
        ]

        def _parse(_resp):
            raw = (_resp.choices[0].message.content or "").strip()
            if "</think>" in raw:                       # local reasoning models
                raw = raw.split("</think>")[-1].strip()
            raw = raw.replace("```json", "").replace("```", "").strip()
            i, j = raw.find("{"), raw.rfind("}")
            candidate = raw[i:j + 1] if i >= 0 and j > i else raw
            try:
                data = _json.loads(candidate)
            except _json.JSONDecodeError:
                try:
                    # Handles raw control characters (newline/tab) left unescaped inside a
                    # string value — see _sanitize_json_string.
                    data = _json.loads(_sanitize_json_string(candidate))
                except _json.JSONDecodeError:
                    # Handles the harder case: an unescaped literal quote INSIDE a string
                    # value (e.g. a door schedule's 32" x 80" — the inch mark terminates the
                    # string early as far as a strict parser is concerned). Ambiguous to fix
                    # with a hand-rolled character scan, so defer to a purpose-built repair
                    # library for malformed LLM JSON output.
                    import json_repair
                    data = json_repair.loads(candidate)
            cat = data.get("category") or "General Document"
            if cat not in _DOC_CATEGORIES:
                cat = "General Document"
            return {"category": cat, "summary": (data.get("summary") or "").strip(),
                    "fields": data.get("fields") or {}}

        def _needs_escalation(result: Optional[dict]) -> bool:
            """Empty/failed read → escalate (unchanged). NEW: for a financial category, also
            never trust a self-reported confidence alone — cross-check the extracted line-item
            arithmetic against the stated total (same heuristic the Smart Scanner already
            uses), and escalate on a mismatch too."""
            if not (result and (result.get("summary") or result.get("fields"))):
                return True
            if result.get("category") in _FINANCIAL_DOC_CATEGORIES:
                if not scan_quality_ok(result.get("fields") or {}):
                    return True
            return False

        # Cheap pass first (free local via tunnel / gemini-flash).
        result = None
        try:
            resp = await litellm.acompletion(model=model, messages=_msgs, temperature=0.1,
                max_tokens=900, api_key=api_key, api_base=api_base)
            result = _parse(resp)
        except Exception as exc:
            logger.debug("Doc analyze cheap pass failed: %s", exc)

        # Escalate to the 70B on an empty/failed read, OR (financial categories only) on a
        # weak/arithmetically-inconsistent extraction — not just on the model's own say-so.
        if _needs_escalation(result):
            cloud = resolve_cloud_route()
            if cloud:
                try:
                    logger.info("Doc analyze: cheap weak — escalating %s to 70B", filename)
                    _cm, _ck, _cb = cloud
                    resp = await litellm.acompletion(model=_cm, messages=_msgs, temperature=0.1,
                        max_tokens=900, api_key=_ck, api_base=_cb)
                    cloud_result = _parse(resp)
                    # Keep the cloud result if it's usable at all — even one that still fails
                    # the arithmetic check is a better fallback than a totally empty local read.
                    if cloud_result and (cloud_result.get("summary") or cloud_result.get("fields")):
                        result = cloud_result
                except Exception as exc2:
                    logger.warning("Doc analyze escalation failed for %s: %s", filename, exc2)
        return result
    except Exception as exc:
        logger.warning("Doc analyze setup failed for %s: %s", filename, exc)
        return None


def _friendly_document_name(category: str, fields: dict, original_filename: str) -> str:
    """Build a human-readable filename from the classified category + whatever identity the
    extraction found, replacing WhatsApp's opaque wamid-based fallback name for an uncaptioned
    photo (e.g. 'image-wamid.HBgLMjc2...jpg'). Falls back to a timestamped category name if
    nothing identifying was extracted — never returns something as opaque as the input."""
    from pathlib import Path as _Path
    from datetime import datetime as _dt

    ext = _Path(original_filename).suffix or ".jpg"
    ident = (fields.get("name") or fields.get("supplier") or fields.get("client")
            or fields.get("company") or fields.get("business_name") or "")
    ident = re.sub(r"[^A-Za-z0-9 ]+", "", ident).strip()
    label = category.split(" / ")[0].split(" (")[0].strip()  # "Quote / Estimate" -> "Quote"
    base = f"{label} - {ident}" if ident else label
    base = re.sub(r"\s+", " ", base).strip()
    ts = _dt.now().strftime("%Y%m%d-%H%M")
    return f"{base} {ts}{ext}"


def _flatten_extraction_item(item: Any) -> str:
    """Compact one-line rendering of a dict/list-of-scalars value found inside a list field
    (e.g. one entry of a project billboard's "team" array: role/company/contact_details) —
    joins whatever scalar values are actually present, recursing one level into a nested dict
    (e.g. contact_details: {email, phone, website}). Never invents structure that isn't there."""
    if isinstance(item, dict):
        parts = []
        for v in item.values():
            if v is None or v == "":
                continue
            if isinstance(v, dict):
                sub = _flatten_extraction_item(v)
                if sub:
                    parts.append(sub)
            elif isinstance(v, list):
                continue  # avoid unbounded nesting inside a list-of-dicts item
            else:
                parts.append(str(v))
        return ", ".join(parts)
    if item is None or item == "":
        return ""
    return str(item)


def _format_extraction(analysis: dict) -> str:
    """Turn a structured analysis into a short WhatsApp breakdown.

    2026-08-27 fix: this used to print every field's raw key/value verbatim — for a `*_cents`
    field (e.g. amount_cents=280000) that meant a customer-facing reply literally saying
    "Amount Cents: 280000" instead of "Amount: R2,800.00". Confirmed live on a real digg-demo
    payment notification. Any `*_cents` field is now converted to Rand and has "Cents" dropped
    from its label; everything else is unchanged.

    2026-08-27, same day: this ALSO used to silently drop any field whose value was a list or
    dict entirely — confirmed live on a real project billboard photo (gerflor): the extraction
    correctly pulled a "team" array (architect/engineer/contractor/H&S consultant, each with
    email/phone/website) but every one of those contacts vanished from the WhatsApp reply,
    leaving only the project name and address — "missed all the context". Financial line_items
    stay a count-only summary (a full itemised breakdown would make every invoice/quote reply
    huge); every OTHER list-of-dicts field (team, transactions, etc.) now renders as compact
    sub-bullets instead of disappearing."""
    lines = []
    fields = analysis.get("fields") or {}
    for k, v in fields.items():
        if v is None or v == "" or k in ("items", "line_items"):
            continue
        if isinstance(v, list):
            if not v:
                continue
            label = k.replace("_", " ").title()
            lines.append(f"• {label}:")
            for item in v[:5]:
                rendered = _flatten_extraction_item(item)
                if rendered:
                    lines.append(f"   - {rendered}")
            continue
        if isinstance(v, dict):
            rendered = _flatten_extraction_item(v)
            if rendered:
                label = k.replace("_", " ").title()
                lines.append(f"• {label}: {rendered}")
            continue
        if k.endswith("_cents"):
            try:
                v = f"R{int(v) / 100:,.2f}"
            except (TypeError, ValueError):
                continue  # not actually numeric — drop rather than show a bogus "R" value
            k = k[: -len("_cents")]
        label = k.replace("_", " ").title()
        lines.append(f"• {label}: {v}")
    line_items = fields.get("line_items") or fields.get("items")
    if isinstance(line_items, list) and line_items:
        lines.append(f"• Line items: {len(line_items)}")
    return "\n".join(lines[:16])


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
        ("Proof of Payment",        ["notification of payment", "proof of payment",
                                     "payment confirmation", "payment instruction has been received",
                                     "eft confirmation"]),
    ]
    for label, keywords in rules:
        if any(k in blob for k in keywords):
            return label
    return "General Document"


async def _download_media_bytes(media_id: str) -> Optional[bytes]:
    """Fetch raw media bytes from the Meta Graph API (2-step: get URL, then download)."""
    if not settings.whatsapp_token:
        logger.warning("WHATSAPP_TOKEN not set — cannot download media")
        return None
    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            info = await client.get(
                f"{settings.whatsapp_api_url}/{media_id}",
                headers={"Authorization": f"Bearer {settings.whatsapp_token}"},
            )
            info.raise_for_status()
            url = info.json().get("url", "")
            if not url:
                return None
            dl = await client.get(
                url, headers={"Authorization": f"Bearer {settings.whatsapp_token}"}
            )
            dl.raise_for_status()
            return dl.content
    except Exception as exc:
        logger.error("media download failed for media_id=%s: %s", media_id, exc)
        return None


async def _handle_voice_note(
    phone: str, media_id: str, mime_type: str, msg_id: str,
    route_mode: str, route_tenant: Optional[str],
) -> None:
    """Transcribe an inbound voice note and route the text like a typed message."""
    audio = await _download_media_bytes(media_id)
    if not audio:
        await _send_reply(phone, "Sorry, I couldn't fetch that voice note. Please type your message. 🙏", route_tenant or "")
        return
    text, lang = await transcribe_audio(
        audio, mime_type=mime_type, filename=f"voice-{msg_id}.ogg", tenant_id=route_tenant
    )
    if not text:
        # Transcription is down (real telemetry: 26% of all voice notes ever received were lost
        # to the local Whisper tunnel being unreachable, never to a genuine transcription
        # failure). Park the audio and retry locally rather than lose the order — and say so
        # honestly, instead of blaming the customer's recording.
        queued = False
        if route_tenant:
            try:
                from vula import voice_retry
                queued = voice_retry.enqueue(
                    route_tenant, phone, audio, mime_type=mime_type, msg_id=msg_id,
                    route_mode=route_mode,
                )
            except Exception as exc:
                logger.warning("voice retry enqueue failed: %s", exc)
        if queued:
            await _send_reply(phone, (
                "Sorry — I can't listen to voice notes right this second. 🎙️ I've saved yours "
                "and I'll come back to you as soon as I can. If it's urgent, type it out and "
                "I'll help right away."
            ), route_tenant or "")
        else:
            await _send_reply(phone, (
                "I couldn't make out that voice note. 🎙️ Could you type it out for me? "
                "/ Ek kon nie die stemboodskap hoor nie — tik dit asseblief."
            ), route_tenant or "")
        return
    # We only understand SPEECH in English & Afrikaans. If Whisper labels the note as another
    # language, the transcript is unreliable — ask them to type (we CAN reply in text in their
    # language) or send a voice note in a supported one. Don't process the garbled transcript.
    from core.lang import is_voice_supported
    if not is_voice_supported(lang):
        await _send_reply(phone, (
            "👋 I can only understand *voice notes* in English and Afrikaans right now. "
            "Please *type* your message in your own language — I'll reply in it — or send a "
            "voice note in English or Afrikaans. Enkosi! Ngiyabonga! Dankie! 🙏"
        ), route_tenant or "")
        logger.info("voice note in unsupported language (lang=%s) from %s — asked to type", lang, phone)
        return
    # Process the transcript exactly like a typed message — the assistant replies naturally
    # (in the customer's language). No "I heard…" echo; the reply itself confirms understanding.
    # `lang` is Whisper's detected language — a reliable signal to remember the customer's language.
    logger.info("voice note transcript (%s, lang=%s): %r", phone, lang, text[:120])
    if route_mode == "commerce":
        await _handle_commerce_message(phone, text, msg_id, route_tenant, detected_lang=lang)
    elif route_mode == "knowledge":
        await _handle_message(phone, text, msg_id, route_tenant_id=route_tenant)
    else:
        await _handle_message(phone, text, msg_id)


def _clean_filename(filename: str) -> str:
    """Strip an unrecoverably-mangled filename rather than let it propagate into storage
    metadata/display. Confirmed live (2026-08-27): a WhatsApp-delivered filename with an
    accented character (e.g. "Mipolam Classic - Catálogo.pdf") can arrive already containing a
    literal U+FFFD replacement character — traced as far back as Vula's own webhook handler
    (json.loads on the raw request body, which correctly UTF-8-decodes) with no corrupting step
    found in between, meaning the sender's device or Meta's own backend already lost the
    original byte before Vula ever saw it. Unrecoverable — U+FFFD is by definition a discarded
    byte, not a wrong-codec artifact — so the fix is graceful degradation, not repair: any
    replacement character (or other non-printable control character) anywhere in the name means
    the WHOLE stem is untrustworthy, so it's swapped for a clean generic one. The extension is
    kept as-is (mime-derived extensions are plain ASCII in practice)."""
    if not filename:
        return filename
    if any(ch == "�" or (ord(ch) < 32 and ch not in "\t") for ch in filename):
        stem, dot, ext = filename.rpartition(".")
        return f"document.{ext}" if dot else "document"
    return filename


async def _download_document(media_id: str, tenant_id: str, filename: str, mime_type: str):
    """Download a document from Meta Graph API and save to the upload directory."""
    if not settings.whatsapp_token:
        logger.warning("WHATSAPP_TOKEN not set — cannot download media")
        return None

    filename = _clean_filename(filename)

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


async def _handle_media(phone: str, media_id: str, caption: str, msg_id: str) -> bool:
    """Handle an inbound photo as contractor task evidence.

    Returns True if the sender is a registered contractor (the photo was handled
    as field-ops evidence). Returns False if the sender is NOT a contractor, so
    the caller can fall back (e.g. ingest into the tenant's knowledge base).
    """
    logger.info("WhatsApp media from %s, id=%s", phone, media_id)

    from vula.models.field_ops import get_field_ops_db
    db = get_field_ops_db()
    contractor = db.get_contractor_by_phone(phone)
    if not contractor:
        return False  # not a contractor → let the caller decide (KB ingest, etc.)

    # Even a contractor buys materials + claims. If the photo is a RECEIPT (not site work),
    # route it to the books/expenses instead of logging it as task evidence.
    try:
        probe = await _download_document(media_id, contractor.tenant_id, f"receipt-{msg_id}.jpg", "image/jpeg")
        if probe and await _is_receipt_photo(probe):
            logger.info("contractor photo detected as RECEIPT → expenses (%s)", phone)
            await _handle_document_ingest(
                phone, media_id, caption or f"receipt-{msg_id}.jpg", "image/jpeg",
                route_tenant_id=contractor.tenant_id)
            return True
    except Exception as exc:
        logger.debug("receipt probe skipped: %s", exc)

    # Find the contractor's active task awaiting sign-off or in-progress
    active_tasks = db.get_tasks_for_contractor(
        contractor.id, status="in_progress"
    ) or db.get_tasks_for_contractor(contractor.id, status="awaiting_sign_off")

    tid = contractor.tenant_id  # send field-ops replies from the tenant's number

    if not active_tasks:
        await _send_reply(phone, "Thanks for the photo! I don't see an active task for you right now. Ask your site manager to assign you a task.", tid)
        return True

    task = active_tasks[0]

    # Download media from Meta and save locally
    photo_url = await _download_media(media_id, contractor.id, task.id)

    # Vision: does the photo match the contractor's task for the day?
    task_desc = getattr(task, "description", "") or ""
    assessment = await _assess_evidence_photo(photo_url, task.title, task_desc)

    # Persist the photo + the AI assessment together so it's in the record
    # for progress reporting (caption carries both).
    evidence_caption = caption
    if assessment:
        evidence_caption = f"{caption} | AI: {assessment}".strip(" |")
    db.save_evidence(task.id, contractor.id, photo_url, evidence_caption)

    photo_count = db.count_evidence(task.id)
    db.update_task_status(task.id, "awaiting_sign_off")

    await _send_reply(
        phone,
        f"Photo received ({photo_count} total for '{task.title}'). "
        f"Sent to your site manager for review. Reply DONE when you've submitted all photos.",
        tid,
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
            f"Reply APPROVE or REJECT <reason> to sign off.",
            tid,
        )
    return True


async def _handle_data_deletion(phone: str, tenant_id: Optional[str]) -> None:
    """Handle a DELETE / STOP / opt-out request — POPIA + Meta compliance.

    Removes the requester's data: tenant_phones entry, chat history, and
    flags the request. Confirms back to the user.
    """
    logger.info("Data deletion request from %s (tenant=%s)", phone, tenant_id)
    removed = []

    # Persist a do-not-contact suppression that OUTLIVES the PII deletion, so the
    # number is never broadcast to again even if it later re-messages (POPIA).
    if tenant_id:
        try:
            from vula.api.commerce import record_opt_out
            record_opt_out(tenant_id, phone, source="stop_keyword")
        except Exception as exc:
            logger.debug("opt-out suppression skipped: %s", exc)

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
        "For a full deletion or questions, email hello@vula.co.za.",
        tenant_id,
    )


async def _handle_task_complete(phone: str, tenant_id: str) -> None:
    """Mark the contractor's current in-progress task as awaiting sign-off."""
    from vula.models.field_ops import get_field_ops_db
    db = get_field_ops_db()
    contractor = db.get_contractor_by_phone(phone)
    if not contractor:
        await _send_reply(phone, "I couldn't find your contractor profile. Ask your site manager to register you in Vula.", tenant_id)
        return

    active_tasks = db.get_tasks_for_contractor(contractor.id, status="in_progress")
    if not active_tasks:
        active_tasks = db.get_tasks_for_contractor(contractor.id, status="pending")

    if not active_tasks:
        await _send_reply(phone, "I don't see any active tasks assigned to you. Ask your site manager to check your assignments.", tenant_id)
        return

    task = active_tasks[0]
    db.update_task_status(task.id, "awaiting_sign_off")

    await _send_reply(
        phone,
        f"Task '{task.title}' marked as complete. "
        f"Your site manager has been notified and will sign it off. "
        f"Send photos of the finished work if required.",
        tenant_id,
    )

    # Notify managers
    team = db.get_project_team(task.project_id)
    managers = [m for m in team if m["role"] in ("architect", "site_manager")]
    for manager in managers:
        await _send_reply(
            manager["phone"],
            f"✅ {contractor.name} completed task '{task.title}' (project {task.project_id}).\n"
            f"Reply APPROVE or REJECT <reason>.",
            tenant_id,
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
                "Reply DONE to mark your own work complete.",
                tenant_id,
            )
        else:
            await _send_reply(
                phone,
                "I couldn't find your profile. Ask your site manager to add you to the project in Vula.",
                tenant_id,
            )
        return

    # Find the most recent task awaiting sign-off in a project they manage
    pending = db.get_tasks_awaiting_signoff_for_manager(phone)
    if not pending:
        await _send_reply(
            phone,
            "No tasks are currently awaiting your sign-off. "
            "Check the Vula dashboard for the latest project status.",
            tenant_id,
        )
        return

    task = pending[0]
    task_id        = task["task_id"]
    task_title     = task["title"]
    contractor_phone = task["contractor_phone"]
    contractor_name  = task["contractor_name"]

    db.record_sign_off(task_id, phone, decision, notes)

    # Mirror the sign-off to ClickUp if connected (best-effort).
    try:
        from vula.integrations.clickup_sync import mirror_signoff
        await mirror_signoff(tenant_id, task_id, decision, notes)
    except Exception:
        pass

    if decision == "approved":
        await _send_reply(phone, f"Task '{task_title}' approved ✅. {contractor_name} has been notified.", tenant_id)
        if contractor_phone:
            await _send_reply(
                contractor_phone,
                f"Your work on '{task_title}' has been approved by your site manager. "
                f"Well done! Check the Vula app for your next task.",
                tenant_id,
            )
    else:
        reason = notes or "no reason given"
        await _send_reply(phone, f"Task '{task_title}' rejected. {contractor_name} has been notified.", tenant_id)
        if contractor_phone:
            await _send_reply(
                contractor_phone,
                f"Your work on '{task_title}' needs attention: {reason}. "
                f"Please fix and reply DONE when ready for re-inspection.",
                tenant_id,
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

        # Upload to Supabase Storage so the photo is durable (volume is ephemeral).
        durable_url = _upload_evidence_to_storage(
            dl.content, f"{task_id}/{media_id}.{ext}", content_type
        )
        return durable_url or str(path)

    except Exception as exc:
        logger.error("Media download failed for %s: %s", media_id, exc)
        return f"media://{media_id}"


def _compress_image(data: bytes, content_type: str) -> tuple[bytes, str]:
    """Resize/recompress images before storage to save space (the main storage driver).
    Caps the largest side at 1600px and re-encodes JPEG q80. Best-effort — returns the
    original on any failure or if compression doesn't actually shrink it."""
    if not content_type.startswith("image/") or any(x in content_type for x in ("svg", "gif")):
        return data, content_type
    try:
        from io import BytesIO
        from PIL import Image
        img = Image.open(BytesIO(data))
        MAX = 1600
        if max(img.size) > MAX:
            img.thumbnail((MAX, MAX), Image.LANCZOS)
        out = BytesIO()
        if content_type == "image/png" and img.mode in ("RGBA", "P", "LA"):
            img.save(out, format="PNG", optimize=True)
            blob, ct = out.getvalue(), "image/png"
        else:
            img.convert("RGB").save(out, format="JPEG", quality=80, optimize=True)
            blob, ct = out.getvalue(), "image/jpeg"
        return (blob, ct) if len(blob) < len(data) else (data, content_type)
    except Exception as exc:
        logger.debug("image compress skipped: %s", exc)
        return data, content_type


def _upload_to_storage(bucket: str, object_path: str, data: bytes, content_type: str) -> Optional[str]:
    """Upload bytes to a public Supabase Storage bucket. Returns the public URL,
    or None on failure. Used for evidence photos and filed documents."""
    try:
        data, content_type = _compress_image(data, content_type)
        from vula.models.field_ops import _client
        sb = _client()
        try:
            sb.storage.create_bucket(bucket, options={"public": True})
        except Exception:
            pass  # already exists
        try:
            sb.storage.from_(bucket).upload(
                object_path, data,
                {"content-type": content_type, "upsert": "true"},
            )
        except Exception as up_exc:
            if "exists" not in str(up_exc).lower():
                raise
        return sb.storage.from_(bucket).get_public_url(object_path)
    except Exception as exc:
        logger.warning("Storage upload to '%s' failed (%s)", bucket, exc)
        return None


def _upload_evidence_to_storage(data: bytes, object_path: str, content_type: str) -> Optional[str]:
    """Upload an evidence photo to the 'evidence' bucket. Returns a public URL,
    or None on failure (caller falls back to the local path)."""
    return _upload_to_storage("evidence", object_path, data, content_type)


async def _describe_photo_for_rep(media_id: str) -> str:
    """ONE cloud-vision call transcribing/describing whatever's in a rep's photo (a business
    card, a signboard, a document) — best-effort, empty string on any failure. Distinct from
    _scan_financial_photo (which extracts a strict receipt/invoice JSON shape) — this is a
    free-text description for a rep asking Vula to "research this"/"who is this" from a photo,
    so the caption-routing fix (route the caption to commerce_admin) has real photo content to
    reason from, not just the bare caption text."""
    try:
        data = await _download_media_bytes(media_id)
        if not data:
            return ""
        from core.llm_router import resolve_cloud_vision_route
        route = resolve_cloud_vision_route()
        if not route:
            return ""
        model, api_key, api_base = route
        import base64
        import litellm
        litellm.drop_params = True
        img_b64 = base64.b64encode(data).decode()
        resp = await litellm.acompletion(
            model=model,
            messages=[{"role": "user", "content": [
                {"type": "text", "text": (
                    "Describe this photo in 2-3 short sentences for a sales rep's assistant. "
                    "If it's a business card, document, or sign, transcribe the key text exactly "
                    "as printed (name, company, phone, email, address) — never guess or invent "
                    "anything not actually visible."
                )},
                {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{img_b64}"}},
            ]}],
            temperature=0, max_tokens=250, api_key=api_key, api_base=api_base)
        return (resp.choices[0].message.content or "").strip()
    except Exception as exc:
        logger.debug("photo description failed for rep media_id=%s: %s", media_id, exc)
        return ""


async def _scan_financial_photo(photo_path: str) -> Optional[dict]:
    """ONE strong cloud-vision call on the actual IMAGE → strict JSON for financial documents.
    This is the authoritative reader for photographed receipts/invoices — reading the image
    directly is far more consistent than OCR-text → small local model (which hallucinated
    totals). Returns {doc_type, vendor, date, currency, total, vat} or None."""
    from pathlib import Path as _Path
    try:
        p = _Path(photo_path or "")
        if not p.exists() or p.suffix.lower() not in (".jpg", ".jpeg", ".png", ".webp"):
            return None
        from core.llm_router import resolve_cloud_vision_route
        route = resolve_cloud_vision_route()
        if not route:
            return None
        model, api_key, api_base = route
        import base64
        import json as _json
        import litellm
        litellm.drop_params = True
        img_b64 = base64.b64encode(p.read_bytes()).decode()
        resp = await litellm.acompletion(
            model=model,
            messages=[{"role": "user", "content": [
                {"type": "text", "text":
                    "Read this document image carefully. Return STRICT JSON only, no prose:\n"
                    '{"doc_type":"receipt|invoice|delivery_note|quote|statement|menu|'
                    'payment_confirmation|other",'
                    '"vendor":string|null,"date":"YYYY-MM-DD"|null,'
                    '"currency":"ZAR"|"USD"|"EUR"|"other"|"unknown",'
                    '"total":number|null,"vat":number|null,'
                    '"payment_method":"card"|"cash"|"eft"|"unknown","card_last4":string|null,'
                    '"reference":string|null,"payee":string|null,'
                    '"corners":[[x,y],[x,y],[x,y],[x,y]]|null}\n'
                    "Rules: total = the single final amount PAYABLE printed on the document "
                    "(grand total / amount due) — copy the printed number exactly, do not compute. "
                    "vat = the printed VAT/tax amount, null if not shown. R means ZAR. "
                    "card_last4 = the last 4 card digits if printed (e.g. '**** 1234', 'Card 572'), "
                    "else null. A menu/price list has many prices but no single bill total → "
                    "doc_type=menu, total=null. A delivery note lists goods delivered, often "
                    "without prices. A proof-of-payment / EFT confirmation / bank-app payment "
                    "screenshot (e.g. FNB/Capitec/Absa/PayShap/SnapScan confirming money was "
                    "sent) → doc_type=payment_confirmation, total=the amount paid, "
                    "reference=any transaction/reference number shown, payee=who received the "
                    "payment if shown. corners = the 4 corners of the physical receipt/invoice "
                    "AS PHOTOGRAPHED (only if it's a paper document shot at an angle, not already "
                    "a flat screenshot), clockwise starting top-left — top-left, top-right, "
                    "bottom-right, bottom-left — each as [x,y] FRACTIONS of the image width/height "
                    "(0.0-1.0, not pixels). null if you can't confidently see all 4 corners, if "
                    "it's already a flat screenshot, or if it's not a single rectangular document."},
                {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{img_b64}"}},
            ]}],
            temperature=0, max_tokens=280, api_key=api_key, api_base=api_base)
        raw = (resp.choices[0].message.content or "").strip()
        raw = raw.replace("```json", "").replace("```", "").strip()
        i, j = raw.find("{"), raw.rfind("}")
        if i < 0 or j <= i:
            return None
        data = _json.loads(raw[i:j + 1])
        return data if isinstance(data, dict) else None
    except Exception as exc:
        logger.debug("financial photo scan failed: %s", exc)
        return None


async def _scan_business_card_photo(photo_path: str) -> Optional[dict]:
    """ONE strong cloud-vision call on the actual IMAGE → strict JSON for a business card.
    Same fix as _scan_financial_photo, applied here 2026-08-27 after a real report: the
    OCR-text → cheap-model path (_analyze_document) reliably read an email off a card but
    consistently missed the phone number — small print, varied formatting, icons — while an
    email's distinct @-pattern OCRs cleanly. Reading the image directly avoids that gap.
    Returns {name, company, title, phone, email} or None."""
    from pathlib import Path as _Path
    try:
        p = _Path(photo_path or "")
        if not p.exists() or p.suffix.lower() not in (".jpg", ".jpeg", ".png", ".webp"):
            return None
        from core.llm_router import resolve_cloud_vision_route
        route = resolve_cloud_vision_route()
        if not route:
            return None
        model, api_key, api_base = route
        import base64
        import json as _json
        import litellm
        litellm.drop_params = True
        img_b64 = base64.b64encode(p.read_bytes()).decode()
        resp = await litellm.acompletion(
            model=model,
            messages=[{"role": "user", "content": [
                {"type": "text", "text":
                    "Read this business card image carefully. Return STRICT JSON only, no "
                    'prose:\n{"name":string|null,"company":string|null,"title":string|null,'
                    '"phone":string|null,"email":string|null}\n'
                    "Rules: transcribe the printed phone number and email EXACTLY as shown, "
                    "including country/area codes and symbols. If more than one phone number is "
                    "printed, prefer a mobile/cell number. null for any field genuinely not "
                    "printed on the card — never guess or invent one."},
                {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{img_b64}"}},
            ]}],
            temperature=0, max_tokens=200, api_key=api_key, api_base=api_base)
        raw = (resp.choices[0].message.content or "").strip()
        raw = raw.replace("```json", "").replace("```", "").strip()
        i, j = raw.find("{"), raw.rfind("}")
        if i < 0 or j <= i:
            return None
        data = _json.loads(raw[i:j + 1])
        return data if isinstance(data, dict) else None
    except Exception as exc:
        logger.debug("business card photo scan failed: %s", exc)
        return None


async def _research_missing_contact_details(name: str, company: str) -> dict:
    """Best-effort web search when a business card genuinely doesn't print a phone/email —
    same real "Can you do web search" pattern already proven finding a contractor's real
    contact details (confirmed live, gerflor, 2026-08-26). Never invents a number/address —
    only returns what a real page actually states, and the caller must present it as
    "found via web search, please verify", never as if it came from the card itself."""
    query = " ".join(p for p in (name, company, "contact") if p).strip()
    if not query:
        return {}
    try:
        from core.skills.web_search import _ddg_search, _fetch_text
        hits = await _ddg_search(query, limit=3)
        if not hits:
            return {}
        contexts = []
        for h in hits[:2]:
            text = await _fetch_text(h["url"])
            if text:
                contexts.append(f"[{h['url']}] {h['title']}\n{text[:2000]}")
        if not contexts:
            return {}

        import litellm
        from core.llm_router import resolve_generation_route
        litellm.drop_params = True
        model, api_key, api_base = await resolve_generation_route()
        prompt = (
            "Using ONLY the web page excerpts below, find a phone number and/or email address "
            f"for \"{name or company}\"" + (f" at \"{company}\"" if name and company else "") +
            ". Return STRICT JSON only: {\"phone\": string|null, \"email\": string|null}. "
            "null for anything not actually stated in the excerpts — never guess or invent a "
            "number.\n\n" + "\n\n".join(contexts)
        )
        resp = await litellm.acompletion(
            model=model, messages=[{"role": "user", "content": prompt}],
            temperature=0, max_tokens=150, api_key=api_key, api_base=api_base)
        raw = (resp.choices[0].message.content or "").strip()
        raw = raw.replace("```json", "").replace("```", "").strip()
        import json as _json
        i, j = raw.find("{"), raw.rfind("}")
        if i < 0 or j <= i:
            return {}
        data = _json.loads(raw[i:j + 1])
        return data if isinstance(data, dict) else {}
    except Exception as exc:
        logger.debug("contact research skipped for %s/%s: %s", name, company, exc)
        return {}


async def _scan_site_photo(photo_path: str) -> Optional[dict]:
    """ONE strong cloud-vision call on the actual IMAGE → strict JSON for a building
    exterior/signage photo. Same fix as _scan_business_card_photo — the OCR-text pass reads a
    document held up to the camera fine, but a street-sign/nameplate address in the background
    of a site photo is exactly the small/angled/distant text it's unreliable on.
    Returns {address, business_name, notes} or None."""
    from pathlib import Path as _Path
    try:
        p = _Path(photo_path or "")
        if not p.exists() or p.suffix.lower() not in (".jpg", ".jpeg", ".png", ".webp"):
            return None
        from core.llm_router import resolve_cloud_vision_route
        route = resolve_cloud_vision_route()
        if not route:
            return None
        model, api_key, api_base = route
        import base64
        import json as _json
        import litellm
        litellm.drop_params = True
        img_b64 = base64.b64encode(p.read_bytes()).decode()
        resp = await litellm.acompletion(
            model=model,
            messages=[{"role": "user", "content": [
                {"type": "text", "text":
                    "Look at this photo of a building's exterior. Return STRICT JSON only, no "
                    'prose:\n{"address":string|null,"business_name":string|null,'
                    '"notes":string|null}\n'
                    "Rules: transcribe any street number/address genuinely visible on a sign, "
                    "plaque, or nameplate EXACTLY as shown. business_name is any company/shop "
                    "name visible on signage. notes is one short phrase for anything else "
                    "notable (e.g. \"under construction\", \"security gate\") or null. null for "
                    "any field not actually visible in the photo — never guess or invent an "
                    "address."},
                {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{img_b64}"}},
            ]}],
            temperature=0, max_tokens=200, api_key=api_key, api_base=api_base)
        raw = (resp.choices[0].message.content or "").strip()
        raw = raw.replace("```json", "").replace("```", "").strip()
        i, j = raw.find("{"), raw.rfind("}")
        if i < 0 or j <= i:
            return None
        data = _json.loads(raw[i:j + 1])
        return data if isinstance(data, dict) else None
    except Exception as exc:
        logger.debug("site photo scan failed: %s", exc)
        return None


async def _research_missing_address(business_name: str) -> dict:
    """Best-effort web search when a site photo's signage is legible but no street-sign address
    is visible — same "Can you do web search" pattern already proven for business-card contact
    details. Never invents an address — only returns what a real page actually states, and the
    caller must present it as "found via web search, please verify"."""
    query = f"{business_name} address".strip()
    if not business_name:
        return {}
    try:
        from core.skills.web_search import _ddg_search, _fetch_text
        hits = await _ddg_search(query, limit=3)
        if not hits:
            return {}
        contexts = []
        for h in hits[:2]:
            text = await _fetch_text(h["url"])
            if text:
                contexts.append(f"[{h['url']}] {h['title']}\n{text[:2000]}")
        if not contexts:
            return {}

        import litellm
        from core.llm_router import resolve_generation_route
        litellm.drop_params = True
        model, api_key, api_base = await resolve_generation_route()
        prompt = (
            "Using ONLY the web page excerpts below, find the physical/street address for "
            f"\"{business_name}\". Return STRICT JSON only: {{\"address\": string|null}}. "
            "null if the address isn't actually stated in the excerpts — never guess or invent "
            "one.\n\n" + "\n\n".join(contexts)
        )
        resp = await litellm.acompletion(
            model=model, messages=[{"role": "user", "content": prompt}],
            temperature=0, max_tokens=120, api_key=api_key, api_base=api_base)
        raw = (resp.choices[0].message.content or "").strip()
        raw = raw.replace("```json", "").replace("```", "").strip()
        import json as _json
        i, j = raw.find("{"), raw.rfind("}")
        if i < 0 or j <= i:
            return {}
        data = _json.loads(raw[i:j + 1])
        return data if isinstance(data, dict) else {}
    except Exception as exc:
        logger.debug("address research skipped for %s: %s", business_name, exc)
        return {}


async def _is_receipt_photo(photo_path: str) -> bool:
    """Is this image a financial document (receipt/invoice/delivery note) rather than site work?
    Lets a contractor's material-purchase photo route to the books instead of task evidence."""
    fin = await _scan_financial_photo(photo_path)
    return bool(fin and fin.get("doc_type") in ("receipt", "invoice", "delivery_note", "quote"))


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

async def _rag_reply(tenant_id: str, question: str, conversation_history: str = "", phone: str = "") -> str:
    """Answer a question — routes through the multi-agent runner.

    The agent uses HRM to pick the right skill(s): KB recall, web research
    (SA tenders, company info, live prices), architecture planning, etc.,
    runs them in parallel, and merges. Falls back to plain RAG if the agent
    errors, then to the shared construction KB.
    """
    # Attribute every nested LLM call (skills, research, etc.) to this tenant for COGS.
    try:
        from vula.integrations.metering import set_request_tenant
        set_request_tenant(tenant_id)
    except Exception:
        pass

    # 1. Try the full multi-agent runner (research + memory + all skills)
    try:
        from core.agent_runner import get_agent_runner
        runner = get_agent_runner()
        # customer_phone/session_id — without this, a message that reaches commerce_assistant
        # via this (knowledge-mode) path instead of the dedicated commerce route had no phone
        # to attach a booking to: book_appointment recorded no customer_phone, and
        # cancel_appointment couldn't look the caller up at all (2026-07-27 bookings-via-chat gap).
        metadata = {"customer_phone": phone, "session_id": phone} if phone else {}
        # 2026-08-17: this (knowledge-mode tenant) path never detected a language at all — the
        # only skill with explicit language handling (commerce_assistant.py) is reached via a
        # DIFFERENT dispatch route that knowledge-mode tenants never use, so whichever skill HRM
        # picks here (commerce_admin, reasoning, etc.) got no signal beyond the raw message text
        # and the generic "mirror their language" instruction. behaviour_preamble() now accepts
        # preferred_language centrally (core/skills/base.py) — every skill that passes it through
        # benefits from one fix here, no per-skill detection needed.
        try:
            from core.lang import detect_language
            lang = detect_language(question)
            if lang:
                metadata["preferred_language"] = lang
        except Exception as exc:
            logger.debug("rag_reply language detect skipped: %s", exc)
        result = await runner.run(
            question=question,
            tenant_id=tenant_id,
            conversation_history=conversation_history,
            metadata=metadata,
            max_branches=1,    # cost cap: 1 LLM call per WhatsApp reply
            max_tokens=700,    # room to hold working facts + show code calcs
            top_k=5,           # retrieve enough to surface the right clause/doc
        )
        if result.final_answer and result.final_answer.strip():
            logger.info(
                "Agent answered tenant=%s skill=%s confidence=%.2f",
                tenant_id, result.skill_used, result.confidence,
            )
            try:
                _LAST_CONF.set(float(result.confidence or 0.0))
            except Exception:
                pass
            # 2026-08-18: no production-queryable latency telemetry existed for the WhatsApp
            # chat path at all — confirmed via investigation, the only persistence was a local,
            # per-instance, best-effort SQLite reflection file. Reuses the existing durable
            # sink (vula_reasoning_telemetry, migration 045) already used elsewhere, rather than
            # building new infrastructure — this is what makes "how slow is DIGG's chat really"
            # answerable going forward instead of guessed at.
            try:
                from core.reasoning_telemetry import emit as _emit_rag
                _emit_rag(system="vula-whatsapp-rag", task="rag_reply", tenant_id=tenant_id,
                         outcome="ok", extra={"latency_ms": result.latency_ms,
                                              "confidence": round(float(result.confidence or 0.0), 3),
                                              "skill": result.skill_used})
            except Exception:
                pass
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
            from vula.email_imap.credentials import decrypt_secret
            creds = {
                "token": decrypt_secret(result.data["access_token"]),
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


async def _send_wa_template(tenant_id: str, to: str, template: str, *params: str) -> bool:
    """Send an APPROVED WhatsApp template message. Proactive/scheduled notifications must use a
    template — free text to someone who hasn't messaged recently fails with 'Re-engagement message'
    (confirmed 2026-07-15: this affected every scheduled OTH/DIGG proactive notification, not a
    dev-mode issue). Shared by server.py (OTH schedules) and field_ops.py (DIGG field-ops)."""
    creds = await _get_tenant_wa_creds(tenant_id) if tenant_id else None
    if not creds:
        if settings.whatsapp_token and settings.whatsapp_phone_id:
            creds = {"token": settings.whatsapp_token, "phone_id": settings.whatsapp_phone_id}
        else:
            logger.info("WhatsApp not configured — skipping template send to %s", to)
            return False

    number = to.lstrip("+").replace(" ", "").replace("-", "")
    if number.startswith("0"):
        number = "27" + number[1:]

    body: dict = {"messaging_product": "whatsapp", "to": number, "type": "template",
                  "template": {"name": template, "language": {"code": "en"}}}
    if params:
        body["template"]["components"] = [
            {"type": "body", "parameters": [{"type": "text", "text": str(p)} for p in params]}
        ]
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(
                f"https://graph.facebook.com/v19.0/{creds['phone_id']}/messages",
                headers={"Authorization": f"Bearer {creds['token']}", "Content-Type": "application/json"},
                json=body,
            )
            if not resp.is_success:
                logger.warning("template send failed (%s -> %s): %s", template, to, resp.text[:200])
            elif tenant_id:
                # Stamp last_notified_at for a recognized team member (keep-window-open nudge,
                # migration 112) — single hook point covers every proactive template send across
                # every tenant/call site (server.py's OTH schedules, field_ops.py's DIGG sends,
                # any future one), not just the nudge feature's own sends. Best-effort.
                try:
                    from datetime import datetime, timezone
                    from vula.commerce import service as _cs_stamp
                    _cs_stamp._client().table("vula_team_members").update(
                        {"last_notified_at": datetime.now(timezone.utc).isoformat()}
                    ).eq("tenant_id", tenant_id).eq("whatsapp", number).execute()
                except Exception as exc:
                    logger.debug("last_notified_at stamp skipped (run migration 112?): %s", exc)
            return resp.is_success
    except Exception as exc:
        logger.warning("template send failed (%s -> %s): %s", template, to, exc)
        return False


_MD_LINK_RE = re.compile(r"\[([^\]]*)\]\((https?://[^\s)]+)\)")

# A trailing GENERIC offer of further help. CONVERSATION_RULES has forbidden this since
# 2026-08-08, and it is still being emitted — confirmed 2026-09-01 in a real Gerflor reply that
# closed "Let me know if there's anything else I can help you with." after saving a pricing rule.
# Another instance of a prompt rule not holding on its own, so it gets a deterministic strip.
#
# Deliberately narrow: it matches only the CONTENTLESS form (the giveaway is "anything else" /
# "anything I can help with"). A specific, useful follow-up — "Let me know if you'd like the Hake
# added to your cart" — names a real next step and must survive untouched, because that is a
# genuine question a shop assistant would ask.
_GENERIC_CLOSER_RE = re.compile(
    r"(?:^|(?<=[.!?\n]))\s*"
    r"(?:[-—•*]\s*)?"
    r"(?:but\s+)?(?:please\s+)?"
    r"(?:let me know|just let me know|feel free to (?:ask|reach out)|do let me know|"
    r"is there|was there|anything else|laat my weet|laat weet)"
    r"[^.!?\n]{0,80}?"
    r"(?:anything else|anything more|something else|anything i can help|how else can i help|"
    r"iets anders)"
    r"[^.!?\n]{0,60}[.!?]?\s*$",
    re.IGNORECASE,
)

# The same closer with "anything else" as the OPENER rather than following a lead-in
# ("Saved. Anything else I can help with?"), which the two-part pattern above can't reach.
_GENERIC_CLOSER_BARE_RE = re.compile(
    r"(?:^|(?<=[.!?\n]))\s*(?:[-—•*]\s*)?"
    r"(?:anything else|anything more|something else|iets anders)"
    r"[^.!?\n]{0,60}[.!?]?\s*$",
    re.IGNORECASE,
)


# WhatsApp's own markup is SINGLE-character: *bold*, _italic_, ~strike~. Markdown's doubled
# forms render as literal asterisks/underscores on a phone. Confirmed 2026-09-01 in real traffic:
# 6 of 187 recent replies carried "**...**", e.g. "**New Project: Belladonna**" sent to DIGG
# exactly like that. Headings are markdown-only and have no WhatsApp equivalent, so they become
# bold, which is what a heading is for here.
_MD_BOLD_RE = re.compile(r"\*\*(?!\s)([^*\n]+?)(?<!\s)\*\*")
_MD_ITALIC_RE = re.compile(r"__(?!\s)([^_\n]+?)(?<!\s)__")
_MD_HEADING_RE = re.compile(r"^\s{0,3}#{1,6}\s+(.+?)\s*#*\s*$", re.MULTILINE)


_WA_TEXT_LIMIT = 4096
_WA_SPLIT_AT = 3900   # headroom under Meta's hard cap


def _split_for_whatsapp(text: str, limit: int = _WA_SPLIT_AT) -> list[str]:
    """Break an over-long reply into WhatsApp-sized parts on a natural boundary.

    2026-09-01: the send path did `message[:4096]`, silently truncating mid-sentence with
    nothing telling the customer the rest existed. That was latent only because long replies
    were separately being discarded by the degenerate-output length bug (fixed the same day) —
    with those flowing again, a real 79-invoice list or a full price list would now be cut off.

    Splits on the largest natural boundary that fits: paragraph, then line, then sentence, then
    a hard cut as a last resort. Never returns an empty part.
    """
    text = (text or "").strip()
    if len(text) <= limit:
        return [text] if text else []

    parts: list[str] = []
    rest = text
    while len(rest) > limit:
        window = rest[:limit]
        cut = -1
        for sep in ("\n\n", "\n", ". ", "! ", "? ", " "):
            idx = window.rfind(sep)
            # Require the break to be past the halfway mark, else a stray early newline would
            # produce a one-line message followed by a wall of text.
            if idx > limit // 2:
                cut = idx + (len(sep) if sep != "\n\n" else 0)
                break
        if cut <= 0:
            cut = limit
        chunk = rest[:cut].strip()
        if chunk:
            parts.append(chunk)
        rest = rest[cut:].lstrip()
    if rest.strip():
        parts.append(rest.strip())
    return parts or [text[:limit]]


# A dense block of text is the last obviously bot-like thing about a Vula reply: a real person
# sends the answer, then the follow-up question, as separate messages. Only applied to a reply
# that is genuinely long AND already has paragraph structure — and it NEVER breaks inside a
# paragraph or a list, so a split can't land mid-thought. Capped at 3 so a reply can't turn into
# a burst of notifications.
_NATURAL_SPLIT_MIN_CHARS = 700
_NATURAL_MAX_PARTS = 3
_NATURAL_PART_TARGET = 1200


def _natural_parts(text: str) -> list[str]:
    """Group an already-structured long reply into a few messages, the way a person sends them.

    Returns [text] unchanged when the reply is short, has no paragraph breaks, or would only
    produce one part — so ordinary replies are completely unaffected.
    """
    text = (text or "").strip()
    if len(text) < _NATURAL_SPLIT_MIN_CHARS:
        return [text] if text else []
    blocks = [b.strip() for b in re.split(r"\n\s*\n", text) if b.strip()]
    if len(blocks) < 2:
        return [text]

    parts: list[str] = []
    current = ""
    for b in blocks:
        candidate = f"{current}\n\n{b}" if current else b
        # Start a new message once this one is a reasonable size, but never exceed the cap —
        # the tail is always appended to the last part rather than spawning a fourth.
        if current and len(candidate) > _NATURAL_PART_TARGET and len(parts) < _NATURAL_MAX_PARTS - 1:
            parts.append(current)
            current = b
        else:
            current = candidate
    if current:
        parts.append(current)
    return parts if len(parts) > 1 else [text]


def _whatsapp_markup(text: str) -> str:
    """Convert markdown emphasis to WhatsApp's own, so it renders instead of showing as
    literal punctuation. Deliberately conservative: only doubled markers with non-space
    content are touched, so a bare '**' or an asterisked bullet is left alone."""
    if not text:
        return text
    text = _MD_HEADING_RE.sub(lambda m: f"*{m.group(1).strip()}*", text)
    text = _MD_BOLD_RE.sub(lambda m: f"*{m.group(1)}*", text)
    return _MD_ITALIC_RE.sub(lambda m: f"_{m.group(1)}_", text)


# Meta error codes worth telling a human about, vs ones that are just noise.
# 131047 re-engagement: outside the 24-hour customer service window, only an approved template
#        may be sent — the single likeliest reason a real answer never arrives.
# 131026 undeliverable: the number can't receive WhatsApp (wrong number, not on WhatsApp).
# 131030 not in allowed list: a dev-mode restriction, a configuration problem.
# 131031 account locked / 133x quality issues: the line itself is in trouble.
_SEND_FAILURE_MEANING = {
    "131047": "outside the 24-hour reply window — needs an approved template to reach them",
    "131026": "that number can't receive WhatsApp messages",
    "131030": "that number isn't on the allowed-recipients list for this line",
    "131031": "this WhatsApp account is restricted",
    "131049": "Meta throttled this send for quality reasons",
    "132000": "the message template didn't match its expected shape",
}

# (phone, code) -> monotonic time of last owner notification. A number that has gone dead
# would otherwise generate an alert on every single reply attempt.
_send_failure_notified: dict = {}
_SEND_FAILURE_NOTIFY_COOLDOWN_S = 3600.0


async def _record_send_failure(to: str, tenant_id: str, body: str, exc_text: str) -> None:
    """Make a failed WhatsApp send VISIBLE.

    2026-09-02: a send that failed was logged to Railway and nothing else — no telemetry, no
    human told. 71 call sites send replies and only 4 check the return value, so 67 paths were
    send-and-forget. On the main client-facing channel that means a customer's answer can simply
    never arrive, and the business finds out only if the customer chases.

    Records telemetry always (so the failure rate is measurable at all), and notifies the team
    once per hour per number for failures a human can actually act on — chiefly the 24-hour
    window, where the fix is to reach the customer another way.

    Never raises and never notifies about a failure to notify: this runs inside the send path.
    """
    import re as _re3
    code = ""
    m = _re3.search(r'"code"\s*:\s*(\d+)', body or "")
    if m:
        code = m.group(1)
    meaning = _SEND_FAILURE_MEANING.get(code, "")

    try:
        from core.reasoning_telemetry import emit
        emit(system="vula-wa-send", task="send_reply", outcome="failed",
             tenant_id=tenant_id or None, reason=(meaning or exc_text)[:120],
             extra={"code": code or "unknown"})
    except Exception:
        pass

    if not (tenant_id and meaning):
        return   # nothing a human could usefully do with a transient/unknown failure
    try:
        import time as _time
        key = (to, code)
        now = _time.monotonic()
        last = _send_failure_notified.get(key)
        if last and (now - last) < _SEND_FAILURE_NOTIFY_COOLDOWN_S:
            return
        _send_failure_notified[key] = now
        from vula.integrations.notify import notify_team
        await notify_team(tenant_id, "wa_send_failed", (
            f"⚠️ A reply to {to} didn't go through — {meaning}.\n\n"
            f"They haven't received it. Worth reaching them another way if it was urgent."
        ))
    except Exception as exc:
        logger.debug("send-failure notification skipped: %s", exc)


def _strip_generic_closer(text: str) -> str:
    """Drop a trailing contentless "anything else?" sign-off.

    Never strips the whole message: if removing it would leave nothing, the original is kept —
    a bare "Is there anything else I can help with?" is a real (if weak) reply on its own, and
    sending an empty WhatsApp message would be worse than sending a bland one.
    """
    if not text:
        return text
    stripped = _GENERIC_CLOSER_RE.sub("", text).rstrip()
    stripped = _GENERIC_CLOSER_BARE_RE.sub("", stripped).rstrip()
    return stripped if stripped else text


def _sanitize_outbound(message: str) -> str:
    """FINAL choke-point guard: no matter which code path produced the text — agent loop,
    learned answer, n8n relay, admin agent — the same two fixes apply before anything reaches a
    real customer, rather than trusting every model call to get it right every time.

    1. (2026-09-01) A markdown-style link [text](url) shows as literal, unclickable text in
       WhatsApp, which doesn't render markdown — real complaint, off-the-hook: "the link you
       can't select". Deterministic rewrite to a plain, tappable URL — the prompt rule
       (CONVERSATION_RULES) is the first line of defense, this is the backstop.
    2. (2026-07-17) A message that parses as a raw JSON object must never reach a customer. Raw
       model JSON leaked to a real customer three separate times via three different paths;
       per-path guards keep missing new paths, so the send function itself enforces it. Unwraps
       a human-readable field if present, else replaces with a safe apology, and logs loudly so
       the leaking path can be found.
    """
    text = (message or "").strip()
    text = _MD_LINK_RE.sub(
        lambda m: f"{m.group(1)}: {m.group(2)}" if m.group(1).strip() else m.group(2), text)
    text = _whatsapp_markup(text)
    text = _strip_generic_closer(text)
    import re as _re2
    # Caught by this file's own new test suite: a code-fenced JSON leak (```json\n{...}\n```)
    # starts with a backtick, not "{" — the old startswith("{") check on the raw text meant
    # this class never reached the unwrap logic below at all, despite the fence-stripping regex
    # existing specifically to handle it. Strip fences BEFORE the shape check, not after.
    s = _re2.sub(r"^\s*```(?:json)?|```\s*$", "", text, flags=_re2.IGNORECASE).strip()
    if not s.startswith("{"):
        return text
    import json as _json
    try:
        d = _json.loads(s)
    except Exception:
        return text
    if not isinstance(d, dict):
        return text
    for k in ("message", "reply", "text", "answer"):
        v = d.get(k)
        if isinstance(v, str) and v.strip():
            logger.warning("OUTBOUND JSON LEAK caught at _send_reply — unwrapped %r field: %.120s", k, text)
            return _MD_LINK_RE.sub(
                lambda m: f"{m.group(1)}: {m.group(2)}" if m.group(1).strip() else m.group(2),
                v.strip())
    # A leaked TOOL CALL is genuinely unusable — apologise. But a flat data object is a real
    # answer wearing the wrong clothes, and apologising throws it away.
    # 2026-09-02, reproduced on the real off-the-hook tenant: finance_admin answered
    # "how many unpaid invoices and what's the total?" with {"unpaid_invoices": 0, "total": "R0"}
    # — correct, and the owner received "Sorry — I got a bit tangled up there" instead.
    if not _looks_like_tool_call(d):
        rendered = _render_flat_json(d)
        if rendered:
            logger.warning("OUTBOUND JSON caught at _send_reply — rendered as text: %.160s", text)
            return rendered
    logger.warning("OUTBOUND JSON LEAK caught at _send_reply — suppressed: %.200s", text)
    return "Sorry — I got a bit tangled up there. Could you say that again? 🙏"


def _looks_like_tool_call(d: dict) -> bool:
    """A model emitting an unexecuted tool call, not an answer — nothing to salvage."""
    return "name" in d and ("parameters" in d or "arguments" in d)


def _render_flat_json(d: dict) -> str:
    """Turn a simple {key: scalar} answer object into a readable line.

    Only handles the flat, obviously-presentable case — nested structures, empty objects and
    anything with a non-scalar value fall through to the apology rather than being mangled into
    something that looks like a real answer but isn't.
    """
    if not d or len(d) > 6:
        return ""
    bits = []
    for k, v in d.items():
        if v is None or isinstance(v, (dict, list)):
            return ""
        label = str(k).replace("_", " ").strip()
        label = label[:1].upper() + label[1:]
        bits.append(f"{label}: {v}")
    return ". ".join(bits) + "." if bits else ""


async def _mark_read_and_typing(message_id: str, tenant_id: str = "") -> None:
    """Blue-tick the customer's message and show 'typing…' while we work on the reply.

    2026-09-01, ahead of off-the-hook taking real WhatsApp orders: a customer got total
    silence between sending a message and the full reply landing — no ticks, no typing dots —
    which can be many seconds (voice transcription alone measured 3–10s in real telemetry,
    before the LLM even starts). That dead air is the single most bot-like thing about the
    experience; a real shop assistant visibly reads your message first.

    Endpoint/shape verified against Meta's own docs (developers.facebook.com,
    /docs/whatsapp/cloud-api/typing-indicators): one POST does both. Meta dismisses the
    indicator when we reply or after 25s, whichever comes first — so this is only ever called
    on a path that IS about to reply.

    Best-effort and deliberately silent on failure: a cosmetic nicety must never be able to
    block or break a real order.
    """
    if not message_id:
        return
    creds = await _get_tenant_wa_creds(tenant_id) if tenant_id else None
    if not creds:
        if settings.whatsapp_token and settings.whatsapp_phone_id:
            creds = {"token": settings.whatsapp_token, "phone_id": settings.whatsapp_phone_id}
        else:
            return
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            await client.post(
                f"https://graph.facebook.com/v19.0/{creds['phone_id']}/messages",
                headers={
                    "Authorization": f"Bearer {creds['token']}",
                    "Content-Type": "application/json",
                },
                json={
                    "messaging_product": "whatsapp",
                    "status": "read",
                    "message_id": message_id,
                    "typing_indicator": {"type": "text"},
                },
            )
    except Exception as exc:
        logger.debug("read/typing indicator skipped for %s: %s", message_id, exc)


async def _send_reply(to: str, message: str, tenant_id: str = "") -> bool:
    """
    Send a WhatsApp text message via the Meta Graph API.
    Credentials resolved per-tenant from Supabase, falling back to env vars.
    """
    message = _sanitize_outbound(message)
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

    # Long replies go as several messages rather than one truncated one — see
    # _split_for_whatsapp. Sent in order; a failure part-way stops rather than sending the tail
    # out of context.
    parts = []
    for _p in _natural_parts(message):
        parts.extend(_split_for_whatsapp(_p))
    parts = parts or [message]
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            for i, part in enumerate(parts):
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
                        "text": {"body": part[:_WA_TEXT_LIMIT]},
                    },
                )
                resp.raise_for_status()
            if len(parts) > 1:
                logger.info("WhatsApp reply sent to %s in %d parts (%d chars)",
                            to, len(parts), len(message or ""))
            else:
                logger.info("WhatsApp reply sent to %s", to)
            return True
    except httpx.HTTPStatusError as exc:
        # Surface Meta's actual reason (e.g. #131030 recipient not in allowed list,
        # #131047 re-engagement/24h window) — raise_for_status alone hides the body.
        body = ""
        try:
            body = exc.response.text[:600]
        except Exception:
            pass
        logger.error("WhatsApp reply failed to %s (%s): %s", to, exc, body)
        await _record_send_failure(to, tenant_id, body, str(exc))
        return False
    except Exception as exc:
        logger.error("WhatsApp reply failed to %s: %s", to, exc)
        await _record_send_failure(to, tenant_id, "", str(exc))
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

async def _handle_commerce_message(phone: str, text: str, msg_id: str, tenant_id: str,
                                   detected_lang: Optional[str] = None) -> None:
    """Route inbound messages for commerce tenants (e.g. Off the Hook customers).

    Greetings get the interactive welcome menu. Everything else is handled by the
    commerce_assistant AI skill (tool-calling agent over products/cart/orders,
    grounded with the tenant knowledge base and persisted multi-turn memory).
    n8n remains a last-resort fallback if the skill is unavailable.
    """
    try:
        from vula.integrations.metering import set_request_tenant
        set_request_tenant(tenant_id)
    except Exception:
        pass
    text_lower = text.lower().strip()

    # Escalation answer: if a helper (e.g. Staci) is replying to an open escalation on
    # this tenant's line, their message is the answer — relay + learn, don't treat it as
    # a customer order. Runs on the commerce path too so every tenant is covered.
    if await _maybe_helper_escalation_answer(phone, text):
        return

    # Answering "which project is that receipt for?" → allocate the pending expense claim.
    _alloc = await _maybe_allocate_pending_expense(tenant_id, phone, text)
    if _alloc:
        await _send_reply(phone, _alloc, tenant_id)
        return

    # Answering a bank-review question (team members only — never customers).
    _rev = await _maybe_bank_review_answer(tenant_id, phone, text)
    if _rev:
        await _send_reply(phone, _rev, tenant_id)
        return

    # A team member managing their own notification prefs ("stop follow-up emails").
    from vula.integrations.notify import handle_preference_command
    _pref = handle_preference_command(tenant_id, phone, text)
    if _pref:
        await _send_reply(phone, _pref, tenant_id)
        return

    # Onboarding capture: if this contact is mid opt-in/intro flow, their message is part of
    # it (opt-in → name → delivery address → email), not a normal order. Handle + return.
    try:
        from vula.commerce import onboarding
        onb_reply = await onboarding.handle_capture(tenant_id, phone, text)
    except Exception as exc:
        logger.debug("onboarding capture skipped: %s", exc)
        onb_reply = None
    if onb_reply is not None:
        await _send_reply(phone, onb_reply, tenant_id)
        return

    # Opt-out / data deletion (POPIA) — honoured on commerce lines too, so STOP
    # from a customer who receives broadcasts actually suppresses them.
    if _DELETE_RE.match(text):
        await _handle_data_deletion(phone, tenant_id)
        return

    # Record implied marketing consent on first inbound (POPIA). Best-effort.
    try:
        from vula.api.commerce import record_inbound_consent
        record_inbound_consent(tenant_id, phone)
    except Exception:
        pass

    # Stamp last_message_at for a recognized team member (keep-window-open nudge, migration 112) —
    # ANY reply resets their 24h window, so this is what lets the nudge check (see whatsapp.py's
    # scheduled poller hook) know a member is still reachable via free text and skip nudging them.
    # Best-effort, never blocks the actual reply logic below.
    try:
        from datetime import datetime, timezone
        _digits = "".join(c for c in (phone or "") if c.isdigit())
        _digits = "27" + _digits[1:] if _digits.startswith("0") else _digits
        from vula.commerce import service as _cs_stamp
        _cs_stamp._client().table("vula_team_members").update(
            {"last_message_at": datetime.now(timezone.utc).isoformat()}
        ).eq("tenant_id", tenant_id).eq("whatsapp", _digits).eq("active", True).execute()
    except Exception as exc:
        logger.debug("last_message_at stamp skipped (run migration 112?): %s", exc)

    # Review capture: a bare 1–5 (or stars) right after we asked for a rating
    # becomes a real review — recorded, thanked, done.
    if await _maybe_capture_review(phone, text, tenant_id):
        return

    # Human handoff: if an owner/agent has taken over this conversation from the
    # shared inbox, the bot stays quiet — but we still log the customer's message
    # so the agent sees it in the inbox thread.
    try:
        from vula.commerce import service as commerce_service
        session = await commerce_service.get_or_create_session(
            tenant_id, session_key=phone, channel="whatsapp", customer_phone=phone
        )
        if session.get("paused"):
            await commerce_service.append_message(tenant_id, session["id"], "user", text)
            logger.info("Handoff active for %s (%s) — bot muted, message logged", phone, tenant_id)
            return
    except Exception as exc:
        logger.debug("Handoff check failed (non-fatal): %s", exc)

    # Owner-configurable conversation flow (migration 118) — is this phone already mid-flow?
    # Checked before greeting/menu so a flow reply (e.g. answering "which location?") is never
    # accidentally swallowed by the greeting/supplier-intake keyword checks below.
    try:
        from vula.commerce import flows as commerce_flows
        flow_reply = await commerce_flows.maybe_continue_flow(tenant_id, phone, text)
    except Exception as exc:
        logger.debug("flow continue check failed: %s", exc)
        flow_reply = None
    if flow_reply is not None:
        await _send_reply(phone, flow_reply, tenant_id)
        return

    # 1. Greeting / explicit "menu" request — send the interactive category list. "menu" is its
    # own trigger (not just a greeting) because the bot's own copy tells customers to reply
    # *menu* to see what's fresh — without this, that reply fell through to the AI assistant
    # and lost the interactive list (tap-to-browse) in favour of plain prose.
    greeting_words = {"hi", "hello", "hallo", "hey", "howzit", "good morning", "goeie dag", "menu"}
    if any(text_lower.startswith(w) for w in greeting_words):
        # A not-yet-welcomed owner/staff member greeting the shop gets THEIR welcome, not the
        # customer menu — confirmed live 2026-07-16: Stacy's first "hi" from her new number hit
        # this branch and returned before the owner check further down ever ran.
        if _is_tenant_owner(tenant_id, phone) and await _maybe_welcome_new_owner(tenant_id, phone):
            return
        await _send_commerce_welcome(phone, tenant_id)
        return

    # 2. Supplier Intake — OTH-07 logic. Word-boundary match (not bare substring) — a plain
    # "sell" in text_lower matched inside the Afrikaans "kanselleer" (cancel), wrongly routing
    # a customer's cancellation request to the supplier-intake reply.
    #
    # Direction matters too: "sell"/"supply" are used both by a fisherman offering their catch
    # TO the business ("I can supply you with linefish") and by a customer asking whether the
    # business sells something TO them ("do you sell abalone?", "can you supply me with X?") —
    # confirmed live 2026-07-25, both phrasings from a real customer got the supplier-intake
    # reply instead of reaching the shopping assistant. Exclude the customer-directed phrasing
    # before matching keywords, rather than trying to enumerate every supplier phrasing instead.
    _customer_asking_re = re.compile(
        r"\byou\s+(?:guys\s+)?(?:sell|supply|have|stock)\b|\bsupply\s+(?:me|us)\b|\bsell\s+(?:me|us|to me)\b"
    )
    supplier_keywords = {"supply", "sell", "catch", "supplier", "verskaf", "fish for you"}
    if (not _customer_asking_re.search(text_lower)
            and any(re.search(rf"\b{re.escape(k)}\b", text_lower) for k in supplier_keywords)):
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

    # Owner-configurable conversation flow — does this NEW message match a trigger phrase?
    # Deliberately placed AFTER greeting/menu and supplier-intake so those reserved system
    # behaviours always win — an owner's own trigger phrase can't accidentally shadow "menu".
    # Not gated on _is_tenant_owner: an owner can self-test a flow by texting its trigger phrase.
    try:
        from vula.commerce import flows as commerce_flows
        flow_start_reply = await commerce_flows.maybe_start_flow(tenant_id, phone, text)
    except Exception as exc:
        logger.debug("flow start check failed: %s", exc)
        flow_start_reply = None
    if flow_start_reply is not None:
        await _send_reply(phone, flow_start_reply, tenant_id)
        return

    # Owner/staff → admin agent (run the shop); customers → shopping agent.
    if _is_tenant_owner(tenant_id, phone):
        await _maybe_welcome_new_owner(tenant_id, phone)
        if await _run_commerce_admin(phone, text, tenant_id, detected_lang=detected_lang):
            return
        # Admin agent failed → fall through to the customer assistant.

    handled = await _run_commerce_assistant(phone, text, tenant_id, detected_lang=detected_lang)
    if not handled:
        # Skill unavailable/failed — fall back to n8n, then a holding reply.
        await _forward_to_n8n_commerce(phone, text, msg_id, tenant_id)


_ADMIN_AGENT_ROLES = ("owner", "operations", "staff", "admin", "sales_rep", "manager")


async def _maybe_capture_review(phone: str, text: str, tenant_id: str) -> bool:
    """If this message is a bare 1–5 rating (digits or star emoji) and we recently
    asked this customer to rate a delivered order, record it as a review.
    Returns True when handled (reply already sent)."""
    t = text.strip()
    rating = None
    if re.fullmatch(r"[1-5]", t):
        rating = int(t)
    elif re.fullmatch(r"⭐{1,5}|★{1,5}", t):
        rating = len(t)
    if rating is None:
        return False
    try:
        from vula.commerce import service as commerce_service
        db = commerce_service._client()
        digits = "".join(c for c in phone if c.isdigit())
        alt = "0" + digits[2:] if digits.startswith("27") else digits
        # Most recent delivered order for this phone that was asked but not yet rated.
        rows = (db.table("commerce_orders")
                .select("id,display_id,customer_phone,customer_name,review_requested_at,review_rating")
                .eq("tenant_id", tenant_id).eq("status", "delivered")
                .not_.is_("review_requested_at", "null").is_("review_rating", "null")
                .order("review_requested_at", desc=True).limit(10).execute()).data or []
        order = next((o for o in rows
                      if "".join(c for c in (o.get("customer_phone") or "") if c.isdigit())
                      in (digits, alt)), None)
        if not order:
            return False
        db.table("commerce_reviews").insert({
            "tenant_id": tenant_id, "order_id": order["id"],
            "customer_phone": order.get("customer_phone"),
            "customer_name": order.get("customer_name"),
            "rating": rating, "source": "whatsapp",
        }).execute()
        db.table("commerce_orders").update({"review_rating": rating}).eq("id", order["id"]).execute()
        commerce_service._rating_cache.pop(tenant_id, None)
        thanks = ("Thank you so much for the 5 stars! 🌟 See you next order."
                  if rating == 5 else
                  f"Thanks for the feedback ({rating}/5)."
                  + (" We'd love to make it right — the owner will follow up with you." if rating <= 3 else ""))
        await _send_reply(phone, thanks, tenant_id)
        if rating <= 3:
            try:
                from vula.integrations.notify import notify_team
                await notify_team(tenant_id, "review",
                                  f"⚠️ {order.get('customer_name') or phone} rated order "
                                  f"{order.get('display_id')} {rating}/5 — worth a personal follow-up.")
            except Exception:
                pass
        return True
    except Exception as exc:
        logger.debug("review capture skipped: %s", exc)
        return False


def _is_tenant_owner(tenant_id: str, phone: str) -> bool:
    """Is this phone an owner/staff/rep of the tenant (→ admin agent)?

    DB-driven (vula_team_members), falling back to the static _TENANT_TEAM map in
    vula.api.yoco for backward compat. Was previously reading ONLY the static map,
    which meant any team member added purely via the DB/dashboard (e.g. a sales rep
    added by a manager, never hand-added to yoco.py) was silently invisible here —
    a real gap now that "sales_rep" is a role reps get added under.

    Deliberately NOT yoco._tenant_team() — that helper is scoped to "who gets order
    alerts" (owner/manager/operations, or anyone opted into order notifications) and
    would silently drop a plain "staff"/"admin"/"sales_rep" team member who hasn't
    opted into order notifications, which is a real behaviour regression for this
    admin-agent-access check.
    """

    def _digits(p: str) -> str:
        n = "".join(ch for ch in (p or "") if ch.isdigit())
        return "27" + n[1:] if n.startswith("0") else n

    target = _digits(phone)
    try:
        from vula.commerce import service as commerce_service
        rows = (commerce_service._client().table("vula_team_members")
                .select("whatsapp,role").eq("tenant_id", tenant_id).eq("active", True)
                .execute().data or [])
        if rows:
            return any(_digits(r.get("whatsapp") or "") == target
                       and (r.get("role") or "") in _ADMIN_AGENT_ROLES for r in rows)
    except Exception as exc:
        logger.debug("_is_tenant_owner DB lookup skipped: %s", exc)

    try:
        from vula.api.yoco import _TENANT_TEAM
    except Exception:
        return False
    for _name, team_phone, role in _TENANT_TEAM.get(tenant_id, []):
        if _digits(team_phone) == target and role in _ADMIN_AGENT_ROLES:
            return True
    return False


async def _maybe_welcome_new_owner(tenant_id: str, phone: str) -> bool:
    """First-contact welcome for a recognized owner/staff member — fires automatically the
    moment their WhatsApp number is recognized (e.g. after being added to the team, or after a
    phone number change), so they get a friendly confirmation instead of silently landing
    straight in the admin agent with no acknowledgement. Tenant-generic — works for any team
    member on any tenant, not hardcoded to one person. Returns True only if the welcome was
    actually sent this call (callers use this to suppress the customer menu on a greeting)."""
    try:
        from datetime import datetime, timezone
        from vula.commerce import service as _cs

        digits = "".join(c for c in (phone or "") if c.isdigit())
        digits = "27" + digits[1:] if digits.startswith("0") else digits
        db = _cs._client()
        rows = (db.table("vula_team_members").select("id,name,welcomed_at")
                .eq("tenant_id", tenant_id).eq("whatsapp", digits).eq("active", True)
                .limit(1).execute().data or [])
        if not rows or rows[0].get("welcomed_at"):
            return False

        # Atomic claim — only proceed if welcomed_at is still NULL, so a webhook retry (or the
        # other uvicorn worker) can't send this twice.
        claim = (db.table("vula_team_members")
                 .update({"welcomed_at": datetime.now(timezone.utc).isoformat()})
                 .eq("id", rows[0]["id"]).is_("welcomed_at", "null").execute())
        if not claim.data:
            return False

        first_name = (rows[0].get("name") or "there").split()[0]
        try:
            from vula.commerce import service as _cs2
            inv = await _cs2.get_invoice_settings(tenant_id)
            shop_name = (inv or {}).get("trading_as") or (inv or {}).get("company_name") or tenant_id.replace("-", " ").title()
        except Exception:
            shop_name = tenant_id.replace("-", " ").title()

        # Dashboard link (2026-07-24) — this used to only mention the WhatsApp line itself, never
        # the actual admin dashboard, even though every owner/staff member also has a login there.
        # The dashboard lives on its own "admin." subdomain, NOT a /admin path on the storefront
        # domain (that path is the tenant's own separate legacy system, e.g. off-the-hook's own
        # in-repo /admin — see tenantThemes.js's admin.<storefront-domain> convention). Deliberately
        # doesn't repeat the actual email/password here — those are sent once, separately, via
        # whichever channel that team member's login was actually delivered through; this is just
        # a standing reminder of where to go, safe to resend on every trigger.
        from vula.api.tenants import store_url as _store_url
        import re as _re2
        raw_url = _store_url(tenant_id)
        host = _re2.sub(r"^https?://", "", raw_url).rstrip("/") if raw_url else None
        dash_url = f"https://admin.{host}" if host else None
        dash_line = (f"\n\nYour Vula dashboard is at {dash_url} — log in with the email/password "
                     f"you were given." if dash_url else
                     "\n\nAsk your Vula contact for your dashboard login if you don't have it yet.")

        msg = (f"Hey {first_name}! 🎣 Welcome to Vula — you're all set up as the owner/team "
               f"member for *{shop_name}* here on WhatsApp. This is where your delivery "
               f"briefings, low-stock nudges, order chases and day-to-day questions land from "
               f"now on.{dash_line} No fish were harmed in the sending of this message. 🐟🎉")
        await _send_reply(phone, msg, tenant_id)
        logger.info("Sent owner welcome to %s (%s)", phone, tenant_id)
        return True
    except Exception as exc:
        logger.debug("owner welcome skipped: %s", exc)
        return False


# Nudge fires once a member's window has been quiet this long since our last message to them —
# comfortably inside the 24h cutoff so the nudge itself still lands as free text-equivalent (it's
# a template, so it always lands, but this margin means it's sent well before the cutoff, not
# racing it) while not being trigger-happy on every proactive send.
_NUDGE_AFTER_HOURS = 20


async def check_and_nudge_quiet_team_members() -> int:
    """Keep-the-window-open nudge (migration 112), generalized across every tenant/team member —
    not hardcoded to one person. Any team member we've sent a proactive template to, who hasn't
    replied since, and whose 24h window is now approaching its close, gets a check-in template
    ("anything you need?") — since ANY reply resets the window, this is what lets Vula keep
    reaching them via free text afterward instead of hitting the template wall on every follow-up.

    No separate dedup ledger needed: sending the nudge itself goes through _send_wa_template,
    which stamps last_notified_at to "now" (see that function) — so the next poller pass naturally
    sees a fresh window and won't re-fire until ANOTHER ~20h of silence passes. Self-limiting by
    construction, not by a separate log table.

    Called from the same 5-minute automations poller (server.py's _automations_loop) as everything
    else in this reliability arc — no new asyncio task.
    """
    from datetime import datetime, timezone, timedelta
    from vula.commerce import service as _cs
    db = _cs._client()
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=_NUDGE_AFTER_HOURS)).isoformat()

    try:
        candidates = (db.table("vula_team_members").select("id,tenant_id,name,whatsapp,last_message_at,last_notified_at")
                      .eq("active", True).not_.is_("last_notified_at", "null")
                      .lte("last_notified_at", cutoff).execute().data or [])
    except Exception as exc:
        logger.debug("nudge candidate query skipped (run migration 112?): %s", exc)
        return 0

    sent = 0
    for m in candidates:
        # Already replied since our last message — window is open on its own, no nudge needed.
        if m.get("last_message_at") and m["last_message_at"] > m["last_notified_at"]:
            continue
        tenant_id, whatsapp = m.get("tenant_id"), m.get("whatsapp")
        if not (tenant_id and whatsapp):
            continue
        try:
            from vula.commerce import service as _cs2
            inv = await _cs2.get_invoice_settings(tenant_id)
            shop_name = (inv or {}).get("trading_as") or (inv or {}).get("company_name") or tenant_id.replace("-", " ").title()
        except Exception:
            shop_name = tenant_id.replace("-", " ").title()
        first_name = (m.get("name") or "there").split()[0]
        ok = await _send_wa_template(tenant_id, whatsapp, "vula_owner_checkin_nudge", first_name, shop_name)
        if ok:
            sent += 1
            logger.info("Sent keep-window-open nudge to %s (%s)", whatsapp, tenant_id)
    return sent


async def check_and_nudge_due_reminders() -> int:
    """Send a WhatsApp nudge for every reminder (migration 117) whose due_at has passed and
    hasn't been nudged yet. Free text via _send_reply, not a template — reminders are set by
    someone actively using the assistant, so the 24h window is usually still open; if that
    proves unreliable for far-future reminders, a proper approved template (same pattern as
    check_and_nudge_quiet_team_members's vula_owner_checkin_nudge) is the natural follow-up.
    Called from the same 5-minute automations poller as everything else in this arc."""
    from datetime import datetime, timezone
    from vula.commerce import service as _cs
    db = _cs._client()
    now = datetime.now(timezone.utc).isoformat()

    try:
        due = (db.table("vula_reminders").select("id,tenant_id,created_by,text")
               .eq("status", "open").is_("nudged_at", "null")
               .not_.is_("due_at", "null").lte("due_at", now).execute().data or [])
    except Exception as exc:
        logger.debug("reminder nudge query skipped (run migration 117?): %s", exc)
        return 0

    sent = 0
    for r in due:
        tenant_id, phone, text = r.get("tenant_id"), r.get("created_by"), r.get("text")
        if not (tenant_id and phone and text):
            continue
        ok = await _send_reply(phone, f"⏰ Reminder: {text}", tenant_id)
        if ok:
            sent += 1
            try:
                db.table("vula_reminders").update(
                    {"nudged_at": datetime.now(timezone.utc).isoformat()}
                ).eq("id", r["id"]).execute()
            except Exception as exc:
                logger.debug("marking reminder %s as nudged failed: %s", r.get("id"), exc)
    return sent


async def _sender_is_sales_rep(phone: str, tenant_id: str) -> bool:
    """Is this phone a registered, active sales_rep for this tenant? Shared by both the text-
    message dispatch (_handle_message) and the image-message dispatch (the inbound webhook's
    image/video branch) — factored out after finding the image branch had no sales_rep
    awareness at all, so a rep captioning a photo "log as meeting" fell through to generic
    document filing instead of reaching commerce_admin/log_meeting."""
    try:
        from vula.commerce import service as _commerce_service

        def _digits(p: str) -> str:
            n = "".join(ch for ch in (p or "") if ch.isdigit())
            return "27" + n[1:] if n.startswith("0") else n
        target = _digits(phone)
        rep_rows = (_commerce_service._client().table("vula_team_members")
                   .select("whatsapp").eq("tenant_id", tenant_id).eq("role", "sales_rep")
                   .eq("active", True).execute().data or [])
        return any(_digits(r.get("whatsapp") or "") == target for r in rep_rows)
    except Exception as exc:
        logger.debug("sales_rep routing check skipped: %s", exc)
        return False


async def _run_commerce_admin(phone: str, text: str, tenant_id: str,
                              detected_lang: Optional[str] = None) -> bool:
    """Drive the commerce_admin skill (owner running the shop) with memory."""
    try:
        from core.skills.base import SkillInput
        from core.skills.loader import get_skill
        from vula.commerce import service as commerce_service
    except Exception as exc:  # pragma: no cover — import guard
        logger.warning("commerce_admin unavailable: %s", exc)
        return False

    admin_session_key = f"admin:{phone}"
    history = ""
    session_id: Optional[str] = None
    preferred_language: Optional[str] = None
    try:
        session = await commerce_service.get_or_create_session(
            tenant_id, session_key=admin_session_key, channel="whatsapp", customer_phone=phone
        )
        session_id = session["id"]
        preferred_language = session.get("preferred_language")
        history = commerce_service.format_history(
            await commerce_service.get_recent_messages(tenant_id, session_id, limit=commerce_service.DEFAULT_HISTORY_LIMIT)
        )
    except Exception as exc:
        logger.debug("Admin session/history load failed (non-fatal): %s", exc)

    # A link the person pasted is part of what they're asking about, not decoration.
    # 2026-09-02, real Gerflor transcript: a rep shared a Google Maps link for Danielle's
    # company, then asked "What the company details" — and got Sports Flooring Warehouse's
    # phone and email from the knowledge base, presented as the answer. Confirmed by the rep:
    # "not the same company". Nothing had ever resolved a pasted link, and asked about "the
    # company" the assistant answered from unrelated KB rather than admitting it hadn't looked.
    # Resolving it here means the following turn knows WHICH company is meant — and when the
    # link can't be opened, says so instead of substituting a different business.
    link_context = ""
    try:
        from vula.commerce import shared_link
        urls = shared_link.find_urls(text)[:2]
        if urls:
            lines = []
            for u in urls:
                lines.append(shared_link.describe(await shared_link.resolve_shared_link(u)))
            link_context = "\n\n" + "\n".join(lines)
            logger.info("resolved %d shared link(s) for %s", len(urls), tenant_id)
    except Exception as exc:
        logger.debug("shared-link resolution skipped: %s", exc)

    # 2026-08-17: the owner/staff admin path never tracked a language preference at all —
    # only commerce_assistant.py (the customer path) did, which is exactly backwards, since
    # the owner is the one actually using this path for real business chat. Same pattern.
    try:
        from core.lang import detect_language, normalize_lang
        lang = normalize_lang(detected_lang) or detect_language(text)
        if lang and lang != normalize_lang(preferred_language):
            preferred_language = lang
            await commerce_service.set_session_language(tenant_id, admin_session_key, lang)
        preferred_language = preferred_language or lang
    except Exception as exc:
        logger.debug("admin language preference update skipped: %s", exc)

    # Who is this, specifically? Needed so a company with several team members (e.g. a few
    # sales reps sharing the tenant's WhatsApp number) can be scoped per-caller rather than
    # every recognized admin getting the identical, full owner-level view. Best-effort — an
    # unmatched/failed lookup just leaves the caller unscoped (today's behaviour).
    caller_name, caller_role = None, None
    try:
        def _digits(p: str) -> str:
            n = "".join(ch for ch in (p or "") if ch.isdigit())
            return "27" + n[1:] if n.startswith("0") else n
        target = _digits(phone)
        rows = (commerce_service._client().table("vula_team_members")
                .select("name,whatsapp,role").eq("tenant_id", tenant_id).eq("active", True)
                .execute().data or [])
        match = next((r for r in rows if _digits(r.get("whatsapp") or "") == target), None)
        if match:
            caller_name, caller_role = match.get("name"), match.get("role")
    except Exception as exc:
        logger.debug("caller identity lookup skipped: %s", exc)

    skill = get_skill("commerce_admin")
    output = await skill(
        SkillInput(
            question=text, tenant_id=tenant_id,
            conversation_history=history + link_context,
            metadata={"session_id": admin_session_key, "customer_phone": phone,
                      "caller_name": caller_name, "caller_role": caller_role,
                      "preferred_language": preferred_language},
        )
    )
    if not output.success or not output.answer:
        logger.warning("commerce_admin returned no answer: %s", output.error)
        return False

    if output.confirm_request:
        # Real reply buttons instead of a plain-text "reply yes to confirm" — see
        # core.skills.commerce_admin.ConfirmationRequired / _send_wa_buttons.
        cr = output.confirm_request
        number = _wa_number(phone)
        creds = await _get_tenant_wa_creds(tenant_id)
        sent = False
        if creds:
            sent = await _send_wa_buttons(
                creds, number, cr["summary"],
                [{"id": f"admin_confirm:{cr['id']}", "title": cr.get("confirm_label") or "Confirm"},
                 {"id": f"admin_cancel:{cr['id']}", "title": cr.get("cancel_label") or "Cancel"}],
            )
        if not sent:
            # No creds resolved, or the Graph API call failed — fall back to plain text rather
            # than leaving the owner with nothing at all; the pending row is still there so a
            # typed "yes" still works via the normal free-text path on the next turn.
            await _send_reply(phone, f"{cr['summary']}\n\nReply 'yes' to confirm.", tenant_id)
    else:
        await _send_reply(phone, output.answer, tenant_id)
    if session_id:
        try:
            await commerce_service.append_message(tenant_id, session_id, "user", text)
            await commerce_service.append_message(
                tenant_id, session_id, "assistant", strip_caveat(output.answer))
        except Exception:
            pass
    return True


async def _handle_admin_confirm_reply(phone: str, reply_id: str, tenant_id: str) -> None:
    """A tap on commerce_admin's Confirm/Cancel buttons (see ConfirmationRequired /
    _send_wa_buttons). Re-dispatches the ORIGINAL tool call with confirm=True baked in — no LLM
    involved in interpreting the reply at all, since the whole point is removing that ambiguity.
    """
    from datetime import datetime, timezone
    from vula.commerce import service as commerce_service

    action, _, pending_id = reply_id.partition(":")
    if not pending_id:
        return
    db = commerce_service._client()
    now_iso = datetime.now(timezone.utc).isoformat()

    try:
        # Atomic claim: only a still-pending, unexpired row can be resolved, and the UPDATE
        # itself flips status so a double-tap (or the retry-safe webhook redelivering) can never
        # process the same confirmation twice. Whichever request's UPDATE actually matches a row
        # wins; the other gets .data == [] and does nothing.
        new_status = "cancelled" if action == "admin_cancel" else "confirmed"
        upd = (db.table("commerce_pending_confirmations")
               .update({"status": new_status, "resolved_at": now_iso})
               .eq("id", pending_id).eq("tenant_id", tenant_id).eq("status", "pending")
               .gt("expires_at", now_iso)
               .execute())
        row = (upd.data or [None])[0]
    except Exception as exc:
        logger.warning("pending-confirmation claim failed (run migration 146?): %s", exc)
        row = None

    if not row:
        await _send_reply(phone, "That confirmation has already been handled or has expired — "
                                  "please ask again if you still want to do this.", tenant_id)
        return

    if action == "admin_cancel":
        await _send_reply(phone, "Cancelled — nothing was changed.", tenant_id)
        return

    try:
        from core.skills.commerce_admin import CommerceAdminSkill
        skill = CommerceAdminSkill()
        ctx = {"tenant_id": tenant_id, "phone": phone, "caller_name": None, "caller_role": None}
        confirmed_args = dict(row["tool_args"] or {})
        confirmed_args["confirm"] = True
        result = await skill._dispatch_tool(row["tool_name"], confirmed_args, ctx)
    except Exception as exc:
        logger.warning("admin confirm re-dispatch failed: %s", exc)
        await _send_reply(phone, "Something went wrong applying that — please try again.", tenant_id)
        return

    # Summarise the raw tool result into plain WhatsApp language — same established pattern as
    # commerce_admin._agent_loop's own weak-model safety net, reused here since there's no
    # agent loop turn at all for this step (deliberately — that's the whole fix).
    reply_text = None
    try:
        import json
        import litellm
        from core.llm_router import resolve_generation_route, looks_degenerate
        from core.prompt_safety import fence
        litellm.drop_params = True
        model, api_key, api_base = await resolve_generation_route()
        resp = await litellm.acompletion(
            model=model, temperature=0.2, max_tokens=300, api_key=api_key, api_base=api_base,
            messages=[
                {"role": "system", "content": "Summarise this data for a shop owner in short, "
                                              "plain WhatsApp language. No JSON."},
                {"role": "user", "content": fence('TOOL_RESULT', json.dumps(result, default=str))},
            ])
        reply_text = (resp.choices[0].message.content or "").strip()
        if reply_text and looks_degenerate(reply_text):
            reply_text = None
    except Exception as exc:
        logger.debug("admin confirm result summarise skipped: %s", exc)
    if not reply_text:
        # Deterministic fallback — never worse than silence, and never a garbled reply.
        reply_text = ("Error: " + str(result.get("error"))) if result.get("error") else \
                     ("Done: " + ", ".join(f"{k}: {v}" for k, v in result.items()
                                           if k not in ("verified",) and v not in (None, "", [])))

    await _send_reply(phone, reply_text, tenant_id)
    try:
        session = await commerce_service.get_or_create_session(
            tenant_id, session_key=f"admin:{phone}", channel="whatsapp", customer_phone=phone)
        await commerce_service.append_message(tenant_id, session["id"], "user", f"[tapped {action}]")
        await commerce_service.append_message(
            tenant_id, session["id"], "assistant", strip_caveat(reply_text))
    except Exception:
        pass


async def _handle_native_order(phone: str, order: dict, tenant_id: str) -> None:
    """A customer checked out via WhatsApp's native product catalog cart (built from
    scripts/sync_meta_catalog.py's synced products — see there for the id convention). Seeds a
    REAL Vula cart deterministically from product_retailer_id/quantity — no LLM involved in
    matching products or pricing at all, the same principle behind commerce_admin's confirm
    buttons. Deliberately does NOT trust the webhook's own item_price (add_to_cart always
    re-prices from the live commerce_products row — same "never trust the client" principle
    vula/commerce/service.py's create_order already applies to a client-supplied discount code).
    The existing commerce_assistant text flow picks this cart back up on the customer's very
    next message (get_or_create_cart is keyed by session_id=phone, the same convention
    _run_commerce_assistant already uses) — this only needs to seed the cart and ask for
    delivery details, not reimplement checkout."""
    from vula.commerce import service as commerce_service

    product_items = order.get("product_items") or []
    if not product_items:
        return

    try:
        cart = await commerce_service.get_or_create_cart(tenant_id, phone, phone)
    except Exception as exc:
        logger.warning("native-order cart creation failed: %s", exc)
        return

    added, missing = [], []
    for item in product_items:
        retailer_id = str(item.get("product_retailer_id") or "").strip()
        try:
            quantity = float(item.get("quantity") or 1)
        except (TypeError, ValueError):
            quantity = 1.0
        if not retailer_id:
            continue
        try:
            rows = (commerce_service._client().table("commerce_products")
                    .select("id,name").eq("tenant_id", tenant_id).eq("slug", retailer_id)
                    .limit(1).execute().data or [])
        except Exception as exc:
            logger.debug("native-order product lookup failed for %s: %s", retailer_id, exc)
            rows = []
        if not rows:
            missing.append(retailer_id)
            continue
        try:
            await commerce_service.add_to_cart(tenant_id, cart["id"], rows[0]["id"], quantity)
            qty_label = int(quantity) if quantity == int(quantity) else quantity
            added.append(f"{qty_label}x {rows[0]['name']}")
        except Exception as exc:
            logger.warning("native-order add_to_cart failed for %s: %s", retailer_id, exc)
            missing.append(retailer_id)

    lines = []
    if added:
        lines.append("Got your order: " + ", ".join(added) + ".")
    if missing:
        lines.append(f"Couldn't match {len(missing)} item(s) from the catalog to a current "
                      "product — the team will follow up on those.")
    if not added and not missing:
        return
    note = (order.get("text") or "").strip()
    if note:
        lines.append(f"Note: {note}")
    if added:
        lines.append("What's the delivery address?")
    await _send_reply(phone, "\n".join(lines), tenant_id)


async def _run_commerce_assistant(phone: str, text: str, tenant_id: str,
                                  detected_lang: Optional[str] = None) -> bool:
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
    preferred_language: Optional[str] = None
    try:
        session = await commerce_service.get_or_create_session(
            tenant_id, session_key=phone, channel="whatsapp", customer_phone=phone
        )
        session_id = session["id"]
        preferred_language = session.get("preferred_language")
        history = commerce_service.format_history(
            await commerce_service.get_recent_messages(tenant_id, session_id, limit=commerce_service.DEFAULT_HISTORY_LIMIT)
        )
    except Exception as exc:
        logger.debug("Commerce session/history load failed (non-fatal): %s", exc)

    # Remember the customer's language: Whisper's detection (voice) is trusted; else a light
    # text heuristic. Persist when we learn/change it, and use it to reply in their language.
    try:
        from core.lang import detect_language, normalize_lang
        lang = normalize_lang(detected_lang) or detect_language(text)
        if lang and lang != normalize_lang(preferred_language):
            preferred_language = lang
            await commerce_service.set_session_language(tenant_id, phone, lang)
        preferred_language = preferred_language or lang
    except Exception as exc:
        logger.debug("language preference update skipped: %s", exc)

    skill = get_skill("commerce_assistant")
    output = await skill(
        SkillInput(
            question=text,
            tenant_id=tenant_id,
            conversation_history=history,
            metadata={"session_id": phone, "customer_phone": phone,
                      "preferred_language": preferred_language},
        )
    )

    if not output.success:
        logger.warning("commerce_assistant returned no answer: %s", output.error)
        return False

    # Escalate-and-learn: if the shopping assistant couldn't really answer (e.g. a
    # question outside the catalog — delivery areas, custom orders, opening hours it
    # doesn't know), ask the tenant's human helper on WhatsApp and hold the customer
    # with a friendly note, instead of guessing. Same loop as the knowledge path.
    reply = await _maybe_escalate_and_learn(tenant_id, phone, text, output.answer)

    # 2026-08-14: show the real product photo (when add_to_cart resolved one, see
    # commerce_assistant.py's SkillOutput.media_url) before the text confirmation — reuses the
    # image-send path already proven for the greeting menu header (_send_wa_image). Best-effort:
    # a failed/missing image must never block the actual order confirmation text.
    if output.media_url:
        try:
            creds = await _resolve_wa(tenant_id)
            if creds:
                await _send_wa_image(creds, _wa_number(phone), output.media_url)
        except Exception as exc:
            logger.debug("product photo send skipped: %s", exc)

    await _send_reply(phone, reply, tenant_id)

    # Persist this turn so the next message has context.
    if session_id:
        try:
            await commerce_service.append_message(tenant_id, session_id, "user", text)
            await commerce_service.append_message(
                tenant_id, session_id, "assistant", strip_caveat(reply))
        except Exception as exc:
            logger.debug("Commerce message persistence failed (non-fatal): %s", exc)

    return True


async def _handle_commerce_interactive(
    phone: str, reply_id: str, reply_title: str, msg_id: str, tenant_id: str
) -> None:
    """Handle interactive list/button replies from the WhatsApp product catalog."""
    # Category tap → show that category's products (with prices) as a sub-menu.
    if reply_id.startswith("cat_"):
        category = reply_id[4:]
        sent = await _send_category_products(phone, tenant_id, category)
        if not sent:  # no creds → let the assistant list it as text
            await _handle_commerce_message(phone, f"Show me your {reply_title or category.replace('_', ' ')}", msg_id, tenant_id)
        return
    # Product tap → hand to the commerce assistant to add it / take the order.
    if reply_id.startswith("prod_"):
        await _handle_commerce_message(phone, f"I'd like to order {reply_title}", msg_id, tenant_id)
        return
    await _forward_to_n8n_commerce(phone, reply_id, msg_id, tenant_id, reply_title=reply_title)


# Friendly labels for common category keys (fallback = title-cased key).
_CATEGORY_LABELS = {
    "fresh_fish": "Fresh Fish", "fresh_chicken": "Fresh Chicken",
    "frozen_chicken": "Frozen Chicken", "frozen_seafood": "Frozen Seafood",
    "linefish": "Linefish", "shellfish": "Shellfish & Prawns", "crayfish": "Crayfish",
    "box_deal": "Box Deals", "smoked": "Smoked Fish", "frozen": "Frozen", "extras": "Extras",
    "general": "Our Products",
}


def _fmt_price(cents) -> str:
    try:
        return f"R{int(cents) / 100:.2f}"
    except Exception:
        return ""


def _product_desc(p: dict) -> str:
    parts = [_fmt_price(p.get("price_cents") or 0)]
    if p.get("weight_grams"):
        parts.append(f"{int(p['weight_grams'])}g")
    elif p.get("serves"):
        parts.append(f"serves {p['serves']}")
    base = " · ".join(x for x in parts if x)
    # Fold in the product's own description/notes (e.g. "great for the braai") if it fits
    # within WhatsApp's 72-char row limit — price/weight always wins the space if tight.
    extra = (p.get("description") or p.get("notes") or "").strip()
    if extra:
        room = 72 - len(base) - 3  # " · " separator
        if room >= 10:
            return f"{base} · {extra[:room]}"
    return base[:72]


async def _send_wa_buttons(creds: dict, number: str, body: str, buttons: list) -> bool:
    """Send a real WhatsApp interactive reply-button message (max 3, each `id`/`title` a short
    string). 2026-08-25: backs the confirm/cancel flow for commerce_admin's mutating tools — see
    core.skills.commerce_admin.ConfirmationRequired. A button tap comes back as an exact,
    unambiguous button_reply id (handled in the webhook's msg_type == "interactive" branch),
    replacing the free-text "yes"/"confirm" parsing that produced a real fabricated-success
    incident (2026-08-22/24: the model misread its own tool results and invented an invoice)."""
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(
                f"https://graph.facebook.com/v19.0/{creds['phone_id']}/messages",
                headers={"Authorization": f"Bearer {creds['token']}", "Content-Type": "application/json"},
                json={"messaging_product": "whatsapp", "to": number, "type": "interactive",
                      "interactive": {"type": "button",
                                      "body": {"text": body[:1024]},
                                      "action": {"buttons": [
                                          {"type": "reply", "reply": {"id": b["id"], "title": b["title"][:20]}}
                                          for b in buttons[:3]
                                      ]}}},
            )
            resp.raise_for_status()
            return True
    except Exception as exc:
        logger.error("WA buttons send failed to %s: %s", number, exc)
        return False


async def _send_wa_list(creds: dict, number: str, header: str, body: str,
                        footer: str, button: str, sections: list) -> bool:
    """Send an interactive WhatsApp list (≤10 rows total across sections)."""
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(
                f"https://graph.facebook.com/v19.0/{creds['phone_id']}/messages",
                headers={"Authorization": f"Bearer {creds['token']}", "Content-Type": "application/json"},
                json={"messaging_product": "whatsapp", "to": number, "type": "interactive",
                      "interactive": {"type": "list",
                                      "header": {"type": "text", "text": header[:60]},
                                      "body": {"text": body[:1024]},
                                      "footer": {"text": footer[:60]},
                                      "action": {"button": button[:20], "sections": sections}}},
            )
            resp.raise_for_status()
            return True
    except Exception as exc:
        logger.error("WA list send failed to %s: %s", number, exc)
        return False


async def _send_wa_image(creds: dict, number: str, image_url: str) -> bool:
    """Send a standalone image message. Used as a decorative header immediately before an
    interactive list, since WhatsApp's list type only supports a text header (Meta limit)."""
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(
                f"https://graph.facebook.com/v19.0/{creds['phone_id']}/messages",
                headers={"Authorization": f"Bearer {creds['token']}", "Content-Type": "application/json"},
                json={"messaging_product": "whatsapp", "to": number, "type": "image",
                      "image": {"link": image_url}},
            )
            resp.raise_for_status()
            return True
    except Exception as exc:
        logger.warning("WA header image send failed to %s: %s", number, exc)
        return False


async def _resolve_wa(tenant_id: str) -> Optional[dict]:
    creds = await _get_tenant_wa_creds(tenant_id) if tenant_id else None
    if not creds and settings.whatsapp_token and settings.whatsapp_phone_id:
        creds = {"token": settings.whatsapp_token, "phone_id": settings.whatsapp_phone_id}
    return creds


def _wa_number(phone: str) -> str:
    n = phone.lstrip("+").replace(" ", "").replace("-", "")
    return "27" + n[1:] if n.startswith("0") else n


async def _send_category_products(phone: str, tenant_id: str, category: str) -> bool:
    """Send a sub-menu of in-stock products (with prices) for one category."""
    creds = await _resolve_wa(tenant_id)
    if not creds:
        return False
    try:
        from vula.commerce import service as _cs
        prods = await _cs.list_products(tenant_id, category=category, in_stock_only=True, statuses={"active"})
    except Exception:
        prods = []
    rows = [{"id": f"prod_{p.get('id')}", "title": (p.get("name") or "Item")[:24],
             "description": _product_desc(p)} for p in prods[:10]]
    if not rows:
        await _send_reply(phone, "Nothing in stock there right now — type what you're after and I'll help. 🐟", tenant_id=tenant_id)
        return True
    label = (_CATEGORY_LABELS.get(category) or category.replace("_", " ").title())
    return await _send_wa_list(creds, _wa_number(phone), label, "Tap an item to order, or type a question.",
                               "Off the Hook 🐟", "Choose item", [{"title": label[:24], "rows": rows}])


async def _send_commerce_welcome(phone: str, tenant_id: str) -> None:
    """Send the Off the Hook welcome message with interactive category list."""
    # Use the tenant's LIVE WhatsApp creds (phone_id + token) — same source as _send_reply —
    # not a hardcoded (retired) number. Falls back to env. Failures fall back to a text menu.
    creds = await _get_tenant_wa_creds(tenant_id) if tenant_id else None
    if not creds and settings.whatsapp_token and settings.whatsapp_phone_id:
        creds = {"token": settings.whatsapp_token, "phone_id": settings.whatsapp_phone_id}
    if not creds:
        logger.info("Commerce welcome: no WhatsApp creds for %s", tenant_id)
        return

    number = phone.lstrip("+").replace(" ", "").replace("-", "")
    if number.startswith("0"):
        number = "27" + number[1:]

    # Build the menu from the tenant's LIVE in-stock catalog.
    try:
        from vula.commerce import service as _cs
        prods = await _cs.list_products(tenant_id, in_stock_only=True, statuses={"active"})
    except Exception as exc:
        logger.debug("welcome catalog fetch failed: %s", exc)
        prods = []

    # ⭐ Today's specials — featured / daily-catch items, with prices
    specials = [p for p in prods if p.get("is_daily_catch") or p.get("is_featured")][:4]
    spec_rows = [{"id": f"prod_{p.get('id')}", "title": (p.get("name") or "Item")[:24],
                  "description": _product_desc(p)} for p in specials]

    # Categories — fill the remaining row budget (WhatsApp lists allow ≤10 rows total)
    counts: dict[str, int] = {}
    for p in prods:
        counts[p.get("category") or "general"] = counts.get(p.get("category") or "general", 0) + 1
    cat_rows = []
    for key, n in list(counts.items())[:max(0, 10 - len(spec_rows))]:
        label = (_CATEGORY_LABELS.get(key) or key.replace("_", " ").title())[:24]
        cat_rows.append({"id": f"cat_{key}", "title": label,
                         "description": (f"{n} item{'s' if n != 1 else ''}")[:72]})

    sections = []
    if spec_rows:
        sections.append({"title": "Today's specials", "rows": spec_rows})
    if cat_rows:
        sections.append({"title": "Browse the catch", "rows": cat_rows})

    menu_url = f"{settings.public_base_url.rstrip('/')}/menu/{tenant_id}" if settings.public_base_url else ""

    _text_lines = "\n".join(f"• {r['title']}" for r in (cat_rows or spec_rows)) or \
        "Fresh Fish, Frozen Seafood, Fresh Chicken, Frozen Chicken"
    _text_menu = (
        "Welcome to Off the Hook! 🐟\n\n"
        "Cape Town's freshest daily catch, door to door.\n\n"
        f"What are you looking for?\n{_text_lines}\n\n"
        "Reply with a product name, or visit offthehook.co.za"
        + (f"\n\n📸 See photos of the catch: {menu_url}" if menu_url else "")
    )

    if not sections:
        await _send_reply(phone, _text_menu, tenant_id=tenant_id)
        return

    # Optional hero image, sent as its own message right before the list — WhatsApp's list
    # type has no image-header slot, so this is the closest thing to a "rich menu" it allows.
    try:
        from vula.commerce import service as _cs
        inv_settings = await _cs.get_invoice_settings(tenant_id)
        header_image_url = (inv_settings or {}).get("menu_header_image_url") or ""
    except Exception:
        header_image_url = ""
    if header_image_url:
        await _send_wa_image(creds, number, header_image_url)

    body_text = "Cape Town's freshest catch, door to door.\n\nTap a special or category, or just tell me what you want."
    if menu_url:
        body_text += f"\n\n📸 See photos of the catch: {menu_url}"
    ok = await _send_wa_list(
        creds, number, "Off the Hook 🐟", body_text,
        "Free delivery on orders over R500", "View menu", sections,
    )
    if ok:
        logger.info("Commerce welcome sent to %s (%d specials, %d cats)", phone, len(spec_rows), len(cat_rows))
    else:
        await _send_reply(phone, _text_menu, tenant_id=tenant_id)


async def _forward_to_n8n_commerce(
    phone: str, text: str, msg_id: str, tenant_id: str, reply_title: str = ""
) -> None:
    """Forward message to n8n for AI-powered order processing."""
    n8n_base = getattr(settings, "n8n_webhook_base", None)
    if not n8n_base:
        await _send_reply(phone, "On it! Our team will be in touch shortly. 🐟", tenant_id)
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
        await _send_reply(phone, "Got it! We'll be in touch in a few minutes. 🐟", tenant_id)
