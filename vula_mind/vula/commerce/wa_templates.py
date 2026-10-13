"""
vula/commerce/wa_templates.py — WhatsApp message template management.

Meta requires an approved TEMPLATE for any business-initiated message sent outside the 24h
customer-service window (confirmed the hard way on 2026-07-15 — see project memory). Before this
module, submitting a template meant hand-writing a one-off script against the Graph API. This is
the reusable path: create + submit, list with LIVE status (never cached — Meta's review can flip
a status at any time), and delete.

Meta's template list is WABA-wide, not tenant-scoped — `commerce_wa_templates` exists purely so
Vula's dashboard can group "which templates belong to which tenant" and remember the body/example
text used to create each one.
"""
from __future__ import annotations

import logging
import re
import uuid
from typing import Any, Dict, List, Optional

import httpx

log = logging.getLogger(__name__)

GRAPH_BASE = "https://graph.facebook.com/v19.0"


def _client():
    from vula.commerce import service
    return service._client()


async def _waba_creds(tenant_id: str) -> Optional[Dict[str, str]]:
    """(waba_id, token) for a tenant — resolved from vula_whatsapp_accounts, the same table
    _get_tenant_wa_creds reads phone_id/token from. Never hardcode a WABA id."""
    try:
        row = (_client().table("vula_whatsapp_accounts")
               .select("waba_id,access_token").eq("tenant_id", tenant_id)
               .eq("status", "connected").limit(1).execute().data or [None])[0]
        if row and row.get("waba_id") and row.get("access_token"):
            from vula.email_imap.credentials import decrypt_secret
            return {"waba_id": row["waba_id"], "token": decrypt_secret(row["access_token"])}
        # A number on Vula's own WhatsApp Business Account has no token of its own; the
        # server's system-user token manages that account's templates (2026-10-13, SeedleMe).
        from config import settings
        if row and row.get("waba_id") and settings.whatsapp_token:
            return {"waba_id": row["waba_id"], "token": settings.whatsapp_token}
    except Exception as exc:
        log.debug("WABA creds lookup failed for %s: %s", tenant_id, exc)
    return None  # no WhatsApp account on record — caller decides how to handle


def _placeholder_count(body_text: str) -> int:
    nums = {int(n) for n in re.findall(r"\{\{(\d+)\}\}", body_text)}
    return max(nums) if nums else 0


async def _upload_media_handle(url: str, token: str) -> tuple[Optional[str], Optional[str]]:
    """Meta's resumable Upload API: download the sample media, upload it to Meta, return the
    'handle' a template's HEADER example needs. Sending a real template message later takes a
    plain image URL directly — this handle is ONLY required at template-creation time, for the
    sample Meta reviews the design against. Returns (handle, error) — exactly one is set."""
    from config import settings
    app_id = settings.vula_fb_app_id
    if not app_id:
        return None, "VULA_FB_APP_ID is not configured on the server."
    try:
        async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
            img = await client.get(url)
            img.raise_for_status()
            content = img.content
            mime = img.headers.get("content-type", "image/jpeg").split(";")[0]

            session = await client.post(
                f"{GRAPH_BASE}/{app_id}/uploads",
                params={"file_length": len(content), "file_type": mime, "access_token": token},
            )
            session.raise_for_status()
            session_id = session.json()["id"]  # "upload:<...>"

            up = await client.post(
                f"{GRAPH_BASE}/{session_id}",
                headers={"Authorization": f"OAuth {token}", "file_offset": "0"},
                content=content,
            )
            up.raise_for_status()
            handle = up.json().get("h")
            if not handle:
                return None, f"Upload succeeded but Meta returned no handle: {up.text[:200]}"
            return handle, None
    except Exception as exc:
        log.warning("media upload failed for %s: %s", url, exc)
        return None, f"Could not fetch/upload that image: {exc}"


