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
import re
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
    # Keyed by workspace_slug (the operational tenant id); vula_tenants.tenant_id is a UUID and
    # never matched a config row, so billing columns were always blank.
    signups = {}
    for r in (db.table("vula_tenants").select("*").execute().data or []):
        signups[r.get("workspace_slug") or str(r.get("tenant_id"))] = r
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
                                     "default_payment_provider",
                                     "share_knowledge_with_network", "spend_cap_usd") if k in c},
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
               "store_url", "default_payment_provider", "share_knowledge_with_network",
               "spend_cap_usd"}
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


# ── Billing lifecycle (vula_tenants: signup/payment state) ─────────────────────
# The Tenants tab's existing Suspend/Activate toggles vula_tenant_config.active — operational,
# any reason. These four are specifically about the *subscription*, reading/writing
# vula_tenants (paid, status, trial_ends) instead — the "was write-only" audit finding closed
# 2026-07-24. mark-paid/extend-trial are pure vula_tenants writes; cancel/reactivate also flip
# vula_tenant_config.active so cancelling actually stops the bot/checkout/logins, not just the
# billing label (same enforcement path the Suspend toggle uses).

def _billing_row_not_found(tenant_id: str) -> HTTPException:
    return HTTPException(status_code=404, detail=f"tenant '{tenant_id}' not found in vula_tenants")


@router.post("/tenants/{tenant_id}/mark-paid")
async def master_mark_paid(tenant_id: str, identity: dict = Depends(require_master)) -> dict:
    """Manual payment confirmation (EFT, correction) — PayFast's ITN webhook does this
    automatically for a real gateway payment; this covers everything else."""
    res = (_client().table("vula_tenants").update({"paid": True, "status": "active"})
           .eq("workspace_slug", tenant_id).execute())
    if not res.data:
        raise _billing_row_not_found(tenant_id)
    audit(identity, "tenant_marked_paid", tenant_id)
    from vula.api import merchant_audit
    merchant_audit.audit(tenant_id, identity, "billing_marked_paid")
    return res.data[0]


@router.post("/tenants/{tenant_id}/impersonate")
async def master_impersonate_tenant(tenant_id: str, body: dict,
                                    identity: dict = Depends(require_master)) -> dict:
    """Logged the moment master opens a tenant's own workspace ("Open as tenant" in the
    dashboard). 2026-09-15 audit: the VIEW itself already worked (is_tenant_member already
    lets a master JWT through tenant_admin_guard for any tenant — see vula/api/tenant_auth.py
    — and any write already gets attributed to master's real identity via
    require_tenant_actor + merchant_audit). What was actually missing was a clean, dedicated
    record of WHO looked at WHICH tenant's real data, WHEN, and WHY — support reproduction
    needs that distinct from the per-write attribution that already happens for free once
    inside. Dual-written like every other tenant-affecting master action: vula_admin_audit
    (master's own cross-tenant view) and vula_merchant_audit (so the tenant's own audit trail
    shows it too — a tenant should be able to see when master looked at their account, not
    just take it on faith)."""
    reason = ((body or {}).get("reason") or "").strip()
    audit(identity, "master_impersonate_tenant", tenant_id, reason=reason or None)
    from vula.api import merchant_audit
    merchant_audit.audit(tenant_id, identity, "master_viewed_as_tenant", reason=reason or None)
    return {"ok": True}


@router.post("/tenants/{tenant_id}/extend-trial")
async def master_extend_trial(tenant_id: str, body: dict,
                              identity: dict = Depends(require_master)) -> dict:
    days = int((body or {}).get("days") or 14)
    rows = (_client().table("vula_tenants").select("trial_ends")
            .eq("workspace_slug", tenant_id).limit(1).execute().data or [])
    if not rows:
        raise _billing_row_not_found(tenant_id)
    current = rows[0].get("trial_ends")
    base = datetime.fromisoformat(current.replace("Z", "+00:00")) if current else datetime.now(timezone.utc)
    base = max(base, datetime.now(timezone.utc))  # extend from today if the trial already lapsed
    new_end = (base + timedelta(days=days)).isoformat()
    res = (_client().table("vula_tenants").update({"trial_ends": new_end})
           .eq("workspace_slug", tenant_id).execute())
    audit(identity, "tenant_trial_extended", tenant_id, days=days, new_trial_ends=new_end)
    from vula.api import merchant_audit
    merchant_audit.audit(tenant_id, identity, "billing_trial_extended", days=days)
    return res.data[0]


