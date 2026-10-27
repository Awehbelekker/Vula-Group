"""
vula/api/whatsapp_connect.py — Meta Embedded Signup OAuth flow.

This is the backend for the "Connect WhatsApp" button in Vula Admin.
Clients click the button → Meta popup → code returned → this endpoint
exchanges code for token → registers Vula webhook → stores credentials.

After this, WhatsApp ordering works automatically for that client.
Ian never needs to touch Meta credentials again.

Endpoints:
    POST /v1/whatsapp/connect           — exchange code for token + register webhook
    GET  /v1/whatsapp/connect/status/{tenant_id}  — connection status
    DELETE /v1/whatsapp/disconnect/{tenant_id}    — disconnect (revoke)
    GET  /v1/whatsapp/accounts          — list all connected tenants (admin)

Setup required (one-time):
    1. Create Vula Facebook App at developers.facebook.com
    2. Add WhatsApp product → request whatsapp_business_management permission
    3. Enable Embedded Signup in the app settings
    4. Set env vars:
       VULA_FB_APP_ID     — your Facebook App ID
       VULA_FB_APP_SECRET — your Facebook App Secret
       VULA_FB_CONFIG_ID  — Embedded Signup configuration ID
"""
from __future__ import annotations

import logging
from typing import Optional

import httpx
from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel

from config import settings

log = logging.getLogger(__name__)
router = APIRouter(tags=["whatsapp-connect"])

GRAPH_BASE = "https://graph.facebook.com/v19.0"


# ── Supabase helper ───────────────────────────────────────────────────────────

def _supabase():
    from supabase import create_client
    return create_client(
        settings.supabase_url,
        settings.supabase_service_role_key or settings.supabase_service_key,
    )


# ── Models ────────────────────────────────────────────────────────────────────

class ConnectRequest(BaseModel):
    tenant_id: str
    code: str                    # Short-lived code from Meta Embedded Signup
    connected_by: Optional[str] = None  # email of the admin who clicked Connect
    # From Embedded Signup's session-info message (sessionInfoVersion 2): the WhatsApp account
    # and number the owner actually picked. Without these the first number found on ANY of the
    # token's businesses was used — the wrong one for an owner with several.
    waba_id: Optional[str] = None
    phone_number_id: Optional[str] = None
    # The owner linked the WhatsApp Business app number they already use (Meta's coexistence,
    # Embedded Signup's WhatsApp Business app onboarding): they keep the app on their phone and
    # Vula answers on the same number (vula/api/whatsapp_coexist.py).
    coexistence: bool = False
    # What the number is for (vula/whatsapp_numbers.py: shop | bookings | assistant | team).
    # None keeps a reconnected number's purpose; a new one gets the business's usual kind.
    purpose: Optional[str] = None
    # Moving a number from Vula's own WhatsApp Business Account to the business's own (Meta moves
    # it when the owner enters it in Embedded Signup; it keeps working throughout, with its
    # display name, quality and limits): the phone_number_id of the number on Vula's account.
    move_phone_number_id: Optional[str] = None