def _build_button_components(buttons: List[dict]) -> List[dict]:
    """Up to 2 buttons: URL (with an optional {{1}} dynamic suffix — pairs naturally with click
    tracking, the suffix becomes the trackable short code at send time) or QUICK_REPLY."""
    out = []
    for b in buttons[:2]:
        btype = (b.get("type") or "URL").upper()
        text = (b.get("text") or "").strip()[:25]
        if not text:
            continue
        if btype == "URL":
            burl = (b.get("url") or "").strip()
            if not burl:
                continue
            entry = {"type": "URL", "text": text, "url": burl}
            if "{{1}}" in burl:
                entry["example"] = [burl.replace("{{1}}", "sample")]
            out.append(entry)
        elif btype == "QUICK_REPLY":
            out.append({"type": "QUICK_REPLY", "text": text})
    return out


async def create_template(
    tenant_id: str, name: str, body_text: str, *,
    category: str = "UTILITY", language: str = "en",
    example_params: Optional[List[str]] = None, created_by: Optional[str] = None,
    header_type: Optional[str] = None, header_example_url: Optional[str] = None,
    buttons: Optional[List[dict]] = None,
) -> Dict[str, Any]:
    """Submit a new template to Meta for review, and record it locally for tenant grouping.
    `name` must be unique per WABA (Meta convention: lowercase_snake_case). Returns Meta's
    response (id + status=PENDING) merged with the local record, or {"error": ...}.

    `header_type` ('IMAGE'|'VIDEO'|'DOCUMENT') + `header_example_url` add a rich media header —
    Meta needs a sample to review the design against (uploaded via the Upload API), but the real
    image sent later can be any URL matching the same format. `buttons` (max 2, URL or
    QUICK_REPLY) add tappable actions below the body."""
    name = re.sub(r"[^a-z0-9_]", "_", name.strip().lower())
    if not name or not body_text.strip():
        return {"error": "name and body_text are required"}

    creds = await _waba_creds(tenant_id)
    if not creds:
        return {"error": "No connected WhatsApp Business Account (WABA) found for this tenant."}

    n_params = _placeholder_count(body_text)
    examples = (example_params or [])[:n_params] or [f"example{i+1}" for i in range(n_params)]

    components: List[dict] = []

    if header_type and header_example_url:
        handle, upload_err = await _upload_media_handle(header_example_url, creds["token"])
        if not handle:
            return {"error": upload_err or "Could not upload the header sample image."}
        components.append({"type": "HEADER", "format": header_type.upper(),
                           "example": {"header_handle": [handle]}})

    body_component: dict = {"type": "BODY", "text": body_text}
    if n_params:
        body_component["example"] = {"body_text": [examples]}
    components.append(body_component)

    if buttons:
        btn_components = _build_button_components(buttons)
        if btn_components:
            components.append({"type": "BUTTONS", "buttons": btn_components})

    payload = {"name": name, "language": language, "category": category.upper(),
               "components": components}
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(
                f"{GRAPH_BASE}/{creds['waba_id']}/message_templates",
                headers={"Authorization": f"Bearer {creds['token']}", "Content-Type": "application/json"},
                json=payload,
            )
            data = resp.json()
    except Exception as exc:
        log.warning("template create failed for %s/%s: %s", tenant_id, name, exc)
        return {"error": str(exc)}

    if not resp.is_success:
        err = (data.get("error") or {}).get("message") or resp.text[:300]
        return {"error": err}

    try:
        _client().table("commerce_wa_templates").insert({
            "id": str(uuid.uuid4()), "tenant_id": tenant_id, "name": name,
            "category": category.upper(), "language": language, "body_text": body_text,
            "param_count": n_params, "example": examples, "created_by": created_by,
            "header_type": header_type.upper() if header_type else None,
            "header_example_url": header_example_url, "buttons": buttons or None,
        }).execute()
    except Exception as exc:
        log.debug("wa_templates local insert retrying without rich-media cols (run migration 065?): %s", exc)
        try:
            _client().table("commerce_wa_templates").insert({
                "id": str(uuid.uuid4()), "tenant_id": tenant_id, "name": name,
                "category": category.upper(), "language": language, "body_text": body_text,
                "param_count": n_params, "example": examples, "created_by": created_by,
            }).execute()
        except Exception as exc2:
            log.debug("wa_templates local insert skipped (run migration 063?): %s", exc2)

    return {"name": name, "meta_id": data.get("id"), "status": data.get("status", "PENDING"),
            "category": category.upper()}


