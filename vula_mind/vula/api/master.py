"""
vula/api/master.py — the master admin panel's backend: /v1/master/*.

Every endpoint here is behind require_master (vula/api/master_auth.py) — a verified Supabase
JWT whose user has role='master' in vula_tenant_users. This is Vula's own operator view across
ALL tenants: provisioning state, platform health, usage/cost, users, and an audit trail.

Reads aggregate tables that already exist (vula_ai_usage, vula_infra_snapshot,
vula_reasoning_telemetry, vula_whatsapp_accounts, vula_scheduler_lock,
commerce_scheduled_job_config, vula_tenants, vula_tenant_config, vula_tenant_users);
writes go through the audit() helper so vula_admin_audit (migration 072) records the actor.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException

from vula.api.master_auth import require_master

log = logging.getLogger(__name__)

router = APIRouter(tags=["master"], dependencies=[Depends(require_master)])


def _client():
    from vula.commerce import service as cs
    return cs._client()


def audit(identity: dict, action: str, tenant_id: Optional[str] = None, **detail: Any) -> None:
    """Append one admin action to vula_admin_audit. Best-effort — auditing must never block
    the action itself, but failures are logged loudly since a silent audit gap defeats the
    point of having one."""
    try:
        _client().table("vula_admin_audit").insert({
            "actor_email": identity.get("email"), "actor_id": identity.get("user_id"),
            "action": action, "tenant_id": tenant_id, "detail": detail or {},
        }).execute()
    except Exception as exc:
        log.error("AUDIT WRITE FAILED (%s by %s): %s — run migration 072?",
                  action, identity.get("email"), exc)


@router.get("/me")
async def me(identity: dict = Depends(require_master)):
    """Who am I (verified) — also the smoke-test endpoint for the auth matrix."""
    return identity


# ── Tenants & provisioning ────────────────────────────────────────────────────

@router.get("/tenants")
async def master_tenants():
    """Joined view: configured tenants (vula_tenant_config) + signup/billing state
    (vula_tenants, matched on tenant_id) + login count (vula_tenant_users)."""
    db = _client()
    cfg = db.table("vula_tenant_config").select("*").order("display_name").execute().data or []
    signups = {r.get("tenant_id"): r for r in
               (db.table("vula_tenants").select("*").execute().data or [])}
    users = db.table("vula_tenant_users").select("tenant_id,role").execute().data or []
    user_counts: dict[str, int] = {}
    for u in users:
        user_counts[u["tenant_id"]] = user_counts.get(u["tenant_id"], 0) + 1
    out = []
    for c in cfg:
        s = signups.get(c["tenant_id"]) or {}
        out.append({
            **{k: c.get(k) for k in ("tenant_id", "display_name", "business_type", "modules",
                                     "theme", "active", "plan", "store_url",
                                     "default_payment_provider") if k in c},
            "paid": s.get("paid"), "signup_status": s.get("status"),
            "trial_ends": s.get("trial_ends"), "signup_email": s.get("email"),
            "logins": user_counts.get(c["tenant_id"], 0),
        })
    return {"tenants": out}


@router.patch("/tenants/{tenant_id}")
async def master_update_tenant(tenant_id: str, body: dict,
                               identity: dict = Depends(require_master)):
    """Update a tenant's config (modules, display_name, theme, active). Audited."""
    allowed = {"display_name", "business_type", "modules", "theme", "active", "plan",
               "store_url", "default_payment_provider"}
    patch = {k: v for k, v in (body or {}).items() if k in allowed}
    if not patch:
        raise HTTPException(status_code=400, detail=f"nothing to update (allowed: {sorted(allowed)})")
    res = (_client().table("vula_tenant_config").update(patch)
           .eq("tenant_id", tenant_id).execute())
    if not res.data:
        raise HTTPException(status_code=404, detail=f"tenant '{tenant_id}' not found")
    from vula.api import tenants as _tenants
    _tenants.invalidate(tenant_id)
    audit(identity, "tenant_updated", tenant_id, patch=patch)
    return res.data[0]