class ConnectResponse(BaseModel):
    tenant_id: str
    phone_number: str
    phone_number_id: str
    waba_id: str
    verified_name: str
    status: str
    webhook_registered: bool
    coexistence: bool = False
    moved: Optional[bool] = None          # set when this connect was a move
    move_note: Optional[str] = None


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.post("/connect", response_model=ConnectResponse)
async def connect_whatsapp(body: ConnectRequest, authorization: str = Header(default="")):
    from vula.api.tenant_auth import check_body_tenant
    await check_body_tenant(body.tenant_id, authorization)
    """
    Exchange the short-lived code from Meta Embedded Signup for a
    long-lived access token, discover the phone number details,
    and register Vula's webhook with Meta — all automatically.
    """
    app_id = settings.vula_fb_app_id
    app_secret = settings.vula_fb_app_secret

    # A move: the number must be this business's and on Vula's own account (no token of its own).
    moving = None
    if body.move_phone_number_id:
        from vula import whatsapp_numbers as nums
        moving = next((r for r in nums.rows_for(body.tenant_id, _supabase())
                       if r.get("phone_number_id") == body.move_phone_number_id
                       and r.get("status") == "connected" and not r.get("access_token")), None)
        if not moving:
            raise HTTPException(status_code=400, detail="Only a number on Vula's WhatsApp account can be "
                                "moved to yours, and this one isn't.")

    if not app_id or not app_secret:
        raise HTTPException(
            status_code=503,
            detail="Vula Facebook App not configured. Set VULA_FB_APP_ID and VULA_FB_APP_SECRET.",
        )

    async with httpx.AsyncClient(timeout=15.0) as client:
        # Step 1 — exchange short-lived code for long-lived token
        token_resp = await client.get(
            f"{GRAPH_BASE}/oauth/access_token",
            params={
                "client_id": app_id,
                "client_secret": app_secret,
                "code": body.code,
            },
        )
        if not token_resp.is_success:
            log.error("Token exchange failed: %s", token_resp.text)
            raise HTTPException(status_code=400, detail=f"Token exchange failed: {token_resp.text}")

        token_data = token_resp.json()
        access_token = token_data.get("access_token")
        if not access_token:
            raise HTTPException(status_code=400, detail="No access token in Meta response")

        auth_headers = {"Authorization": f"Bearer {access_token}"}

        waba_id = None
        phone_number_id = None
        phone_number = None
        verified_name = None

        # Step 2a — the number the owner chose in the signup popup, when the frontend sent it.
        if body.waba_id and body.phone_number_id:
            chosen = await client.get(f"{GRAPH_BASE}/{body.phone_number_id}",
                                      params={"fields": "id,display_phone_number,verified_name"},
                                      headers=auth_headers)
            if chosen.is_success:
                waba_id, phone_number_id = body.waba_id, body.phone_number_id
                phone_number = chosen.json().get("display_phone_number", "")
                verified_name = chosen.json().get("verified_name", "")
            else:
                log.warning("chosen phone %s not readable with this token, discovering instead: %s",
                            body.phone_number_id, chosen.text[:200])

        # Step 2b — otherwise discover WABA and phone numbers for this token
        businesses = []
        if not phone_number_id:
            waba_resp = await client.get(
                f"{GRAPH_BASE}/me/businesses",
                headers=auth_headers,
            )
            businesses = waba_resp.json().get("data", [])

        for biz in businesses:
            wa_resp = await client.get(
                f"{GRAPH_BASE}/{biz['id']}/whatsapp_business_accounts",
                headers=auth_headers,
            )
            for waba in wa_resp.json().get("data", []):
                phones_resp = await client.get(
                    f"{GRAPH_BASE}/{waba['id']}/phone_numbers",
                    params={"fields": "id,display_phone_number,verified_name,status"},
                    headers=auth_headers,
                )
                phones = phones_resp.json().get("data", [])
                if phones:
                    waba_id = waba["id"]
                    phone = phones[0]
                    phone_number_id = phone["id"]
                    phone_number = phone.get("display_phone_number", "")
                    verified_name = phone.get("verified_name", "")
                    break
            if waba_id:
                break

        if not phone_number_id:
            raise HTTPException(
                status_code=400,
                detail="No WhatsApp phone numbers found for this account. "
                       "Please verify the phone number in Meta Business Manager first.",
            )

        # A move must end with the number the owner meant: Meta connects whatever was entered.
        if moving and _digits(phone_number) != _digits(moving.get("phone_number")):
            raise HTTPException(status_code=409, detail=(
                f"Meta connected {phone_number or 'a different number'}, not {moving.get('phone_number')}, "
                f"so nothing was moved. Start again and enter {moving.get('phone_number')} in the Meta window."))

        # Step 2c — register the number for Cloud API messaging (a number added through Embedded
        # Signup can't send until it is). Behind a flag until tested live: registering sets the
        # number's two-step PIN, so it needs WHATSAPP_REGISTRATION_PIN, and fails harmlessly on
        # a number that's already registered with a different PIN.
        # Never for a number shared with the WhatsApp Business app: it's already registered, and
        # registering it for the Cloud API alone would take it off the owner's phone.
        # A moved number is always registered: that is what completes Meta's move (until then
        # it keeps sending from Vula's account).
        registered = None
        if (((settings.whatsapp_register_on_connect or moving) and settings.whatsapp_registration_pin)
                and not body.coexistence):
            reg = await client.post(f"{GRAPH_BASE}/{phone_number_id}/register", headers=auth_headers,
                                    json={"messaging_product": "whatsapp",
                                          "pin": settings.whatsapp_registration_pin})
            registered = reg.is_success
            if reg.is_success:
                log.info("Registered phone %s for Cloud API", phone_number_id)
            else:
                log.warning("Phone registration failed (non-fatal): %s", reg.text[:300])

        # Step 3 — register Vula's webhook with Meta for this WABA
        webhook_registered = False
        webhook_resp = await client.post(
            f"{GRAPH_BASE}/{waba_id}/subscribed_apps",
            headers=auth_headers,
        )
        if webhook_resp.is_success:
            webhook_registered = True
            log.info("Webhook registered for WABA %s", waba_id)
        else:
            log.warning("Webhook registration failed (non-fatal): %s", webhook_resp.text)

    # Step 3b — a shared app number: ask Meta for the app's contacts and the chat history the
    # owner chose to share (Meta only sends them when asked within 24 hours).
    history_sync = None
    if body.coexistence:
        from vula.api.whatsapp_coexist import request_app_sync
        history_sync = await request_app_sync(phone_number_id, access_token)

    # Step 4 — save credentials to Supabase
    from vula.email_imap.credentials import encrypt_secret
    db = _supabase()
    record = {
        "tenant_id": body.tenant_id,
        "waba_id": waba_id,
        "phone_number_id": phone_number_id,
        "phone_number": phone_number,
        "access_token": encrypt_secret(access_token),
        "token_type": "user",
        "verified_name": verified_name,
        "status": "connected",
        "webhook_registered": webhook_registered,
        "connected_by": body.connected_by,
        "connected_at": "now()",
    }
    from vula import whatsapp_numbers as nums
    purpose = nums.purpose_of(moving) if moving else body.purpose
    if moving and moving.get("label"):
        record["label"] = moving["label"]
    save_number(db, body.tenant_id, phone_number_id, record, purpose=purpose,
                extra={"coexistence": body.coexistence, "history_sync": history_sync})
    from vula import trials
    trials.start_on_go_live(body.tenant_id)          # the 14-day trial runs from going live
    moved, move_note = None, None
    if moving:
        old_pnid = moving["phone_number_id"]
        if old_pnid != phone_number_id:
            # Meta gave the moved number a new id: the Vula-account row is retired, and the new one
            # takes over its place as the main number when it was.
            was_main = (nums.main_row(nums.rows_for(body.tenant_id, db)) or {}).get("phone_number_id") == old_pnid
            db.table("vula_whatsapp_accounts").update({"status": "disconnected", "access_token": None}) \
                .eq("tenant_id", body.tenant_id).eq("phone_number_id", old_pnid).execute()
            if was_main:
                try:
                    _make_main(db, body.tenant_id, phone_number_id)
                except Exception as exc:       # before migration 225: one row per business anyway
                    log.info("main number not marked (migration 225?): %s", exc)
            wa_forget = old_pnid
        else:
            wa_forget = None
        moved = registered is True
        if moved:
            move_note = "Moved: the number is on your own Meta account now, and nothing changed for your customers."
        else:
            told = False
            try:
                from vula.integrations.platform_support import alert_operator
                told = await alert_operator(
                    f"WhatsApp move not finished: {moving.get('phone_number')} ({body.tenant_id}) is on their own "
                    f"account (WABA {waba_id}, number id {phone_number_id}) but isn't registered there yet"
                    f"{'' if settings.whatsapp_registration_pin else ' — WHATSAPP_REGISTRATION_PIN is not set'}.")
            except Exception as exc:  # noqa: BLE001
                log.info("move alert not sent: %s", exc)
            move_note = ("The number is on your own Meta account, but Meta hasn't finished switching it over yet. "
                         + ("Vula has been told and will finish it for you. " if told else
                            "Please tell Vula so it can finish it. ")
                         + "Until then it keeps working as before.")
        log.info("WhatsApp number %s moved to %s's own account (waba %s, new id %s, registered %s)",
                 moving.get("phone_number"), body.tenant_id, waba_id, phone_number_id, registered)
        if wa_forget:
            from vula.api import whatsapp as _wa
            _wa._forget_wa_number(body.tenant_id, wa_forget)
    # The new token is used from the next message on, not after a restart.
    from vula.api import whatsapp as wa
    wa._forget_wa_number(body.tenant_id, phone_number_id)
    _forget_health(body.tenant_id)

    log.info(
        "WhatsApp connected: tenant=%s phone=%s waba=%s",
        body.tenant_id, phone_number, waba_id
    )

    # Best-effort: make sure this tenant can reach Ian with platform feedback from day one —
    # never blocks the connect flow if it fails (e.g. Meta template review hiccup).
    try:
        from vula.integrations.platform_support import ensure_template
        tmpl_result = await ensure_template(body.tenant_id)
        log.info("platform-feedback template ensured for %s: %s", body.tenant_id, tmpl_result)
    except Exception as exc:
        log.warning("platform-feedback template provisioning skipped for %s: %s", body.tenant_id, exc)

    return ConnectResponse(
        tenant_id=body.tenant_id,
        phone_number=phone_number or "",
        phone_number_id=phone_number_id,
        waba_id=waba_id,
        verified_name=verified_name or "",
        status="connected",
        webhook_registered=webhook_registered,
        coexistence=body.coexistence,
        moved=moved,
        move_note=move_note,
    )