@router.post("/tenants/{tenant_id}/cancel")
async def master_cancel_subscription(tenant_id: str, identity: dict = Depends(require_master)) -> dict:
    res = (_client().table("vula_tenants").update({"status": "cancelled"})
           .eq("workspace_slug", tenant_id).execute())
    if not res.data:
        raise _billing_row_not_found(tenant_id)
    _client().table("vula_tenant_config").update({"active": False}).eq("tenant_id", tenant_id).execute()
    from vula.api import tenants as _tenants
    _tenants.invalidate(tenant_id)
    audit(identity, "tenant_cancelled", tenant_id)
    from vula.api import merchant_audit
    merchant_audit.audit(tenant_id, identity, "billing_cancelled")
    return res.data[0]


@router.post("/tenants/{tenant_id}/reactivate")
async def master_reactivate_subscription(tenant_id: str,
                                         identity: dict = Depends(require_master)) -> dict:
    res = (_client().table("vula_tenants").update({"status": "active"})
           .eq("workspace_slug", tenant_id).execute())
    if not res.data:
        raise _billing_row_not_found(tenant_id)
    _client().table("vula_tenant_config").update({"active": True}).eq("tenant_id", tenant_id).execute()
    from vula.api import tenants as _tenants
    _tenants.invalidate(tenant_id)
    audit(identity, "tenant_reactivated", tenant_id)
    from vula.api import merchant_audit
    merchant_audit.audit(tenant_id, identity, "billing_reactivated")
    return res.data[0]


@router.get("/tenants/{tenant_id}/setup")
async def master_tenant_setup(tenant_id: str):
    """Onboarding cockpit (UI overhaul P3) — see setup_checklist."""
    return setup_checklist(tenant_id)


def setup_checklist(tenant_id: str) -> dict:
    """The go-live checklist, COMPUTED live from real state (config/team/WhatsApp/templates/
    payments/products/knowledge/pages/orders) — no manually maintained status field to drift
    out of date. Shared by master's cockpit and the tenant's own Home (2026-09-25: tenants
    never saw it, so a new business had no idea what was left to do). Each step carries the
    dashboard tab that fixes it."""
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
    gateways = _count("vula_payment_providers", tenant_id=tenant_id, active=True) + \
        _count("vula_yoco_accounts", tenant_id=tenant_id)
    payments_ready = bool(gateways or (pays and pays.get("eft_details")))
    try:
        tpls = db.table("commerce_wa_templates").select("status").eq("tenant_id", tenant_id) \
            .limit(100).execute().data or []
    except Exception:
        tpls = []
    approved = sum(1 for t in tpls if (t.get("status") or "").upper() == "APPROVED")
    docs = _count("vula_filed_documents", tenant_id=tenant_id)
    products = _count("commerce_products", tenant_id=tenant_id)
    try:
        vat = (db.table("commerce_invoice_settings").select("vat_registered")
               .eq("tenant_id", tenant_id).limit(1).execute().data or [None])[0]
    except Exception:
        vat = None
    vat_set = bool(vat) and vat.get("vat_registered") is not None

    team_n = _count("vula_team_members", tenant_id=tenant_id, active=True)
    orders_n = _count("commerce_orders", tenant_id=tenant_id)
    pages_n = _count("vula_pages", tenant_id=tenant_id)
    steps = [
        {"id": "created", "label": "Created from business type", "done": True, "tab": "settings",
         "detail": f"{len(cfg.get('modules') or [])} modules enabled"},
        {"id": "branding", "label": "Brand kit", "done": branded, "tab": "settings",
         "detail": "logo/accent set" if branded else "no logo or accent yet"},
        {"id": "team", "label": "Team & logins", "done": team_n > 0, "tab": "team",
         "detail": f"{team_n} member(s)"},
        {"id": "whatsapp", "label": "WhatsApp connected", "tab": "settings",
         "done": bool(wa and wa.get("status") == "connected"),
         "detail": (wa or {}).get("status") or "not connected"},
        # APPROVED, not merely submitted: Meta rejects proactive sends on a pending template.
        {"id": "templates", "label": "Message templates approved", "done": approved > 0,
         "tab": "wa-templates", "detail": f"{approved} approved of {len(tpls)} submitted"},
        {"id": "payments", "label": "Payments (gateway or EFT)", "done": payments_ready,
         "tab": "payments", "detail": "configured" if payments_ready else "no gateway or EFT details"},
        {"id": "vat", "label": "VAT status confirmed", "done": vat_set, "tab": "invoices",
         "detail": ("VAT registered" if (vat or {}).get("vat_registered") else "not VAT registered")
                   if vat_set else "not set — invoices assume VAT-registered until you say"},
        {"id": "knowledge", "label": "Products & knowledge", "done": products > 0 or docs > 0,
         "tab": "products" if products or not docs else "documents",
         "detail": f"{products} product(s), {docs} document(s)"},
        {"id": "storefront", "label": "Storefront pages", "tab": "pages",
         "done": pages_n > 0 or bool(cfg.get("store_url")),
         "detail": cfg.get("store_url") or f"{pages_n} page(s)"},
        {"id": "golive", "label": "First order through", "done": orders_n > 0, "tab": "orders",
         "detail": f"{orders_n} order(s)"},
    ]
    done = sum(1 for s in steps if s["done"])
    return {"tenant_id": tenant_id, "display_name": cfg.get("display_name"),
            "steps": steps, "done": done, "total": len(steps),
            "progress_pct": round(100 * done / len(steps))}