@router.get("/tenants/{tenant_id}/setup")
async def master_tenant_setup(tenant_id: str):
    """Onboarding cockpit (UI overhaul P3): the 9-step go-live checklist, COMPUTED live from
    real state (config/team/WhatsApp/templates/payments/products/pages/orders) — no manually
    maintained status field to drift out of date."""
    db = _client()

    def _count(table, **eq):
        try:
            q = db.table(table).select("id")
            for k, v in eq.items():
                q = q.eq(k, v)
            return len(q.limit(50).execute().data or [])
        except Exception:
            return 0

    cfg = (db.table("vula_tenant_config").select("*")
           .eq("tenant_id", tenant_id).limit(1).execute().data or [None])[0]
    if not cfg:
        raise HTTPException(status_code=404, detail=f"tenant '{tenant_id}' not found")
    theme = cfg.get("theme") or {}

    wa = (db.table("vula_whatsapp_accounts").select("status")
          .eq("tenant_id", tenant_id).limit(1).execute().data or [None])[0]
    try:
        inv = (db.table("commerce_invoice_settings").select("logo_url,accent_color")
               .eq("tenant_id", tenant_id).limit(1).execute().data or [None])[0]
    except Exception:
        inv = None
    branded = bool(theme.get("logo_url") or theme.get("accent")
                   or (inv or {}).get("logo_url") or (inv or {}).get("accent_color"))
    try:
        pays = (db.table("commerce_order_settings").select("payment_methods,eft_details")
                .eq("tenant_id", tenant_id).limit(1).execute().data or [None])[0]
    except Exception:
        pays = None
    payments_ready = bool(pays and (pays.get("eft_details") or pays.get("payment_methods")))

    steps = [
        {"id": "created", "label": "Created from business type", "done": True,
         "detail": f"{len(cfg.get('modules') or [])} modules enabled"},
        {"id": "branding", "label": "Brand kit", "done": branded,
         "detail": "logo/accent set" if branded else "no logo or accent yet"},
        {"id": "team", "label": "Team & logins",
         "done": _count("vula_team_members", tenant_id=tenant_id, active=True) > 0,
         "detail": f"{_count('vula_team_members', tenant_id=tenant_id, active=True)} member(s)"},
        {"id": "whatsapp", "label": "WhatsApp connected",
         "done": bool(wa and wa.get("status") == "connected"),
         "detail": (wa or {}).get("status") or "not connected"},
        {"id": "templates", "label": "Message templates",
         "done": _count("commerce_wa_templates", tenant_id=tenant_id) > 0,
         "detail": f"{_count('commerce_wa_templates', tenant_id=tenant_id)} submitted"},
        {"id": "payments", "label": "Payment details", "done": payments_ready,
         "detail": "configured" if payments_ready else "no gateway or EFT details"},
        {"id": "knowledge", "label": "Products & knowledge",
         "done": _count("commerce_products", tenant_id=tenant_id) > 0,
         "detail": f"{_count('commerce_products', tenant_id=tenant_id)} product(s)"},
        {"id": "storefront", "label": "Storefront pages",
         "done": _count("vula_pages", tenant_id=tenant_id) > 0 or bool(cfg.get("store_url")),
         "detail": cfg.get("store_url") or f"{_count('vula_pages', tenant_id=tenant_id)} page(s)"},
        {"id": "golive", "label": "First order through",
         "done": _count("commerce_orders", tenant_id=tenant_id) > 0,
         "detail": f"{_count('commerce_orders', tenant_id=tenant_id)} order(s)"},
    ]
    done = sum(1 for s in steps if s["done"])
    return {"tenant_id": tenant_id, "display_name": cfg.get("display_name"),
            "steps": steps, "done": done, "total": len(steps),
            "progress_pct": round(100 * done / len(steps))}


# ── Platform health ───────────────────────────────────────────────────────────