@router.get("/connect/status/{tenant_id}")
async def whatsapp_status(tenant_id: str):
    """A business's WhatsApp numbers. The top level is its main number, as before (older
    dashboards read only that); `numbers` lists every number with its purpose."""
    from vula import whatsapp_numbers as nums
    rows = nums.rows_for(tenant_id, _supabase())
    if not rows:
        return {"tenant_id": tenant_id, "status": "not_connected", "numbers": []}
    main = nums.main_row(rows)
    head = main or sorted(rows, key=lambda r: str(r.get("connected_at") or ""))[-1]
    keys = ("tenant_id", "phone_number", "phone_number_id", "waba_id", "verified_name", "status",
            "webhook_registered", "connected_at")
    main_pnid = (main or {}).get("phone_number_id")
    listed = [nums.public(r, main_pnid) for r in rows if r.get("status") != "disconnected"]
    listed.sort(key=lambda n: (not n["is_primary"], n["connected_at"] or ""))
    return {**{k: head.get(k) for k in keys}, "tenant_id": tenant_id,
            "coexistence": bool(head.get("coexistence")), "purpose": nums.purpose_of(head),
            "numbers": listed}


@router.get("/accounts")
async def list_whatsapp_accounts():
    """List all connected WhatsApp accounts — Vula Admin overview."""
    db = _supabase()
    result = (
        db.table("vula_whatsapp_accounts")
        .select("tenant_id,company_name,phone_number,status,webhook_registered,connected_at,verified_name")
        .order("connected_at", desc=True)
        .execute()
    )
    return {"accounts": result.data or [], "count": len(result.data or [])}


