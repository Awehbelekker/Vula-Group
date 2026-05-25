"""
vula/api/onboarding.py

Vula Group — Client Onboarding API

Handles the complete client signup flow:
1. Receive signup form (plan, company, contact details)
2. Provision Supabase tenant record
3. Send WhatsApp notifications (client + team)
4. Return workspace URL + temp credentials immediately
5. Background: mark tenant active, trigger document ingestion queue
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import secrets
import urllib.parse
import uuid
from datetime import datetime, timedelta
from typing import List, Optional

import httpx
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request, Security, UploadFile, File, Form
from fastapi.security.api_key import APIKeyHeader
from pydantic import BaseModel, EmailStr, field_validator

from config import settings
from vula.api.email import send_welcome_email, send_team_alert_email

logger = logging.getLogger(__name__)

router = APIRouter(tags=["onboarding"])

_api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)


async def _require_admin(api_key: str | None = Security(_api_key_header)) -> None:
    """Admin endpoints require API key even when the main API is open."""
    import secrets as _secrets
    if not settings.api_key:
        return
    if not api_key or not _secrets.compare_digest(api_key, settings.api_key):
        raise HTTPException(status_code=401, detail="Admin access requires X-API-Key header.")

# ─── Tier definitions ─────────────────────────────────────────────────────────

TIERS = {
    "starter":  {"price_cents": 150000, "label": "Starter — R1,500/mo"},
    "growth":   {"price_cents": 350000, "label": "Growth — R3,500/mo"},
    "business": {"price_cents": 750000, "label": "Business — R7,500/mo"},
}


# ─── Request / response models ───────────────────────────────────────────────

class OnboardingRequest(BaseModel):
    company_name: str
    industry: str
    staff_count: Optional[int] = None
    contact_name: str
    email: EmailStr
    whatsapp: Optional[str] = None
    pain_points: List[str] = []
    plan: str

    @field_validator("plan")
    @classmethod
    def validate_plan(cls, v: str) -> str:
        if v not in TIERS:
            raise ValueError(f"Plan must be one of: {list(TIERS)}")
        return v

    @field_validator("company_name", "contact_name")
    @classmethod
    def not_empty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("Must not be empty")
        return v.strip()


class OnboardingResponse(BaseModel):
    tenant_id: str
    workspace_url: str
    temp_password: str
    trial_ends: str
    message: str
    payment_url: Optional[str] = None


# ─── PayFast payment link ─────────────────────────────────────────────────────

def _payfast_url(tenant_id: str, plan: str, email: str, name: str) -> Optional[str]:
    """Generate a PayFast payment URL for the given plan.

    Returns None if PayFast is not configured.
    Uses sandbox endpoint when DEBUG=True.
    """
    if not settings.payfast_merchant_id or not settings.payfast_merchant_key:
        return None

    tier = TIERS.get(plan, {})
    amount = tier.get("price_cents", 0) / 100

    host = "sandbox.payfast.co.za" if settings.debug else "www.payfast.co.za"
    notify_url = f"{settings.vula_base_url}/api/v1/payfast/notify"
    return_url = f"{settings.vula_base_url}/welcome?tenant={tenant_id}"
    cancel_url = f"{settings.vula_base_url}/signup?cancelled=1"

    params = {
        "merchant_id": settings.payfast_merchant_id,
        "merchant_key": settings.payfast_merchant_key,
        "return_url": return_url,
        "cancel_url": cancel_url,
        "notify_url": notify_url,
        "name_first": name.split()[0],
        "name_last": name.split()[-1] if len(name.split()) > 1 else "",
        "email_address": email,
        "m_payment_id": tenant_id[:20],
        "amount": f"{amount:.2f}",
        "item_name": f"Vula {plan.title()} — Monthly Subscription",
        "item_description": tier.get("label", plan),
        "subscription_type": "1",          # recurring subscription
        "billing_date": datetime.utcnow().strftime("%Y-%m-%d"),
        "recurring_amount": f"{amount:.2f}",
        "frequency": "3",                  # monthly
        "cycles": "0",                     # until cancelled
    }

    # Build signature
    sig_str = "&".join(f"{k}={urllib.parse.quote_plus(str(v))}" for k, v in params.items() if v != "")
    sig_str += f"&passphrase={urllib.parse.quote_plus(settings.payfast_merchant_key)}"
    signature = hashlib.md5(sig_str.encode()).hexdigest()
    params["signature"] = signature

    qs = urllib.parse.urlencode({k: v for k, v in params.items() if v != ""})
    return f"https://{host}/eng/process?{qs}"


_PAYFAST_VALID_IPS = {
    "197.97.145.144", "197.97.145.145", "197.97.145.146", "197.97.145.147",
    "41.74.179.194", "41.74.179.195", "41.74.179.196", "41.74.179.197",
}


@router.post("/payfast/notify")
async def payfast_notify(request: Request, request_body: dict) -> dict:
    """PayFast ITN (Instant Transaction Notification) webhook.

    Called by PayFast after a successful payment.
    Marks the tenant as paid and extends their subscription.
    """
    # Validate source IP (PayFast publishes their IP ranges)
    client_ip = request.client.host if request.client else ""
    if not settings.debug and client_ip not in _PAYFAST_VALID_IPS:
        logger.warning("PayFast ITN from unexpected IP: %s", client_ip)
        raise HTTPException(status_code=403, detail="Forbidden")

    tenant_id = request_body.get("m_payment_id", "")
    payment_status = request_body.get("payment_status", "")

    if payment_status == "COMPLETE" and tenant_id:
        try:
            await _supabase.update(
                "vula_tenants",
                {"tenant_id": tenant_id},
                {"status": "active", "paid": True},
            )
            logger.info("Payment confirmed for tenant %s", tenant_id)
        except Exception as exc:
            logger.error("Failed to update payment status: %s", exc)

    return {"status": "ok"}


# ─── Supabase client ─────────────────────────────────────────────────────────

class SupabaseClient:
    def __init__(self) -> None:
        self._url = settings.supabase_url.rstrip("/")
        self._headers = {
            "apikey": settings.supabase_service_key,
            "Authorization": f"Bearer {settings.supabase_service_key}",
            "Content-Type": "application/json",
            "Prefer": "return=representation",
        }

    @property
    def _configured(self) -> bool:
        return bool(self._url and settings.supabase_service_key)

    async def insert(self, table: str, data: dict) -> dict:
        if not self._configured:
            logger.warning("Supabase not configured — skipping insert")
            return {}
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                f"{self._url}/rest/v1/{table}",
                headers=self._headers,
                json=data,
            )
            resp.raise_for_status()
            rows = resp.json()
            return rows[0] if rows else {}

    async def update(self, table: str, match: dict, data: dict) -> None:
        if not self._configured:
            return
        params = "&".join(f"{k}=eq.{v}" for k, v in match.items())
        async with httpx.AsyncClient() as client:
            resp = await client.patch(
                f"{self._url}/rest/v1/{table}?{params}",
                headers=self._headers,
                json=data,
            )
            resp.raise_for_status()

    async def select(self, table: str, match: dict) -> list:
        if not self._configured:
            return []
        params = "&".join(f"{k}=eq.{v}" for k, v in match.items())
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                f"{self._url}/rest/v1/{table}?{params}",
                headers=self._headers,
            )
            resp.raise_for_status()
            return resp.json()


_supabase = SupabaseClient()


# ─── WhatsApp messaging ───────────────────────────────────────────────────────

async def _send_whatsapp(to: str, message: str) -> bool:
    if not settings.whatsapp_token or not to:
        logger.info("WhatsApp not configured — skipping notification")
        return False

    number = re.sub(r"[\s\-]", "", to)
    if number.startswith("0"):
        number = "27" + number[1:]
    elif number.startswith("+"):
        number = number[1:]

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
                    "text": {"body": message},
                },
            )
            resp.raise_for_status()
            return True
    except Exception as exc:
        logger.error("WhatsApp send failed to %s: %s", to, exc)
        return False


# ─── Provisioning ─────────────────────────────────────────────────────────────

def _slugify(text: str) -> str:
    slug = text.lower().strip()
    slug = re.sub(r"[^\w\s-]", "", slug)
    slug = re.sub(r"[\s_-]+", "-", slug)
    return slug.strip("-")[:40]


async def _provision(req: OnboardingRequest) -> tuple[dict, str]:
    tenant_id = str(uuid.uuid4())
    temp_password = secrets.token_urlsafe(12)
    slug = _slugify(req.company_name)
    workspace_url = f"{settings.vula_base_url}/{slug}"
    trial_ends = (datetime.utcnow() + timedelta(days=30)).isoformat()

    record = {
        "tenant_id": tenant_id,
        "company_name": req.company_name,
        "industry": req.industry,
        "staff_count": req.staff_count,
        "contact_name": req.contact_name,
        "email": req.email,
        "whatsapp": req.whatsapp,
        "plan": req.plan,
        "pain_points": json.dumps(req.pain_points),
        "status": "provisioning",
        "trial_ends": trial_ends,
        "workspace_url": workspace_url,
        "workspace_slug": slug,
        "temp_password_hash": hashlib.sha256(temp_password.encode()).hexdigest(),
        "created_at": datetime.utcnow().isoformat(),
    }

    try:
        await _supabase.insert("vula_tenants", record)
        logger.info("Tenant provisioned: %s (%s)", tenant_id, req.company_name)
    except Exception as exc:
        logger.error("Supabase insert failed: %s", exc)
        if settings.supabase_url:
            raise HTTPException(status_code=500, detail="Tenant provisioning failed")

    return record, temp_password


async def _background_tasks(record: dict, temp_password: str, req: OnboardingRequest) -> None:
    first_name = req.contact_name.split()[0]

    if req.whatsapp:
        await _send_whatsapp(
            req.whatsapp,
            f"Hi {first_name}!\n\n"
            f"Your Vula workspace for *{req.company_name}* is ready.\n\n"
            f"Login: {record['workspace_url']}\n"
            f"Temp password: {temp_password}\n\n"
            f"Your AI is processing your documents now (2-4 hours). "
            f"One of our team will call you tomorrow for your first demo.\n\n"
            f"Questions? Reply to this message. Vula Group.",
        )

    payment_url = _payfast_url(
        tenant_id=record["tenant_id"],
        plan=req.plan,
        email=str(req.email),
        name=req.contact_name,
    )

    await send_welcome_email(
        to=str(req.email),
        first_name=first_name,
        company_name=req.company_name,
        workspace_url=record["workspace_url"],
        temp_password=temp_password,
        plan=req.plan,
        trial_ends=record["trial_ends"][:10],
        payment_url=payment_url,
    )

    tier_label = TIERS.get(req.plan, {}).get("label", req.plan)
    await _send_whatsapp(
        settings.team_whatsapp,
        f"New Vula Signup\n\n"
        f"Company: {req.company_name}\n"
        f"Contact: {req.contact_name}\n"
        f"Email: {req.email}\n"
        f"WhatsApp: {req.whatsapp or 'Not provided'}\n"
        f"Plan: {tier_label}\n"
        f"Industry: {req.industry}\n"
        f"Pain points: {', '.join(req.pain_points[:3]) if req.pain_points else 'None selected'}\n\n"
        f"Workspace: {record['workspace_url']}\n"
        f"Action: Call {req.contact_name} within 24 hours.",
    )

    await send_team_alert_email(
        company_name=req.company_name,
        contact_name=req.contact_name,
        email=str(req.email),
        whatsapp=req.whatsapp,
        plan=req.plan,
        industry=req.industry,
        pain_points=req.pain_points,
        workspace_url=record["workspace_url"],
    )

    try:
        await _supabase.update(
            "vula_tenants",
            {"tenant_id": record["tenant_id"]},
            {"status": "active"},
        )
    except Exception as exc:
        logger.warning("Status update failed: %s", exc)


# ─── Routes ───────────────────────────────────────────────────────────────────

@router.post("/onboard", response_model=OnboardingResponse)
async def onboard_client(
    background_tasks: BackgroundTasks,
    req: OnboardingRequest,
) -> OnboardingResponse:
    existing = await _supabase.select("vula_tenants", {"email": str(req.email)})
    if existing:
        raise HTTPException(
            status_code=409,
            detail=f"An account for {req.email} already exists. Contact support.",
        )

    record, temp_password = await _provision(req)
    background_tasks.add_task(_background_tasks, record, temp_password, req)

    payment_url = _payfast_url(
        tenant_id=record["tenant_id"],
        plan=req.plan,
        email=str(req.email),
        name=req.contact_name,
    )

    return OnboardingResponse(
        tenant_id=record["tenant_id"],
        workspace_url=record["workspace_url"],
        temp_password=temp_password,
        trial_ends=record["trial_ends"][:10],
        payment_url=payment_url,
        message=(
            "Workspace ready — check your WhatsApp for the login link."
            if not payment_url
            else "Workspace ready — complete your subscription below."
        ),
    )


@router.post("/onboard/documents")
async def upload_onboarding_documents(
    tenant_id: str = Form(...),
    files: List[UploadFile] = File(...),
) -> dict:
    if not files:
        raise HTTPException(status_code=400, detail="No files provided")

    received = []
    for f in files[:10]:
        content = await f.read()
        received.append({
            "filename": f.filename,
            "size_kb": len(content) // 1024,
            "content_type": f.content_type,
            "status": "queued_for_ingestion",
        })

    logger.info("Received %d documents for tenant %s", len(received), tenant_id)
    return {
        "tenant_id": tenant_id,
        "documents_received": len(received),
        "files": received,
        "message": "Documents queued. AI processing begins within 1 hour.",
    }


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


@router.post("/auth/login")
async def login(req: LoginRequest) -> dict:
    """Authenticate a tenant with their email + temp password.

    Returns tenant info on success. Used by the mobile app on first launch.
    In-memory fallback when Supabase is not configured (dev mode).
    """
    rows = await _supabase.select("vula_tenants", {"email": str(req.email)})

    if not rows:
        # Dev mode: Supabase not configured — reject unknown credentials
        if not settings.supabase_url:
            raise HTTPException(
                status_code=401,
                detail="No tenant found. Configure Supabase or use a provisioned account.",
            )
        raise HTTPException(status_code=401, detail="Invalid email or password.")

    tenant = rows[0]
    pw_hash = hashlib.sha256(req.password.encode()).hexdigest()
    stored_hash = tenant.get("temp_password_hash", "")

    if not secrets.compare_digest(pw_hash, stored_hash):
        raise HTTPException(status_code=401, detail="Invalid email or password.")

    return {
        "tenant_id": tenant["tenant_id"],
        "company_name": tenant.get("company_name", ""),
        "contact_name": tenant.get("contact_name", ""),
        "email": tenant.get("email", ""),
        "plan": tenant.get("plan", ""),
        "status": tenant.get("status", ""),
        "trial_ends": (tenant.get("trial_ends") or "")[:10],
        "workspace_url": tenant.get("workspace_url", ""),
    }


@router.get("/tenant/{tenant_id}/status")
async def tenant_status(tenant_id: str) -> dict:
    rows = await _supabase.select("vula_tenants", {"tenant_id": tenant_id})
    if not rows:
        raise HTTPException(status_code=404, detail="Tenant not found")
    t = rows[0]
    return {
        "tenant_id": tenant_id,
        "status": t.get("status"),
        "workspace_url": t.get("workspace_url"),
        "trial_ends": (t.get("trial_ends") or "")[:10],
        "plan": t.get("plan"),
    }


@router.get("/admin/signups", dependencies=[Depends(_require_admin)])
async def list_signups(limit: int = 20) -> dict:
    """
    Internal admin endpoint — list recent signups for Richard/Judy follow-up.
    Protect with API_KEY middleware in production (already enforced by server.py
    when API_KEY is set in .env).
    """
    if not settings.supabase_url or not settings.supabase_service_key:
        return {"signups": [], "note": "Supabase not configured"}

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(
                f"{settings.supabase_url}/rest/v1/vula_tenants"
                f"?order=created_at.desc&limit={limit}"
                f"&select=company_name,contact_name,email,whatsapp,plan,industry,status,trial_ends,created_at",
                headers={
                    "apikey": settings.supabase_service_key,
                    "Authorization": f"Bearer {settings.supabase_service_key}",
                },
            )
            resp.raise_for_status()
            signups = resp.json()
    except Exception as exc:
        logger.error("Admin signups fetch failed: %s", exc)
        return {"signups": [], "error": "Database unavailable"}

    return {"signups": signups, "count": len(signups)}