@router.get("/tenants/{tenant_id}/conversations")
async def master_tenant_conversations(tenant_id: str, limit: int = 30) -> dict:
    """Recent conversation threads for a tenant (Master Build Brief section 6a item 2) — support
    staff/master admin can now read a tenant's real conversation history without direct Railway/
    Supabase access. WhatsApp and portal chat already share one durable store
    (vula/chat/history.py's vula_chat_messages) — this is the thread picker; see the sibling
    /conversations/{phone} endpoint below for one thread's actual messages."""
    from vula.chat.history import get_db
    threads = get_db().list_threads(tenant_id, limit=min(limit, 100))
    return {"tenant_id": tenant_id, "threads": threads}


@router.get("/tenants/{tenant_id}/conversations/{phone}")
async def master_tenant_conversation_messages(tenant_id: str, phone: str, limit: int = 100) -> dict:
    """One thread's actual messages, oldest-first (matches the dashboard's own /v1/chat/{tenant}/
    history endpoint's ordering) — max_age_hours=None because a support reproduction often needs
    a conversation from days ago, unlike the AI's own prompt-context read which only wants
    recent turns."""
    from vula.chat.history import get_db
    msgs = get_db().get(tenant_id, phone=phone, limit=min(limit, 200), max_age_hours=None)
    return {"tenant_id": tenant_id, "phone": phone,
            "messages": [{"role": m.role, "text": m.text, "created_at": m.created_at}
                         for m in msgs]}