@router.delete("/disconnect/{tenant_id}")
async def disconnect_whatsapp(tenant_id: str, phone_number_id: Optional[str] = None):
    """Disconnect one of a business's numbers (phone_number_id), or all of them (marks them
    disconnected, keeps the record). Messages stop at once on this worker; within _WA_CREDS_TTL
    on the other (whatsapp._get_tenant_wa_creds). When the main number goes, another customer
    number becomes the main one."""
    from vula import whatsapp_numbers as nums
    db = _supabase()
    rows = nums.rows_for(tenant_id, db)
    q = db.table("vula_whatsapp_accounts").update({"status": "disconnected", "access_token": None}) \
        .eq("tenant_id", tenant_id)
    if phone_number_id:
        if not any(r.get("phone_number_id") == phone_number_id for r in rows):
            raise HTTPException(status_code=404, detail="That number isn't one of this business's.")
        q = q.eq("phone_number_id", phone_number_id)
    q.execute()
    from vula.api import whatsapp as wa
    for r in rows:
        wa._forget_wa_number(tenant_id, r.get("phone_number_id"))
    _forget_health(tenant_id)
    left = [r for r in rows if r.get("status") == "connected" and phone_number_id
            and r.get("phone_number_id") != phone_number_id]
    was_main = (nums.main_row(rows) or {}).get("phone_number_id") == phone_number_id
    if left and was_main:
        _make_main(db, tenant_id, nums.main_row([{**r, "is_primary": False} for r in left])["phone_number_id"])
    return {"tenant_id": tenant_id, "status": "connected" if left else "disconnected",
            "phone_number_id": phone_number_id}