async def list_templates(tenant_id: str) -> List[Dict[str, Any]]:
    """Templates created for this tenant, with LIVE status from Meta (never cached — status can
    flip from PENDING to APPROVED/REJECTED at any time on Meta's side)."""
    try:
        local = (_client().table("commerce_wa_templates").select("*")
                 .eq("tenant_id", tenant_id).order("created_at", desc=True).execute().data or [])
    except Exception as exc:
        log.debug("wa_templates local list skipped: %s", exc)
        local = []
    if not local:
        return []

    creds = await _waba_creds(tenant_id)
    live_status: Dict[str, dict] = {}
    if creds:
        try:
            async with httpx.AsyncClient(timeout=20.0) as client:
                resp = await client.get(
                    f"{GRAPH_BASE}/{creds['waba_id']}/message_templates",
                    headers={"Authorization": f"Bearer {creds['token']}"},
                    params={"fields": "name,status,category,rejected_reason", "limit": 200},
                )
                if resp.is_success:
                    for t in resp.json().get("data", []):
                        live_status[t["name"]] = t
        except Exception as exc:
            log.debug("live template status fetch failed: %s", exc)

    out = []
    for row in local:
        live = live_status.get(row["name"], {})
        out.append({
            **row,
            "status": live.get("status", "UNKNOWN"),
            "rejected_reason": live.get("rejected_reason"),
        })
    return out


async def delete_template(tenant_id: str, name: str) -> Dict[str, Any]:
    """Delete a template from Meta and the local registry."""
    creds = await _waba_creds(tenant_id)
    if not creds:
        return {"error": "No connected WABA for this tenant."}
    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            resp = await client.delete(
                f"{GRAPH_BASE}/{creds['waba_id']}/message_templates",
                headers={"Authorization": f"Bearer {creds['token']}"},
                params={"name": name},
            )
    except Exception as exc:
        return {"error": str(exc)}
    if not resp.is_success:
        data = {}
        try:
            data = resp.json()
        except Exception:
            pass
        return {"error": (data.get("error") or {}).get("message") or resp.text[:300]}
    try:
        _client().table("commerce_wa_templates").delete().eq("tenant_id", tenant_id).eq("name", name).execute()
    except Exception:
        pass
    return {"deleted": name}


# ── The standard set a new business needs (2026-10-12, onboarding SeedleMe) ───────────────────
# Every proactive send Vula makes goes out as a template; the scheduled jobs look theirs up as
# job_config.template_name(tenant, suffix) — "seedleme_unpaid_order_chase" — so a new shop had
# nothing approved until someone hand-ran scripts/create_launch_templates.py with OTH's names.
# Wording and {{n}} counts match the call sites in vula/api/server.py; Meta rejects a body that
# starts or ends with a variable. (suffix, body, category, which modules need it — None = all.)
STANDARD_JOB_TEMPLATES = [
    ("unpaid_order_chase",      # server.py::_chase_unpaid_orders · name, order id, amount
     "Hi {{1}}, your order {{2}} for {{3}} is still awaiting payment. "
     "Reply here and we'll send you the payment details again.", "UTILITY", {"orders"}),
    ("sales_summary",           # server.py::_send_oth_sales_summary · date, orders, paid, revenue
     "Sales summary for {{1}}: {{2}} orders, {{3}} paid, R{{4}} in revenue. "
     "Open Vula for the breakdown.", "UTILITY", {"orders"}),
    ("delivery_briefing",       # server.py::_send_oth_delivery_briefing · total, date, paid, to collect
     "Delivery briefing for {{2}}: {{1}} orders, {{3}} paid, {{4}} to collect on delivery. "
     "Open Vula for the full list.", "UTILITY", {"orders"}),
    ("low_stock_alert",         # server.py::_send_low_stock_alert · count
     "Stock check: {{1}} product(s) are low on stock. Open Vula to review and reorder.",
     "UTILITY", {"products"}),
    ("pending_project_nudge",   # server.py::_send_pending_project_nudge · count
     "You have {{1}} filed document(s) waiting to be assigned to a project in Vula.",
     "UTILITY", {"projects"}),
]
# Shared by every business on Vula's WhatsApp account — submitted once, reused by name.
SHARED_TEMPLATES = [
    ("vula_notification", "You have a new update from {{1}}. Open Vula to see the details.", "UTILITY"),
    ("vula_owner_checkin_nudge",
     "Hi {{1}}, a few updates for {{2}} are waiting in Vula. Reply here to keep the chat open, "
     "or open the dashboard when you have a moment.", "UTILITY"),
    ("supplier_po_notice",
     "New purchase order {{1}} — {{2}} item(s), total {{3}}. Reply here to confirm or query it.",
     "UTILITY"),
]