@router.get("/tenants/{tenant_id}/errors")
async def master_tenant_errors(tenant_id: str, hours: int = 168) -> dict:
    """Per-tenant error-event drill-down (Master Build Brief section 6a item 2) — the companion
    to /health above, which only ever reports platform-wide aggregates. Two real, unambiguous
    error sources, not routing telemetry noise: webhook processing failures (vula_webhook_
    failures, migration 099) and adversarial-verification defects/checker failures
    (vula_reasoning_telemetry where system="verified-reasoning" — see core/verification.py's
    register_outcome). Deliberately excludes vula-llm-router escalation events: "cloud because
    genuinely complex" is normal, healthy routing, not an error, and mixing it in here would
    make a real problem harder to spot, not easier. Default window is 7 days, not 24h like
    /health, since a support reproduction is rarely about "right now"."""
    db = _client()
    since = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
    out: dict[str, Any] = {"tenant_id": tenant_id, "window_hours": hours}
    try:
        out["webhook_failures"] = (
            db.table("vula_webhook_failures").select("*")
            .eq("tenant_id", tenant_id).gte("created_at", since)
            .order("created_at", desc=True).limit(100).execute().data or [])
    except Exception as exc:
        out["webhook_failures"] = {"error": str(exc)}
    try:
        rows = (db.table("vula_reasoning_telemetry")
                .select("task,outcome,escalated,reason,extra,created_at")
                .eq("tenant_id", tenant_id).eq("system", "verified-reasoning")
                .gte("created_at", since).order("created_at", desc=True)
                .limit(200).execute().data or [])
        out["verification_flags"] = [
            r for r in rows if r.get("outcome") in
            ("defect_found", "checker_error", "checker_unparseable")][:100]
    except Exception as exc:
        out["verification_flags"] = {"error": str(exc)}
    return out


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

        # Skill-routing 24h (emitted by core/hrm/orchestrator.py::plan). "default" = keyword
        # table missed AND the LLM classifier didn't rescue it → the generic 'reasoning' skill,
        # the single biggest source of a wrong answer. Watch this ratio.
        route_rows = [r for r in rows if r.get("system") == "vula-skill-routing"]
        by_reason: dict[str, int] = {}
        by_skill: dict[str, int] = {}
        for r in route_rows:
            by_reason[r.get("reason") or "?"] = by_reason.get(r.get("reason") or "?", 0) + 1
            by_skill[r.get("outcome") or "?"] = by_skill.get(r.get("outcome") or "?", 0) + 1
        out["skill_routing_24h"] = {
            "total": len(route_rows), "by_match": by_reason,
            "fallthrough_to_reasoning_pct": round(
                100 * by_reason.get("default", 0) / max(1, len(route_rows)), 1),
            "top_skills": dict(sorted(by_skill.items(), key=lambda kv: -kv[1])[:8]),
        }
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
        # VRL coverage/recalibration signal (2026-07-27) — the verified-reasoning layer's proven
        # 52%→88% number came from a one-time offline test harness, not live traffic. 30-day
        # window (not 24h like the router stats above) because verify events are still sparse.
        # reasoning / architecture_planning / commerce_admin / commerce_assistant / finance_admin
        # now carry verification_policy="adversarial" by class default, so this should be
        # accumulating — a low count or a high checker_error rate here IS the finding.
        since30 = (now - timedelta(days=30)).isoformat()
        vrows = (db.table("vula_reasoning_telemetry")
                 .select("verifier,outcome,escalated,created_at")
                 .eq("system", "verified-reasoning")
                 .gte("created_at", since30).order("created_at", desc=True).limit(2000)
                 .execute().data or [])
        by_verifier: dict[str, dict] = {}
        for r in vrows:
            v = r.get("verifier") or "unknown"
            b = by_verifier.setdefault(v, {"total": 0, "accepted": 0, "defect_found": 0, "escalated": 0})
            b["total"] += 1
            if r.get("outcome") == "accepted":
                b["accepted"] += 1
            elif r.get("outcome") == "defect_found":
                b["defect_found"] += 1
            if r.get("escalated"):
                b["escalated"] += 1
        out["vrl_health"] = {
            "window_days": 30, "total_events": len(vrows),
            "last_event_at": vrows[0]["created_at"] if vrows else None,
            "by_verifier": by_verifier,
            "low_coverage": len(vrows) < 20,  # below this, a pass-rate % is noise, not signal
        }
    except Exception as exc:
        out["vrl_health"] = {"error": str(exc)}

    try:
        since = (now - timedelta(hours=24)).isoformat()
        rows = (db.table("vula_webhook_failures").select("*")
                .gte("created_at", since).order("created_at", desc=True).limit(50).execute().data or [])
        out["webhook_failures_24h"] = {"count": len(rows), "recent": rows[:10]}
    except Exception as exc:
        out["webhook_failures_24h"] = {"count": 0, "recent": [], "note": f"{exc} (run migration 099?)"}

    out["migration_state"] = _probe_migrations(db)

    return out


# Recent migrations this session added — a cheap existence probe since there's no formal
# schema_migrations tracking table (migrations are hand-run SQL files). Lets Health flag
# "you probably haven't run 073 yet" instead of tenants silently hitting empty-catch fallbacks.
_MIGRATION_PROBES: list[tuple[str, str, str] | tuple[str, str, str, str]] = [
    ("069", "commerce_scheduled_job_config", "Scheduling tab"),
    ("070", "commerce_order_settings", "Delivery areas/fees"),          # column-only, table always exists
    ("071", "vula_wa_msg_dedup", "Durable WhatsApp dedup"),
    ("072", "vula_admin_audit", "Master audit trail"),
    ("073", "commerce_categories", "Product categories/sale price"),
    ("098", "commerce_geo_cache", "Marketplace-style delivery radius"),   # renumbered from 074
    ("075", "commerce_saved_copy", "Marketing saved-copy library"),
    ("099", "vula_webhook_failures", "Webhook failure feed"),             # renumbered from 076
    ("108", "vula_tenants", "Billing: paid column", "paid"),  # re-adds a column 001 always declared but was never applied
]


def _all_probes() -> list[tuple]:
    """The hand-listed probes above PLUS every boot-time sentinel (vula/startup_checks.py) —
    this list used to stop at 108 while the repo reached 177, so Health showed 'all applied'
    while newer migrations were missing. One list to maintain from now on: add a sentinel."""
    from vula.startup_checks import _SENTINELS
    seen = {p[0] for p in _MIGRATION_PROBES}
    extra = [(num, table, f"{table}.{col}" if col else table, *([col] if col else []))
             for num, table, col in _SENTINELS if num not in seen]
    return sorted(list(_MIGRATION_PROBES) + extra, key=lambda p: p[0])


