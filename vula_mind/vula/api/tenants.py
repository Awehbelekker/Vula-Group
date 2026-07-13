"""
vula/api/tenants.py — Tenant Control Plane.

Single source of truth for per-tenant operational config (vula_tenant_config), keyed by the
slug tenant_id used everywhere (off-the-hook, digg-demo). Drives: which modules a tenant sees
(by business type), theme, store URL, default payment gateway, WhatsApp number. Lets a new
store be onboarded as config, never code.

    GET   /v1/tenants                 list all (master)
    GET   /v1/tenants/registry        module catalog + business-type presets (for the UI)
    GET   /v1/tenants/{id}            public-safe config (display_name, theme, store_url, modules)
    POST  /v1/tenants                 create/seed a tenant from a business type
    PATCH /v1/tenants/{id}            update config (modules, theme, store_url, gateway, …)
"""
from __future__ import annotations

import logging
import time
from typing import Optional

from fastapi import APIRouter
from pydantic import BaseModel

log = logging.getLogger(__name__)
router = APIRouter(tags=["tenants"])


# ── Capability catalog ────────────────────────────────────────────────────────
# key → human label. Keys map 1:1 to dashboard tab ids / merchant sub-tabs so the
# UI can filter what a tenant sees by their enabled `modules`.
MODULES: dict[str, str] = {
    "products":   "Products",
    "orders":     "Orders",
    "bookings":   "Bookings",
    "payments":   "Payments",
    "invoices":   "Invoices",
    "delivery":   "Delivery",
    "pages":      "Website / Pages",
    "crm":        "Customers (CRM)",
    "reports":    "Reports",
    "broadcasts": "Broadcasts",
    "marketing":  "Marketing (AI copy)",
    "inbox":      "Team Inbox",
    "automations":"Automations",
    "budget":     "Budget",
    "scanner":    "Smart Scanner",
    "fieldops":   "Field Ops",
    "projects":   "Projects",
    "documents":  "Documents / KB",
    "finances":   "Finances",
    "followups":  "Follow-ups",
    "workspace":  "Workspace",
    "team":       "Team",
}

# Business type → the modules switched on by default at onboarding (master can override).
BUSINESS_TYPES: dict[str, dict] = {
    "food":     {"label": "Food / Restaurant / Takeaway",
                 "modules": ["products", "orders", "payments", "invoices", "delivery",
                             "crm", "reports", "broadcasts", "marketing", "inbox", "team"]},
    "retail":   {"label": "Retail / E-commerce",
                 "modules": ["products", "orders", "payments", "invoices", "pages",
                             "crm", "reports", "broadcasts", "marketing", "inbox", "team"]},
    "services": {"label": "Professional services (architecture, agency, consulting)",
                 "modules": ["invoices", "bookings", "projects", "documents", "finances", "fieldops",
                             "followups", "reports", "team"]},
    "trades":   {"label": "Trades / Construction / Field work",
                 "modules": ["invoices", "fieldops", "projects", "budget", "scanner",
                             "finances", "followups", "team"]},
    "health":   {"label": "Health / Wellness / Bookings",
                 "modules": ["bookings", "invoices", "crm", "followups", "broadcasts", "marketing",
                             "inbox", "reports", "pages", "team"]},
    "other":    {"label": "Other / General",
                 "modules": ["invoices", "crm", "reports", "marketing", "followups", "team"]},
}


def _client():
    from vula.commerce import service as cs
    return cs._client()


def _now() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()


# ── Config resolver (cached) ──────────────────────────────────────────────────
_CACHE: dict[str, tuple[float, dict]] = {}
_TTL = 60.0  # seconds


def get_config(tenant_id: str, fresh: bool = False) -> dict:
    """Operational config for a tenant (cached). Empty dict if none / table missing."""
    if not fresh:
        hit = _CACHE.get(tenant_id)
        if hit and (time.time() - hit[0]) < _TTL:
            return hit[1]
    try:
        rows = (_client().table("vula_tenant_config").select("*")
                .eq("tenant_id", tenant_id).limit(1).execute().data or [])
        cfg = rows[0] if rows else {}
    except Exception as exc:
        log.debug("tenant_config read skipped (run migration 040?): %s", exc)
        cfg = {}
    _CACHE[tenant_id] = (time.time(), cfg)
    return cfg


def store_url(tenant_id: str) -> Optional[str]:
    cfg = get_config(tenant_id)
    if cfg.get("store_url"):
        return cfg["store_url"]
    from config import settings
    return settings.store_urls.get(tenant_id)


def enabled_modules(tenant_id: str) -> list:
    return get_config(tenant_id).get("modules") or []


def _public(cfg: dict) -> dict:
    """Storefront/dashboard-safe subset (no internal columns)."""
    return {
        "tenant_id": cfg.get("tenant_id"), "display_name": cfg.get("display_name"),
        "business_type": cfg.get("business_type"), "theme": cfg.get("theme") or {},
        "store_url": cfg.get("store_url"), "modules": cfg.get("modules") or [],
        "default_payment_provider": cfg.get("default_payment_provider"),
        "status": cfg.get("status"),
    }