class NumberUpdate(BaseModel):
    purpose: Optional[str] = None
    is_primary: Optional[bool] = None
    label: Optional[str] = None


@router.patch("/numbers/{tenant_id}/{phone_number_id}")
async def update_number(tenant_id: str, phone_number_id: str, body: NumberUpdate):
    """Change what one of a business's numbers is for, its name, or make it the main number.
    Takes effect from the next message (both workers within the route cache's 2 minutes)."""
    from vula import whatsapp_numbers as nums
    db = _supabase()
    rows = nums.rows_for(tenant_id, db)
    row = next((r for r in rows if r.get("phone_number_id") == phone_number_id
                and r.get("status") == "connected"), None)
    if not row:
        raise HTTPException(status_code=404, detail="That number isn't connected to this business.")
    main_pnid = (nums.main_row(rows) or {}).get("phone_number_id")
    becomes_main = body.is_primary is True or main_pnid == phone_number_id
    purpose = body.purpose if body.purpose is not None else nums.purpose_of(row)
    if purpose not in nums.PURPOSES:
        raise HTTPException(status_code=422, detail=f"Purpose must be one of: {', '.join(nums.PURPOSES)}.")
    if purpose == "team" and becomes_main:
        raise HTTPException(status_code=409, detail="Customers get Vula's own messages (order updates, "
                            "reminders) from the main number, so it can't be team-only. Make another "
                            "number the main one first.")
    patch: dict = {}
    if body.purpose is not None:
        patch.update({"purpose": purpose, "route_mode": nums.PURPOSES[purpose]})
    if body.label is not None:
        patch["label"] = body.label.strip()[:60] or None
    try:
        if patch:
            db.table("vula_whatsapp_accounts").update(patch).eq("tenant_id", tenant_id) \
                .eq("phone_number_id", phone_number_id).execute()
        if body.is_primary is True and main_pnid != phone_number_id:
            _make_main(db, tenant_id, phone_number_id)
    except Exception as exc:
        log.warning("number update failed (migration 225 applied?): %s", exc)
        raise HTTPException(status_code=503, detail="Couldn't save that just now — try again in a minute.")
    from vula.api import whatsapp as wa
    wa._forget_wa_number(tenant_id, phone_number_id)
    log.info("WhatsApp number %s of %s updated: %s%s", phone_number_id, tenant_id, patch,
             " (now main)" if body.is_primary else "")
    fresh = nums.rows_for(tenant_id, db)
    new_main = (nums.main_row(fresh) or {}).get("phone_number_id")
    out = next((r for r in fresh if r.get("phone_number_id") == phone_number_id), row)
    return nums.public(out, new_main)