def _probe_migrations(db) -> list[dict]:
    out = []
    for num, table, note, *rest in _all_probes():
        column = rest[0] if rest else None  # probe a specific column for column-only migrations
        try:
            db.table(table).select(column or "*").limit(1).execute()
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

    # Spend cap (migration 166) — opt-in per tenant, surfaced here so /master's existing cost
    # view shows who's capped/near-capped without a separate screen.
    today = datetime.now(timezone.utc).date().isoformat()
    today_spend: dict[str, float] = {}
    for r in ai:
        if r.get("day") == today:
            today_spend[r["tenant_id"]] = today_spend.get(r["tenant_id"], 0.0) + float(r.get("est_cost_usd") or 0)
    caps = {r["tenant_id"]: r.get("spend_cap_usd") for r in
            (db.table("vula_tenant_config").select("tenant_id,spend_cap_usd").execute().data or [])
            if r.get("spend_cap_usd") is not None}
    for tid, cap in caps.items():
        t = per_tenant.setdefault(tid, {"ai_cost_usd": 0.0, "calls": 0, "infra_cost_usd": 0.0})
        t["spend_cap_usd"] = float(cap)
        t["capped_today"] = today_spend.get(tid, 0.0) >= float(cap)

    # Document/seat plan-limit usage (Phase 4.3) — reuses plan_limits' cap constants so this
    # existing cost view also shows who's near/over their ADVERTISED plan limits (VulaOnboarding
    # .jsx), not just spend. All-time totals, not day-windowed like the AI/infra data above.
    from vula.commerce.plan_limits import SEAT_LIMITS, STARTER_DOCUMENT_LIMIT
    plans = {r["tenant_id"]: (r.get("plan") or "starter").lower() for r in
             (db.table("vula_tenant_config").select("tenant_id,plan").execute().data or [])}
    # Paged: PostgREST caps a response at 1000 rows, so a busy tenant's document count (and the
    # Starter 25-document cap check shown next to it) used to be silently wrong.
    from vula.commerce.ledger import _all_pages
    doc_counts: dict[str, int] = {}
    for r in _all_pages(lambda: db.table("vula_filed_documents").select("tenant_id,id").order("id")):
        doc_counts[r["tenant_id"]] = doc_counts.get(r["tenant_id"], 0) + 1
    seat_counts: dict[str, int] = {}
    for r in _all_pages(lambda: db.table("vula_tenant_users").select("tenant_id,role,user_id")
                        .in_("role", ["owner", "staff"]).order("user_id")):
        seat_counts[r["tenant_id"]] = seat_counts.get(r["tenant_id"], 0) + 1
    for tid, plan in plans.items():
        t = per_tenant.setdefault(tid, {"ai_cost_usd": 0.0, "calls": 0, "infra_cost_usd": 0.0})
        t["doc_count"] = doc_counts.get(tid, 0)
        t["doc_cap"] = STARTER_DOCUMENT_LIMIT if plan == "starter" else None
        t["seat_count"] = seat_counts.get(tid, 0)
        t["seat_cap"] = SEAT_LIMITS.get(plan, 2)

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
    """Create a login for ANY tenant, under the already-enforced require_master gate so master
    can do the same thing cross-tenant that a tenant owner does from their own dashboard.
    Bypasses users.py's own require_tenant_actor dependency (direct function call, not an HTTP
    request) — passes master's identity through explicitly so the merchant audit row still
    attributes the action to the real actor instead of failing on a missing dependency."""
    from vula.api.users import create_user, CreateUserIn
    result = await create_user(tenant_id, CreateUserIn(**(body or {})), identity=identity)
    audit(identity, "master_create_user", tenant_id, email=body.get("email"), role=body.get("role"))
    return result


@router.post("/tenants/{tenant_id}/users/{user_id}/reset")
async def master_reset_user_password(tenant_id: str, user_id: str, identity: dict = Depends(require_master)):
    from vula.api.users import reset_password
    result = await reset_password(tenant_id, user_id, identity=identity)
    audit(identity, "master_reset_password", tenant_id, user_id=user_id)
    return result


@router.delete("/tenants/{tenant_id}/users/{user_id}")
async def master_remove_user(tenant_id: str, user_id: str, identity: dict = Depends(require_master)):
    from vula.api.users import remove_access
    result = await remove_access(tenant_id, user_id, identity=identity)
    audit(identity, "master_remove_user_access", tenant_id, user_id=user_id)
    return result