# ── API ───────────────────────────────────────────────────────────────────────
@router.get("")
@router.get("/")
async def list_tenants() -> dict:
    try:
        rows = (_client().table("vula_tenant_config").select("*")
                .order("display_name").execute().data or [])
    except Exception as exc:
        return {"tenants": [], "error": f"{exc} (run migration 040?)"}
    return {"tenants": [_public(r) for r in rows]}


@router.get("/registry")
async def registry() -> dict:
    return {
        "modules": [{"id": k, "label": v} for k, v in MODULES.items()],
        "business_types": [{"id": k, "label": v["label"], "modules": v["modules"]}
                           for k, v in BUSINESS_TYPES.items()],
    }


@router.get("/ai-spend")
async def ai_spend(days: int = 14) -> dict:
    """AI/LLM spend (COGS) across all tenants — from vula_ai_usage. Master view."""
    from datetime import datetime, timezone, timedelta
    since = (datetime.now(timezone.utc).date() - timedelta(days=days)).isoformat()
    try:
        rows = (_client().table("vula_ai_usage")
                .select("tenant_id,day,model,calls,prompt_tokens,completion_tokens,est_cost_usd")
                .gte("day", since).execute().data or [])
    except Exception as exc:
        return {"error": f"{exc} (run migration 031?)", "total_usd": 0,
                "by_tenant": [], "by_model": [], "daily": []}

    def _agg(key):
        d: dict = {}
        for r in rows:
            k = r.get(key) or "?"
            e = d.setdefault(k, {"key": k, "usd": 0.0, "calls": 0, "tokens": 0})
            e["usd"] += float(r.get("est_cost_usd") or 0)
            e["calls"] += int(r.get("calls") or 0)
            e["tokens"] += int(r.get("prompt_tokens") or 0) + int(r.get("completion_tokens") or 0)
        return [{**v, "usd": round(v["usd"], 4)} for v in sorted(d.values(), key=lambda x: -x["usd"])]

    daily: dict = {}
    for r in rows:
        e = daily.setdefault(r.get("day"), {"day": r.get("day"), "usd": 0.0})
        e["usd"] += float(r.get("est_cost_usd") or 0)
    return {
        "days": days,
        "total_usd": round(sum(float(r.get("est_cost_usd") or 0) for r in rows), 4),
        "total_calls": sum(int(r.get("calls") or 0) for r in rows),
        "by_tenant": _agg("tenant_id"),
        "by_model": _agg("model"),
        "daily": [{"day": v["day"], "usd": round(v["usd"], 4)} for v in sorted(daily.values(), key=lambda x: x["day"])],
    }


@router.get("/{tenant_id}")
async def get_tenant(tenant_id: str) -> dict:
    cfg = get_config(tenant_id, fresh=True)
    if not cfg:
        # Unknown tenant → empty-but-valid shape so storefront/dashboard still render.
        return _public({"tenant_id": tenant_id})
    return _public(cfg)


class TenantIn(BaseModel):
    tenant_id: str
    display_name: Optional[str] = None
    business_type: Optional[str] = "other"
    store_url: Optional[str] = None
    plan: Optional[str] = "starter"


@router.post("")
@router.post("/")
async def create_tenant(body: TenantIn) -> dict:
    """Seed a tenant from a business type — modules auto-enabled from the preset."""
    preset = BUSINESS_TYPES.get(body.business_type or "other", BUSINESS_TYPES["other"])
    row = {
        "tenant_id": body.tenant_id, "display_name": body.display_name or body.tenant_id,
        "business_type": body.business_type or "other", "store_url": body.store_url,
        "modules": preset["modules"], "plan": body.plan or "starter",
        "status": "active", "updated_at": _now(),
    }
    try:
        existing = (_client().table("vula_tenant_config").select("tenant_id")
                    .eq("tenant_id", body.tenant_id).limit(1).execute().data or [])
        if existing:
            _client().table("vula_tenant_config").update(row).eq("tenant_id", body.tenant_id).execute()
        else:
            _client().table("vula_tenant_config").insert(row).execute()
    except Exception as exc:
        return {"error": f"{exc} (run migration 040?)"}
    _CACHE.pop(body.tenant_id, None)
    return {"tenant": _public(row)}


class TenantPatch(BaseModel):
    display_name: Optional[str] = None
    business_type: Optional[str] = None
    store_url: Optional[str] = None
    domains: Optional[list] = None
    theme: Optional[dict] = None
    default_payment_provider: Optional[str] = None
    wa_phone_number_id: Optional[str] = None
    modules: Optional[list] = None
    plan: Optional[str] = None
    status: Optional[str] = None


@router.patch("/{tenant_id}")
async def patch_tenant(tenant_id: str, body: TenantPatch) -> dict:
    patch = {k: v for k, v in body.model_dump().items() if v is not None}
    if not patch:
        return {"tenant": _public(get_config(tenant_id, fresh=True))}
    patch["updated_at"] = _now()
    try:
        _client().table("vula_tenant_config").update(patch).eq("tenant_id", tenant_id).execute()
    except Exception as exc:
        return {"error": f"{exc} (run migration 040?)"}
    _CACHE.pop(tenant_id, None)
    return {"tenant": _public(get_config(tenant_id, fresh=True))}