def _make_main(db, tenant_id: str, phone_number_id: str) -> None:
    """At most one main number per business (migration 225's partial unique index): clear the
    business's mark, then set it on this number."""
    db.table("vula_whatsapp_accounts").update({"is_primary": False}).eq("tenant_id", tenant_id).execute()
    db.table("vula_whatsapp_accounts").update({"is_primary": True}).eq("tenant_id", tenant_id) \
        .eq("phone_number_id", phone_number_id).execute()


def _digits(v) -> str:
    return "".join(ch for ch in str(v or "") if ch.isdigit())


def _default_purpose(tenant_id: str) -> str:
    """A new number's purpose when none was chosen: the business's usual kind of line."""
    try:
        from vula.api.tenants import _effective_modules, get_config
        return "shop" if "orders" in _effective_modules(get_config(tenant_id, fresh=True) or {}) else "assistant"
    except Exception:
        return "shop"


def save_number(db, tenant_id: str, phone_number_id: str, record: dict, *, purpose: Optional[str] = None,
                extra: Optional[dict] = None) -> None:
    """Store a connected number as one of the business's numbers (one row per phone_number_id).
    A new number gets `purpose` (else the business's usual kind) and becomes the main number when
    the business has no other connected number; a reconnected one keeps its purpose unless a new
    one is given. Before migration 225 (one number per business) it is stored the old way."""
    from vula import whatsapp_numbers as nums
    rows = nums.rows_for(tenant_id, db)
    existing = next((r for r in rows if r.get("phone_number_id") == phone_number_id), None)
    others = [r for r in rows if r.get("phone_number_id") != phone_number_id]
    if purpose not in nums.PURPOSES:
        purpose = None
    if purpose is None and not existing:
        purpose = _default_purpose(tenant_id)
    if purpose == "team" and not nums.connected(others):
        purpose = _default_purpose(tenant_id)      # a business's only number serves its customers
    full = {**record, **(extra or {})}
    if purpose:
        full.update({"purpose": purpose, "route_mode": nums.PURPOSES[purpose]})
    try:
        db.table("vula_whatsapp_accounts").upsert(full, on_conflict="phone_number_id").execute()
        if not nums.connected(others):
            _make_main(db, tenant_id, phone_number_id)
        return
    except Exception as exc:
        log.warning("number stored the one-per-business way (migration 225 not applied?): %s", exc)
    legacy = {**record, **({"route_mode": full["route_mode"]} if "route_mode" in full else {})}
    try:
        db.table("vula_whatsapp_accounts").upsert({**legacy, **(extra or {})}, on_conflict="tenant_id").execute()
    except Exception as exc:       # migration 223 not applied either
        log.warning("coexistence columns missing (run migration 223): %s", exc)
        db.table("vula_whatsapp_accounts").upsert(legacy, on_conflict="tenant_id").execute()


# ── Number health (2026-10-24, EZContact brief PR 4) ──────────────────────────
# What Meta says about the number: quality, how many new customers a day it may message, whether
# the display name is approved, and whether it's registered to send at all. Read with the
# business's own credentials, cached briefly; the token never leaves the server.

_HEALTH_CACHE: dict = {}
_HEALTH_TTL = 600.0
_HEALTH_FIELDS = ("display_phone_number,verified_name,quality_rating,messaging_limit_tier,"
                  "name_status,status,platform_type,code_verification_status,throughput")
_LIMITS = {"TIER_50": "50", "TIER_250": "250", "TIER_1K": "1,000", "TIER_10K": "10,000",
           "TIER_100K": "100,000", "TIER_UNLIMITED": "unlimited"}