# ── Shared-knowledge promotion queue ────────────────────────────────────────────
# Depth pass (product owner, 2026-09-17): grow Vula's own shared knowledge base (vula_training /
# business_basics) from its own web research, and let a tenant that opts in share its reviewed
# knowledge with OTHER tenants (vula_network — see vula/training/network.py). Both land in the
# SAME vula_learned_answers table/review queue, just visibly labelled by `source` so a curator
# knows which kind of row they're looking at:
#   - source='web_research': Vula's own web-search synthesis, already passed core/verification.py's
#     two-signal accuracy gate (adversarial verdict == 'pass' + web confidence >= 0.7) before it
#     was even queued — this is the one place `master.py` intentionally reads across ALL tenants,
#     same as /tenants and /audit already do.
#   - source in ('owner_correction', 'escalation'), status='approved': a tenant already reviewed
#     this via Keep/Bin on WhatsApp — promoting to 'network' additionally requires that tenant's
#     own opt-in (vula_tenant_config.share_knowledge_with_network, migration 164).
# Neither path auto-promotes: this queue is always the last human tap before content becomes
# shared knowledge, mirroring migration 150's real leaked-answer incident that proved a review
# gate is necessary even for content a tenant already approved for its own use.

_PROMOTE_TARGETS = ("construction", "business", "network")


def _resolve_promote_target(target: str) -> str:
    if target == "construction":
        from vula.training.content import TRAINING_TENANT_ID
        return TRAINING_TENANT_ID
    if target == "business":
        from vula.training.business_content import BUSINESS_TRAINING_TENANT_ID
        return BUSINESS_TRAINING_TENANT_ID
    if target == "network":
        from vula.training.network import NETWORK_TENANT_ID
        return NETWORK_TENANT_ID
    raise HTTPException(status_code=400,
                        detail=f"unknown target '{target}' (expected one of {sorted(_PROMOTE_TARGETS)})")


@router.get("/learned-answers")
async def master_learned_answers(limit: int = 100):
    """Cross-tenant promotion queue: this tenant's own already-reviewed answers (status=
    'approved') plus Vula's own accuracy-gated research candidates (status='pending' AND
    source='web_research'), not yet promoted into any shared collection. Joined with tenant
    display names so a curator isn't reading raw tenant_ids."""
    db = _client()
    try:
        approved = (db.table("vula_learned_answers").select("*")
                    .eq("status", "approved").is_("promoted_to_shared_kb_at", "null")
                    .order("created_at").limit(limit).execute().data or [])
        research = (db.table("vula_learned_answers").select("*")
                    .eq("status", "pending").eq("source", "web_research")
                    .is_("promoted_to_shared_kb_at", "null")
                    .order("created_at").limit(limit).execute().data or [])
    except Exception as exc:
        return {"candidates": [], "error": f"{exc} (run migration 164?)"}
    rows = (approved + research)[:limit]
    names = {c["tenant_id"]: c.get("display_name") for c in
             (db.table("vula_tenant_config").select("tenant_id,display_name").execute().data or [])}
    for r in rows:
        r["tenant_display_name"] = names.get(r.get("tenant_id"), r.get("tenant_id"))
    return {"candidates": rows}


@router.post("/learned-answers/{learned_id}/promote")
async def master_promote_learned_answer(learned_id: str, body: dict,
                                        identity: dict = Depends(require_master)) -> dict:
    """Ingest one reviewed learned-answer row into a shared KB collection — the human tap that
    turns a candidate into permanent, cross-tenant-servable knowledge. Idempotent: a row that's
    already promoted is a no-op, not a double-ingest."""
    target = (body or {}).get("target", "")
    target_id = _resolve_promote_target(target)

    from vula import escalation as esc
    row = esc.get_learned_answer(learned_id)
    if not row:
        raise HTTPException(status_code=404, detail=f"learned answer '{learned_id}' not found")
    if row.get("promoted_to_shared_kb_at"):
        return {"already_promoted": True, "learned_id": learned_id,
                "promoted_to_shared_kb_at": row["promoted_to_shared_kb_at"]}

    if target == "network":
        # The one place a tenant's own reviewed content can reach ANOTHER tenant — never
        # without that tenant having explicitly opted in (migration 164).
        cfg = (_client().table("vula_tenant_config").select("share_knowledge_with_network")
               .eq("tenant_id", row["tenant_id"]).limit(1).execute().data or [])
        if not (cfg and cfg[0].get("share_knowledge_with_network")):
            raise HTTPException(
                status_code=403,
                detail=f"tenant '{row['tenant_id']}' has not opted in to network sharing")

    from vula.ingestion.pipeline import VulaIngestionPipeline
    pipeline = VulaIngestionPipeline(tenant_id=target_id)
    result = await pipeline.ingest_text(
        content=f"Q: {row['question']}\nA: {row['answer']}",
        filename=f"promoted_{learned_id}.txt", doc_id=f"promoted_{learned_id}",
        # deliberately NOT source_type="learned" (VulaIngestionPipeline._NON_AUTHORITATIVE
        # excludes that tag from authoritative_only=True retrieval — correct for RAW, unreviewed
        # auto-learned chat, but wrong here: this row already passed master review or the
        # automated accuracy gate, so it should actually be retrievable).
        source_type="promoted",
    )
    if result.status != "success":
        raise HTTPException(status_code=502, detail=f"promotion ingest failed: {result.error}")

    now = datetime.now(timezone.utc).isoformat()
    share_scope = "network" if target == "network" else "internal"
    (_client().table("vula_learned_answers").update({
        "promoted_to_shared_kb_at": now, "promoted_by": identity.get("email"),
        "share_scope": share_scope,
    }).eq("id", learned_id).execute())
    audit(identity, "learned_answer_promoted", row.get("tenant_id"),
          learned_id=learned_id, target=target)
    return {"promoted": True, "learned_id": learned_id, "target": target,
            "promoted_to_shared_kb_at": now}


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