def standard_templates(tenant_id: str, modules: Optional[set] = None,
                       business_type: str = "") -> List[Dict[str, str]]:
    """The templates this business needs, named the way its jobs will look them up."""
    from vula.commerce.job_config import _ONLY_FOR_TYPES, template_name
    mods = set(modules or [])
    out = []
    for suffix, body, category, needs in STANDARD_JOB_TEMPLATES:
        types = _ONLY_FOR_TYPES.get(suffix)
        if (needs and not (needs & mods)) or (types and business_type not in types):
            continue
        out.append({"name": template_name(tenant_id, suffix), "body": body, "category": category})
    out += [{"name": n, "body": b, "category": c} for n, b, c in SHARED_TEMPLATES]
    return out


async def _waba_template_names(creds: Dict[str, str]) -> Optional[set]:
    """Every template name already on the WABA (any tenant's), or None if Meta can't be read."""
    names: set = set()
    url = f"{GRAPH_BASE}/{creds['waba_id']}/message_templates"
    params: Optional[dict] = {"fields": "name", "limit": 200}
    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            while url:
                resp = await client.get(url, headers={"Authorization": f"Bearer {creds['token']}"},
                                        params=params)
                if not resp.is_success:
                    return None
                data = resp.json()
                names |= {t.get("name") for t in data.get("data", []) if t.get("name")}
                url, params = (data.get("paging") or {}).get("next"), None
    except Exception as exc:
        log.debug("template name listing failed: %s", exc)
        return None
    return names


async def submit_standard(tenant_id: str) -> Dict[str, Any]:
    """Submit the standard set for this business — only names not already on its WhatsApp
    account, so running it twice (or for a second business) never duplicates or fails."""
    creds = await _waba_creds(tenant_id)
    if not creds:
        return {"error": "Connect this business's WhatsApp number first (Settings › WhatsApp)."}
    existing = await _waba_template_names(creds)
    if existing is None:
        return {"error": "Couldn't read the templates already on the WhatsApp account — try again."}
    try:
        from vula.api.tenants import _effective_modules, get_config
        cfg = get_config(tenant_id) or {}
        mods, btype = set(_effective_modules(cfg)), cfg.get("business_type") or ""
    except Exception:
        mods, btype = set(), ""
    submitted, already, failed = [], [], []
    for t in standard_templates(tenant_id, mods, btype):
        if t["name"] in existing:
            already.append(t["name"])
            continue
        res = await create_template(tenant_id, t["name"], t["body"], category=t["category"],
                                    created_by="standard set")
        if res.get("error"):
            failed.append({"name": t["name"], "error": res["error"]})
        else:
            submitted.append(t["name"])
    return {"submitted": submitted, "already_there": already, "failed": failed}