@router.get("/health")
async def master_health():
    """One call for the Health sub-panel: WhatsApp lines, scheduler, LLM routing, escalations."""
    db = _client()
    now = datetime.now(timezone.utc)
    out: dict[str, Any] = {}

    try:
        out["whatsapp"] = [
            {k: r.get(k) for k in ("tenant_id", "phone_number", "status", "last_error",
                                   "webhook_registered", "verified_name")}
            for r in (db.table("vula_whatsapp_accounts").select("*").execute().data or [])]
    except Exception as exc:
        out["whatsapp"] = {"error": str(exc)}

    try:
        lock = (db.table("vula_scheduler_lock").select("*").limit(1).execute().data or [None])[0]
        out["scheduler"] = {
            "leader": (lock or {}).get("holder"),
            "lease_expires_at": (lock or {}).get("expires_at"),
            "lease_expired": bool(lock) and str(lock.get("expires_at", "")) < now.isoformat(),
        }
    except Exception as exc:
        out["scheduler"] = {"error": str(exc)}

    try:
        out["scheduled_jobs"] = (db.table("commerce_scheduled_job_config")
                                 .select("tenant_id,job_type,enabled,last_fired_at,template_name,hour,minute")
                                 .order("tenant_id").execute().data or [])
    except Exception as exc:
        out["scheduled_jobs"] = {"error": str(exc)}

    try:
        since = (now - timedelta(hours=24)).isoformat()
        rows = (db.table("vula_reasoning_telemetry")
                .select("system,outcome,escalated,reason")
                .gte("created_at", since).limit(2000).execute().data or [])
        router_rows = [r for r in rows if r.get("system") == "vula-llm-router"]
        local = sum(1 for r in router_rows if r.get("outcome") == "local")
        cloud = sum(1 for r in router_rows if r.get("outcome") == "cloud")
        reasons: dict[str, int] = {}
        for r in router_rows:
            if r.get("escalated"):
                reasons[r.get("reason") or "?"] = reasons.get(r.get("reason") or "?", 0) + 1
        out["llm_router_24h"] = {"total": len(router_rows), "local": local, "cloud": cloud,
                                 "escalation_reasons": reasons}
    except Exception as exc:
        out["llm_router_24h"] = {"error": str(exc)}

    try:
        esc = (db.table("vula_escalations").select("tenant_id,status,created_at")
               .eq("status", "open").order("created_at").limit(50).execute().data or [])
        out["escalations"] = {"open": len(esc),
                              "oldest": esc[0]["created_at"] if esc else None}
    except Exception as exc:
        out["escalations"] = {"error": str(exc)}

    try:
        since = (now - timedelta(hours=24)).isoformat()
        rows = (db.table("vula_webhook_failures").select("*")
                .gte("created_at", since).order("created_at", desc=True).limit(50).execute().data or [])
        out["webhook_failures_24h"] = {"count": len(rows), "recent": rows[:10]}
    except Exception as exc:
        out["webhook_failures_24h"] = {"count": 0, "recent": [], "note": f"{exc} (run migration 076?)"}

    out["migration_state"] = _probe_migrations(db)

    return out


# Recent migrations this session added — a cheap existence probe since there's no formal
# schema_migrations tracking table (migrations are hand-run SQL files). Lets Health flag
# "you probably haven't run 073 yet" instead of tenants silently hitting empty-catch fallbacks.
_MIGRATION_PROBES: list[tuple[str, str, str]] = [
    ("069", "commerce_scheduled_job_config", "Scheduling tab"),
    ("070", "commerce_order_settings", "Delivery areas/fees"),          # column-only, table always exists
    ("071", "vula_wa_msg_dedup", "Durable WhatsApp dedup"),
    ("072", "vula_admin_audit", "Master audit trail"),
    ("073", "commerce_categories", "Product categories/sale price"),
    ("074", "commerce_geo_cache", "Marketplace-style delivery radius"),
    ("075", "commerce_saved_copy", "Marketing saved-copy library"),
    ("076", "vula_webhook_failures", "Webhook failure feed"),
]


def _probe_migrations(db) -> list[dict]:
    out = []
    for num, table, note in _MIGRATION_PROBES:
        try:
            db.table(table).select("*").limit(1).execute()
            applied = True
        except Exception:
            applied = False
        out.append({"migration": num, "table": table, "note": note, "applied": applied})
    return out


# ── Usage & billing ───────────────────────────────────────────────────────────