# ── Qdrant backup (DR) ──────────────────────────────────────────────────────────

@router.get("/qdrant-backup")
async def master_qdrant_backup_status() -> dict:
    """Per-tenant status from the last daily Qdrant snapshot run (migration 171, see
    docs/dr.md). Surfaced separately from /health since it's the one DR signal an operator
    needs at a glance after touching anything backup-related (bucket limits, Qdrant
    reachability), not just general platform health."""
    rows = (_client().table("vula_qdrant_backup_status").select("*")
            .order("tenant_id").execute().data or [])
    return {"statuses": rows}


@router.post("/qdrant-backup/run")
async def master_run_qdrant_backup(identity: dict = Depends(require_master)) -> dict:
    """Fire the Qdrant snapshot job (vula/integrations/qdrant_backup.py) on demand instead of
    waiting up to 24h for the next scheduled run or restarting the service to force an early
    one — e.g. to confirm a fix (bucket size limit, Qdrant connectivity) actually resolved a
    prior per-tenant failure without waiting a day to find out."""
    from vula.integrations.qdrant_backup import backup_all_tenants
    ok_count = await backup_all_tenants()
    audit(identity, "qdrant_backup.run", ok_count=ok_count)
    rows = (_client().table("vula_qdrant_backup_status").select("*")
            .order("tenant_id").execute().data or [])
    return {"ok_count": ok_count, "statuses": rows}


# ── Model bake-off (evals/harness.py tool-choice layer) ─────────────────────────
# The live eval needs the OpenRouter key, which only the deployed API holds, so the Master
# panel starts it here and each model's report is stored in vula_eval_reports (migration 178).
# Every tool is stubbed and prompts use a no-data sandbox tenant — nothing is read from or sent
# to any real tenant or customer. Choosing new defaults from the results stays a human
# decision (CLOUD_MODEL_BY_TASK / MODEL_WORKER_CLOUD on Railway, then `railway up`).

EVAL_CANDIDATES = [
    "openrouter/meta-llama/llama-3.3-70b-instruct",   # today's model_worker_cloud — the baseline
    "openrouter/google/gemini-2.5-flash",             # today's model_worker_cheap
    "openrouter/anthropic/claude-haiku-4.5",
    "openrouter/anthropic/claude-sonnet-5",
    "openrouter/openai/gpt-5-mini",
    "openrouter/qwen/qwen3-235b-a22b",
]
_EVAL_SKILLS = {"email_admin", "commerce_admin", "commerce_assistant"}
_EVAL_MODEL_RE = re.compile(r"^(openrouter/[\w.\-]+/[\w.\-:]+|ollama_chat/[\w.\-:/]+)$")
_MAX_EVAL_MODELS = 6


@router.get("/evals/candidates")
async def master_eval_candidates() -> dict:
    """Suggested models plus what's configured today, so results can be read against it."""
    from config import settings
    return {
        "candidates": EVAL_CANDIDATES,
        "openrouter_configured": bool(settings.openrouter_api_key),
        "current": {
            "model_worker_cloud": settings.model_worker_cloud,
            "model_worker_cheap": settings.model_worker_cheap,
            "cloud_model_by_task": settings.cloud_model_by_task or "",
        },
    }