def health_summary(meta: dict) -> dict:
    """Plain-language rows from Meta's phone-number fields. Missing fields are left out."""
    rows = []
    q = (meta.get("quality_rating") or "").upper()
    if q:
        rows.append({"label": "Quality", "value": {"GREEN": "Green — good", "YELLOW": "Yellow — some customers "
                     "blocked or reported recent messages", "RED": "Red — messages may be limited soon"}
                     .get(q, q.title()), "tone": {"GREEN": "ok", "YELLOW": "warn", "RED": "danger"}.get(q, "warn")})
    tier = meta.get("messaging_limit_tier") or ""
    if tier:
        n = _LIMITS.get(tier, tier.replace("TIER_", ""))
        rows.append({"label": "Daily limit", "value": ("No limit on new customers a day" if n == "unlimited"
                     else f"{n} new customers a day"), "tone": "ok"})
    name = (meta.get("name_status") or "").upper()
    if name:
        ok = name in ("APPROVED", "AVAILABLE_WITHOUT_REVIEW")
        rows.append({"label": "Display name", "value": ("Approved" if ok else
                     {"PENDING_REVIEW": "Waiting for Meta's review", "DECLINED": "Declined by Meta — change it in "
                      "WhatsApp Manager", "EXPIRED": "Expired"}.get(name, name.replace("_", " ").title())),
                     "tone": "ok" if ok else "warn"})
    if meta.get("platform_type"):
        reg = meta.get("platform_type") == "CLOUD_API"
        rows.append({"label": "Sending", "value": "Registered to send" if reg else
                     "Not registered to send yet — ask Vula to finish setting up the number",
                     "tone": "ok" if reg else "danger"})
    return {"rows": rows, "number": meta.get("display_phone_number") or "",
            "name": meta.get("verified_name") or "", "status": meta.get("status") or ""}


def _forget_health(tenant_id: str) -> None:
    for key in [k for k in _HEALTH_CACHE if k == tenant_id or (isinstance(k, tuple) and k[0] == tenant_id)]:
        _HEALTH_CACHE.pop(key, None)


@router.get("/health/{tenant_id}")
async def whatsapp_health(tenant_id: str, phone_number_id: Optional[str] = None):
    """What Meta says about one of the business's numbers (phone_number_id), else its main one."""
    import time as _t
    key = (tenant_id, phone_number_id or "")
    hit = _HEALTH_CACHE.get(key)
    if hit and _t.time() - hit[0] < _HEALTH_TTL:
        return hit[1]
    from vula import whatsapp_numbers as nums
    from vula.api import whatsapp as wa
    rows = nums.rows_for(tenant_id, _supabase())
    row = nums.pick(rows, phone_number_id) if (phone_number_id or rows) else None
    if phone_number_id and row and row.get("phone_number_id") != phone_number_id:
        row = None
    creds = await wa._get_tenant_wa_creds(tenant_id, (row or {}).get("phone_number_id"))
    if not creds or not row:
        return {"tenant_id": tenant_id, "connected": False, "rows": []}
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(f"{GRAPH_BASE}/{creds['phone_id']}", params={"fields": _HEALTH_FIELDS},
                                    headers={"Authorization": f"Bearer {creds['token']}"})
        if not resp.is_success:
            log.info("number health read failed for %s: HTTP %s", tenant_id, resp.status_code)
            return {"tenant_id": tenant_id, "connected": True, "rows": [],
                    "error": "Meta didn't answer just now — try again in a minute."}
        out = {"tenant_id": tenant_id, "connected": True, **health_summary(resp.json())}
    except httpx.HTTPError as exc:
        log.info("number health read failed for %s: %s", tenant_id, type(exc).__name__)
        return {"tenant_id": tenant_id, "connected": True, "rows": [],
                "error": "Meta didn't answer just now — try again in a minute."}
    _HEALTH_CACHE[key] = (_t.time(), out)
    return out


# ── A number added straight to Vula's own WhatsApp Business Account ───────────
# 2026-10-13 (SeedleMe): Embedded Signup stalled, so the number was added in WhatsApp Manager on
# Vula's account instead. Such a number needs no token of its own (the server's system-user
# token covers the whole account — whatsapp._get_tenant_wa_creds sends from the row's own
# phone_number_id); it only needs Vula to know which business it belongs to. Master links it
# here: Meta is asked about the number first, so a typo or another company's number is refused.