@router.get("/usage")
async def master_usage(days: int = 14):
    """Per-tenant AI + infra cost for the last N days, plus per-day series for charts."""
    db = _client()
    since = (datetime.now(timezone.utc).date() - timedelta(days=days)).isoformat()
    ai = (db.table("vula_ai_usage").select("tenant_id,day,model,calls,est_cost_usd")
          .gte("day", since).execute().data or [])
    infra = (db.table("vula_infra_snapshot").select("tenant_id,day,vectors,storage_mb,est_cost_usd")
             .gte("day", since).execute().data or [])
    per_tenant: dict[str, dict] = {}
    for r in ai:
        t = per_tenant.setdefault(r["tenant_id"], {"ai_cost_usd": 0.0, "calls": 0, "infra_cost_usd": 0.0})
        t["ai_cost_usd"] += float(r.get("est_cost_usd") or 0)
        t["calls"] += int(r.get("calls") or 0)
    latest_infra: dict[str, dict] = {}
    for r in infra:
        cur = latest_infra.get(r["tenant_id"])
        if not cur or r["day"] > cur["day"]:
            latest_infra[r["tenant_id"]] = r
    for tid, r in latest_infra.items():
        t = per_tenant.setdefault(tid, {"ai_cost_usd": 0.0, "calls": 0, "infra_cost_usd": 0.0})
        t["infra_cost_usd"] = float(r.get("est_cost_usd") or 0)
        t["vectors"] = r.get("vectors")
        t["storage_mb"] = r.get("storage_mb")
    return {"since": since, "per_tenant": per_tenant, "ai_daily": ai}


# ── Users & access ────────────────────────────────────────────────────────────

@router.get("/users")
async def master_users():
    """Every login across all tenants — with emails resolved from the auth server (the role
    table only stores user_ids)."""
    rows = (_client().table("vula_tenant_users").select("*")
            .order("created_at", desc=True).limit(500).execute().data or [])
    import httpx as _hx
    from config import settings as _s
    key = _s.supabase_service_role_key or _s.supabase_service_key
    emails: dict[str, str] = {}
    try:
        async with _hx.AsyncClient(timeout=10.0) as client:
            for uid in {r["user_id"] for r in rows}:
                try:
                    resp = await client.get(f"{_s.supabase_url.rstrip('/')}/auth/v1/admin/users/{uid}",
                                            headers={"apikey": key, "Authorization": f"Bearer {key}"})
                    if resp.status_code == 200:
                        emails[uid] = resp.json().get("email") or ""
                except Exception:
                    pass
    except Exception:
        pass
    for r in rows:
        r["email"] = emails.get(r["user_id"], "")
    return {"users": rows}


@router.post("/tenants/{tenant_id}/users")
async def master_create_user(tenant_id: str, body: dict, identity: dict = Depends(require_master)):
    """Create a login for ANY tenant. vula/api/users.py has no auth of its own (a tenant owner
    hits it directly from their own dashboard) — this wrapper reuses that logic under the
    already-enforced require_master gate so master can do the same thing cross-tenant safely."""
    from vula.api.users import create_user, CreateUserIn
    result = await create_user(tenant_id, CreateUserIn(**(body or {})))
    audit(identity, "master_create_user", tenant_id, email=body.get("email"), role=body.get("role"))
    return result


@router.post("/tenants/{tenant_id}/users/{user_id}/reset")
async def master_reset_user_password(tenant_id: str, user_id: str, identity: dict = Depends(require_master)):
    from vula.api.users import reset_password
    result = await reset_password(tenant_id, user_id)
    audit(identity, "master_reset_password", tenant_id, user_id=user_id)
    return result


@router.delete("/tenants/{tenant_id}/users/{user_id}")
async def master_remove_user(tenant_id: str, user_id: str, identity: dict = Depends(require_master)):
    from vula.api.users import remove_access
    result = await remove_access(tenant_id, user_id)
    audit(identity, "master_remove_user_access", tenant_id, user_id=user_id)
    return result


# ── Audit log ─────────────────────────────────────────────────────────────────

@router.get("/audit")
async def master_audit(tenant_id: Optional[str] = None, limit: int = 100):
    q = (_client().table("vula_admin_audit").select("*")
         .order("created_at", desc=True).limit(min(limit, 500)))
    if tenant_id:
        q = q.eq("tenant_id", tenant_id)
    try:
        return {"events": q.execute().data or []}
    except Exception as exc:
        return {"events": [], "error": f"{exc} (run migration 072?)"}