@router.post("/evals/tools")
async def master_run_tool_evals(body: dict, identity: dict = Depends(require_master)) -> dict:
    """Queue one tool-choice eval per model; runs in the background, one model after another.
    Poll GET /evals/reports for results."""
    from config import settings
    models = list(dict.fromkeys(str(m).strip() for m in (body.get("models") or []) if str(m).strip()))
    skill = (body.get("skill") or None)
    if not models or len(models) > _MAX_EVAL_MODELS:
        raise HTTPException(status_code=422, detail=f"Pick 1–{_MAX_EVAL_MODELS} models.")
    bad = [m for m in models if not _EVAL_MODEL_RE.match(m)]
    if bad:
        raise HTTPException(status_code=422,
                            detail=f"Use openrouter/<vendor>/<model> or ollama_chat/<model>: {', '.join(bad)}")
    if skill and skill not in _EVAL_SKILLS:
        raise HTTPException(status_code=422, detail=f"skill must be one of {sorted(_EVAL_SKILLS)}")
    if any(m.startswith("openrouter/") for m in models) and not settings.openrouter_api_key:
        raise HTTPException(status_code=400, detail="OPENROUTER_API_KEY is not set on this server.")

    db = _client()
    jobs = []
    for m in models:
        row = (db.table("vula_eval_reports")
               .insert({"model": m, "skill": skill, "status": "running",
                        "created_by": identity.get("email") or identity.get("user_id")})
               .execute().data or [{}])[0]
        if row.get("id"):
            jobs.append((row["id"], m))
    if not jobs:
        raise HTTPException(status_code=500, detail="Couldn't record the run (migration 178 applied?).")
    audit(identity, "evals.run", models=models, skill=skill)

    from vula.commerce.background_tasks import run_background
    run_background("master", "model_bakeoff", _run_eval_batch(jobs, skill))
    return {"queued": [j[0] for j in jobs]}


async def _openrouter_model_ids() -> Optional[set]:
    """OpenRouter's public model list, or None when it can't be fetched (then don't pre-check)."""
    try:
        import httpx
        async with httpx.AsyncClient(timeout=15) as c:
            r = await c.get("https://openrouter.ai/api/v1/models")
            r.raise_for_status()
            return {m.get("id") for m in (r.json().get("data") or []) if m.get("id")}
    except Exception as exc:  # noqa: BLE001
        log.info("OpenRouter model list unavailable, skipping id pre-check: %s", exc)
        return None


async def _run_eval_batch(jobs: list, skill: Optional[str]) -> None:
    from core.llm_router import install_ollama_auth
    from evals import harness
    install_ollama_auth()
    known = await _openrouter_model_ids()
    db = _client()
    def now() -> str:
        return datetime.now(timezone.utc).isoformat()

    for job_id, model in jobs:
        if known is not None and model.startswith("openrouter/") and model.removeprefix("openrouter/") not in known:
            upd = {"status": "failed", "error": "Not an OpenRouter model id (check openrouter.ai/models).",
                   "finished_at": now()}
        else:
            try:
                rep = await harness.run_tools(model, skill)
                upd = {"status": "done", "passed": rep["passed"], "total": rep["total"],
                       "report": rep, "finished_at": now()}
            except Exception as exc:  # noqa: BLE001
                upd = {"status": "failed", "error": f"{type(exc).__name__}: {str(exc)[:300]}",
                       "finished_at": now()}
        try:
            db.table("vula_eval_reports").update(upd).eq("id", job_id).execute()
        except Exception as exc:  # noqa: BLE001
            log.error("eval report %s not saved: %s", job_id, exc)


@router.get("/evals/reports")
async def master_eval_reports(limit: int = 30) -> dict:
    """Latest runs, newest first, with the headline numbers pulled out of each report."""
    try:
        rows = (_client().table("vula_eval_reports").select("*")
                .order("created_at", desc=True).limit(max(1, min(limit, 100))).execute().data or [])
    except Exception as exc:  # noqa: BLE001
        return {"reports": [], "error": f"{exc} (run migration 178?)"}
    out = []
    for r in rows:
        rep = r.get("report") or {}
        out.append({
            **{k: r.get(k) for k in ("id", "model", "skill", "status", "passed", "total", "error",
                                     "created_by", "created_at", "finished_at")},
            "p50_secs": rep.get("p50_secs"), "p95_secs": rep.get("p95_secs"),
            "cost_per_100_usd": rep.get("cost_per_100_usd"), "errors": rep.get("errors"),
            "failures": [{"prompt": x.get("prompt"), "expect": x.get("expect"), "got": x.get("got"),
                          "error": x.get("error")} for x in (rep.get("rows") or []) if not x.get("ok")],
        })
    return {"reports": out}