class LinkNumberError(ValueError):
    pass


async def link_existing_number(tenant_id: str, phone_number_id: str, pin: Optional[str] = None,
                               linked_by: Optional[str] = None, purpose: Optional[str] = None) -> dict:
    """Link a number already on one of Vula's WhatsApp Business Accounts to a tenant. Registers it
    for the Cloud API when Meta says it isn't yet and a two-step PIN is given (the PIN is passed
    to Meta and never stored or logged)."""
    pnid = (phone_number_id or "").strip()
    if not pnid.isdigit():
        raise LinkNumberError("Paste the phone number ID from WhatsApp Manager — digits only.")
    token = settings.whatsapp_token
    if not token:
        raise LinkNumberError("The server has no WhatsApp token configured.")
    db = _supabase()
    rows = db.table("vula_whatsapp_accounts").select("tenant_id,waba_id,phone_number_id").execute().data or []
    other = next((r for r in rows if r.get("phone_number_id") == pnid and r.get("tenant_id") != tenant_id), None)
    if other:
        raise LinkNumberError(f"That number is already linked to '{other['tenant_id']}'.")
    wabas = sorted({r["waba_id"] for r in rows if r.get("waba_id")})
    headers = {"Authorization": f"Bearer {token}"}
    async with httpx.AsyncClient(timeout=20.0) as client:
        found_waba, phone = None, None
        for waba in wabas:
            resp = await client.get(f"{GRAPH_BASE}/{waba}/phone_numbers", headers=headers,
                                    params={"fields": "id,display_phone_number,verified_name,"
                                                      "code_verification_status,name_status,status",
                                            "limit": 100})
            if not resp.is_success:
                continue
            phone = next((p for p in resp.json().get("data", []) if p.get("id") == pnid), None)
            if phone:
                found_waba = waba
                break
        if not phone:
            raise LinkNumberError("That number isn't on Vula's WhatsApp account. Add it in WhatsApp "
                                  "Manager first, then copy its phone number ID.")
        registered = None
        if (phone.get("status") or "").upper() != "CONNECTED" and pin:
            reg = await client.post(f"{GRAPH_BASE}/{pnid}/register", headers=headers,
                                    json={"messaging_product": "whatsapp", "pin": str(pin).strip()})
            registered = reg.is_success
            if not reg.is_success:
                err = ((reg.json() if reg.content else {}).get("error") or {}).get("message") or reg.text[:200]
                raise LinkNumberError(f"Meta refused to register the number: {err}")
    from vula import whatsapp_numbers as nums
    purpose = purpose if purpose in nums.PURPOSES else _default_purpose(tenant_id)
    mode = nums.PURPOSES[purpose]
    from datetime import datetime, timezone
    record = {"tenant_id": tenant_id, "waba_id": found_waba, "phone_number_id": pnid,
              "phone_number": phone.get("display_phone_number") or "",
              "verified_name": phone.get("verified_name") or "", "access_token": None,
              "token_type": "system", "status": "connected", "route_mode": mode,
              "webhook_registered": True, "connected_by": linked_by,
              "connected_at": datetime.now(timezone.utc).isoformat()}
    save_number(db, tenant_id, pnid, record, purpose=purpose)
    from vula import trials
    trials.start_on_go_live(tenant_id)               # the 14-day trial runs from going live
    # Forget anything cached about this number or tenant so the next message routes and replies right.
    from vula.api import whatsapp as wa
    wa._forget_wa_number(tenant_id, pnid)
    log.info("WhatsApp number %s linked to %s (waba %s, mode %s)", pnid, tenant_id, found_waba, mode)
    return {"tenant_id": tenant_id, "phone_number_id": pnid, "waba_id": found_waba,
            "phone_number": record["phone_number"], "verified_name": record["verified_name"],
            "meta_status": phone.get("status"), "name_status": phone.get("name_status"),
            "code_verification_status": phone.get("code_verification_status"),
            "registered_now": registered, "route_mode": mode}
