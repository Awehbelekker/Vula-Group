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
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from vula.api.master_auth import require_auth, require_master

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
    "estimating": "Estimating (Quick Cost / QS Pro / Takeoff)",
    "ai_draft":   "AI Draft",
    "training":   "Training KB",
}

# Business type → the modules switched on by default at onboarding (master can override).
BUSINESS_TYPES: dict[str, dict] = {
    "food":     {"label": "Food / Restaurant / Takeaway",
                 "modules": ["products", "orders", "payments", "invoices", "delivery",
                             "crm", "reports", "broadcasts", "marketing", "inbox", "team", "automations"]},
    "retail":   {"label": "Retail / E-commerce",
                 "modules": ["products", "orders", "payments", "invoices", "pages",
                             "crm", "reports", "broadcasts", "marketing", "inbox", "team", "automations"]},
    "services": {"label": "Professional services (architecture, agency, consulting)",
                 "modules": ["invoices", "bookings", "projects", "documents", "finances", "fieldops",
                             "followups", "reports", "team", "estimating", "ai_draft", "training", "workspace"]},
    "trades":   {"label": "Trades / Construction / Field work",
                 "modules": ["invoices", "fieldops", "projects", "budget", "scanner",
                             "finances", "followups", "team", "estimating", "ai_draft", "training", "workspace"]},
    "health":   {"label": "Health / Wellness / Bookings",
                 "modules": ["bookings", "invoices", "crm", "followups", "broadcasts", "marketing",
                             "inbox", "reports", "pages", "team"]},
    # 2026-09-29: a sales-rep business (Gerflor Western Cape): no products or orders of its own,
    # no projects — contacts, follow-ups, product knowledge and expense slips.
    "rep":      {"label": "Sales rep / Agency (represents a brand)",
                 "modules": ["crm", "followups", "documents", "team", "reports"]},
    "other":    {"label": "Other / General",
                 "modules": ["invoices", "crm", "reports", "marketing", "followups", "team"]},
}


def valid_business_type(bt: Optional[str]) -> str:
    """A known business type or "other" — signup used to store any free-text string, which then
    matched no preset and no routing rule."""
    bt = (bt or "").strip().lower()
    return bt if bt in BUSINESS_TYPES else "other"


def valid_modules(mods) -> list:
    return [m for m in (mods or []) if isinstance(m, str) and m in MODULES]


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
        if isinstance(hit, tuple) and (time.time() - hit[0]) < _TTL:
            return hit[1]
    try:
        rows = (_client().table("vula_tenant_config").select("*")
                .eq("tenant_id", tenant_id).limit(1).execute().data or [])
        cfg = rows[0] if isinstance(rows, list) and rows else {}
        if not isinstance(cfg, dict):
            cfg = {}
    except Exception as exc:
        # Never cache a failed read: a Supabase blip would otherwise make a real tenant look
        # unconfigured (no modules, every tool, uses_projects) for the whole TTL.
        log.debug("tenant_config read failed (run migration 040?): %s", exc)
        hit = _CACHE.get(tenant_id)
        return hit[1] if isinstance(hit, tuple) else {}
    _CACHE[tenant_id] = (time.time(), cfg)
    return cfg


def is_active(tenant_id: str) -> bool:
    """Whether a tenant is allowed to operate (bot replies, checkout, dashboard login).

    Absence of a row or an unset `active` column means active — `active` only exists to be
    explicitly flipped False by master's Suspend action, so anything else defaults open.
    """
    return get_config(tenant_id).get("active") is not False


def invalidate(tenant_id: str) -> None:
    """Drop a tenant's cached config so a write from outside this module (e.g. master's
    suspend/reactivate PATCH) is visible immediately instead of after the 60s TTL."""
    _CACHE.pop(tenant_id, None)


def ensure_billing_row(slug: str, company_name: str, *, email: Optional[str] = None,
                       contact_name: Optional[str] = None, plan: str = "starter",
                       trial_days: Optional[int] = None) -> None:
    """Make sure the tenant has a vula_tenants billing row keyed by its slug (workspace_slug).
    Idempotent and best-effort — never blocks tenant creation (migration 175 must be applied for
    rows without an email/contact). trial_days=None leaves trial_ends empty (master-created
    tenants: billing is agreed directly, so no automated trial-expiry emails)."""
    try:
        db = _client()
        if db.table("vula_tenants").select("tenant_id").eq("workspace_slug", slug).limit(1).execute().data:
            return
        row = {"company_name": company_name or slug, "workspace_slug": slug, "status": "active",
               "plan": plan if plan in ("starter", "growth", "business") else "starter",
               "email": email, "contact_name": contact_name}
        if trial_days:
            from datetime import timedelta
            row["trial_ends"] = (datetime.now(timezone.utc) + timedelta(days=trial_days)).isoformat()
        db.table("vula_tenants").insert({k: v for k, v in row.items() if v is not None}).execute()
    except Exception as exc:
        log.warning("billing row for %s not created (run migration 175?): %s", slug, exc)


def display_name(tenant_id: str) -> str:
    """The business name customers should see — never another tenant's. Falls back to a
    title-cased slug ("my-shop" -> "My Shop")."""
    try:
        name = (get_config(tenant_id) or {}).get("display_name")
    except Exception:
        name = None
    return (name or (tenant_id or "").replace("-", " ").title() or "us").strip()


def store_url(tenant_id: str) -> Optional[str]:
    cfg = get_config(tenant_id)
    if cfg.get("store_url"):
        return cfg["store_url"]
    from config import settings
    return settings.store_urls.get(tenant_id)


def _effective_modules(cfg: dict) -> list:
    """A tenant's configured modules, plus a few that are factual capabilities rather than a
    business-type judgment call — they shouldn't depend on which preset a tenant happened to
    onboard under:
    - `pages`, whenever they have a live Vula-built site (the `food` preset never included it,
      which is why off-the-hook's Storefront tab was invisible despite offthehook.co.za being a
      real, fully Puck-editable Vula site).
    - `documents`, always. `documents` was only ever in the "services" preset (construction docs),
      but every tenant already files documents via WhatsApp/email/dashboard-upload regardless of
      business type — the Documents tab (customer linkage, media library, Drive import) is a
      universal capability, not something food/retail/health/trades tenants should have hidden.
    """
    mods = list(cfg.get("modules") or [])
    if cfg.get("store_url") and "pages" not in mods:
        mods.append("pages")
    if "documents" not in mods:
        mods.append("documents")
    return mods


def enabled_modules(tenant_id: str) -> list:
    return _effective_modules(get_config(tenant_id))


def uses_projects(tenant_id: str) -> bool:
    """Does this business work in projects (jobs/sites)? Only when it has the `projects`
    module. 2026-09-28 (Ian): "DIGG has projects because it's construction; Off the Hook is
    a pure commerce business — client-to-client deals, not projects." Off the Hook had 262 of
    its 264 documents stuck as "which project?". Unknown/failed lookup → True, the behaviour
    before this existed."""
    try:
        cfg = get_config(tenant_id)
    except Exception:
        return True
    if not cfg:
        return True
    return "projects" in _effective_modules(cfg)


# ── Tenant profile: ONE answer to "what kind of business is this?" (2026-09-29) ──────────────
# Before this, six signals decided tenant behaviour separately (business_type, modules,
# uses_projects, a hardcoded WhatsApp route map, four role sets, hardcoded tenant ids) and they
# contradicted each other: Gerflor (a flooring sales rep) was typed "trades" and got architecture
# routing, the construction starter KB and a shop Home; DIGG's owner was offered order/stock
# tools. Callers ask the profile instead of re-deriving it.
_SELLS = {"products", "orders"}


def tenant_profile(tenant_id: str) -> dict:
    cfg = get_config(tenant_id) or {}
    mods = _effective_modules(cfg) if cfg else []
    btype = (cfg.get("business_type") or "other") if cfg else None
    aliases = [a for a in (cfg.get("aliases") or []) if isinstance(a, str) and a.strip()]
    return {
        "tenant_id": tenant_id,
        "known": bool(cfg),
        "display_name": cfg.get("display_name") or display_name(tenant_id),
        "business_type": btype,
        "modules": mods,
        "uses_projects": uses_projects(tenant_id),
        "sells_products": bool(_SELLS & set(mods)) if cfg else True,
        "is_rep_business": btype == "rep",
        "aliases": aliases,
        "description": cfg.get("description") or "",
    }


def what_i_do(tenant_id: str) -> str:
    """One line a customer or new staff member can be told — built from the profile, never
    another tenant's pitch ("I'm Vula, your construction AI" used to go to everyone)."""
    p = tenant_profile(tenant_id)
    name = p["display_name"]
    if p["description"]:
        return f"{name} — {p['description']}"
    if p["is_rep_business"]:
        return f"{name}: product information, samples, pricing questions and follow-ups."
    if p["uses_projects"]:
        return f"{name}: projects, documents, quotes and invoices."
    if p["sells_products"]:
        return f"{name}: products, orders, delivery and payments."
    return f"{name}: questions, bookings and follow-ups."


def _public(cfg: dict) -> dict:
    """Storefront/dashboard-safe subset (no internal columns)."""
    return {
        "tenant_id": cfg.get("tenant_id"), "display_name": cfg.get("display_name"),
        "business_type": cfg.get("business_type"), "theme": cfg.get("theme") or {},
        "store_url": cfg.get("store_url"), "modules": _effective_modules(cfg),
        "default_payment_provider": cfg.get("default_payment_provider"),
        "status": cfg.get("status"),
        "profile": tenant_profile(cfg["tenant_id"]) if cfg.get("tenant_id") else None,
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


@router.get("/ai-spend", dependencies=[Depends(require_master)])
async def ai_spend(days: int = 14) -> dict:
    """AI/LLM spend (COGS) across all tenants — from vula_ai_usage. Master-only (verified JWT)
    since 2026-07-16 — was previously open to any caller."""
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


def tenant_for_host(host: str) -> Optional[str]:
    """Which business a dashboard address belongs to, so the login page shows its brand before
    anyone signs in. Checks, in order: the tenant's own `domains`; its store's host with an
    admin./app./dashboard. prefix (admin.offthehook.co.za → offthehook.co.za); then
    <slug>.vula-ai.com. None for the apex, master or preview hosts."""
    h = (host or "").strip().lower().split(":")[0]
    if not h:
        return None
    bare = h
    for pre in ("admin.", "app.", "dashboard.", "www."):
        if bare.startswith(pre):
            bare = bare[len(pre):]
            break
    try:
        rows = _client().table("vula_tenant_config").select("tenant_id,domains,store_url").limit(500).execute().data or []
    except Exception as exc:
        log.debug("tenant_for_host lookup failed: %s", exc)
        rows = []
    for r in rows:
        doms = {str(d).lower().strip() for d in (r.get("domains") or []) if d}
        if h in doms or bare in doms:
            return r["tenant_id"]
    import re as _re
    for r in rows:
        store = _re.sub(r"^https?://", "", (r.get("store_url") or "").lower()).split("/")[0]
        store = store[4:] if store.startswith("www.") else store
        if store and store == bare and h != bare:          # only the admin./app. host, not the shop
            return r["tenant_id"]
    if bare.endswith(".vula-ai.com"):
        slug = bare[: -len(".vula-ai.com")]
        ids = {r["tenant_id"] for r in rows}
        for cand in (slug, f"{slug}-demo"):
            if cand in ids:
                return cand
    return None


@router.get("/by-domain")
async def get_tenant_by_domain(host: str) -> dict:
    """Public: the tenant a dashboard hostname belongs to (no secrets — just the id), for a
    branded login page."""
    return {"tenant_id": tenant_for_host(host)}


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
async def create_tenant(body: TenantIn, identity: dict = Depends(require_master)) -> dict:
    """Seed a tenant from a business type — modules auto-enabled from the preset. Master-only
    (verified JWT) since 2026-07-16 — was previously open to any caller."""
    btype = valid_business_type(body.business_type)
    preset = BUSINESS_TYPES[btype]
    row = {
        "tenant_id": body.tenant_id, "display_name": body.display_name or body.tenant_id,
        "business_type": btype, "store_url": body.store_url,
        "modules": preset["modules"], "plan": body.plan or "starter",
        "status": "active", "updated_at": _now(),
    }
    try:
        existing = (_client().table("vula_tenant_config").select("tenant_id")
                    .eq("tenant_id", body.tenant_id).limit(1).execute().data or [])
        if existing:
            # "+ New tenant" with a slug that's already taken used to silently overwrite that
            # tenant's modules/plan/business type. Editing goes through PATCH /v1/master/tenants.
            raise HTTPException(status_code=409, detail=f"'{body.tenant_id}' already exists — "
                                                        "edit it from its tenant page instead.")
        _client().table("vula_tenant_config").insert(row).execute()
    except HTTPException:
        raise
    except Exception as exc:
        return {"error": f"{exc} (run migration 040?)"}
    ensure_billing_row(body.tenant_id, body.display_name or body.tenant_id, plan=body.plan or "starter")
    _CACHE.pop(body.tenant_id, None)
    if not existing:
        # 2026-08-28: widen starter_kb seeding to this (master-created) tenant path — previously
        # only signup.py's self-serve flow got starter KB docs, leaving master-created tenants
        # with an empty KB on day one. Same fire-and-forget, best-effort shape as signup.py —
        # never blocks the response. Gated on first-creation only (not the update branch above)
        # so a routine master edit never wastes an LLM call re-generating starter content.
        try:
            from vula.commerce.background_tasks import run_background
            from vula.commerce.starter_kb import seed_starter_kb
            run_background(body.tenant_id, "starter_kb_seed",
                            seed_starter_kb(body.tenant_id, btype))
        except Exception as exc:
            log.debug("starter_kb seeding skipped for %s: %s", body.tenant_id, exc)
    try:
        from vula.api.master import audit
        audit(identity, "tenant_created", body.tenant_id, business_type=body.business_type)
    except Exception:
        pass
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
async def patch_tenant(tenant_id: str, body: TenantPatch,
                       identity: dict = Depends(require_master)) -> dict:
    """Master-only (verified JWT) since 2026-07-16 — was previously open to any caller."""
    patch = {k: v for k, v in body.model_dump().items() if v is not None}
    if not patch:
        return {"tenant": _public(get_config(tenant_id, fresh=True))}
    patch["updated_at"] = _now()
    try:
        _client().table("vula_tenant_config").update(patch).eq("tenant_id", tenant_id).execute()
    except Exception as exc:
        return {"error": f"{exc} (run migration 040?)"}
    _CACHE.pop(tenant_id, None)
    try:
        from vula.api.master import audit
        audit(identity, "tenant_updated", tenant_id, patch=patch)
    except Exception:
        pass
    return {"tenant": _public(get_config(tenant_id, fresh=True))}


# ── Home: the cards each business chooses (2026-09-29) ────────────────────────
# Ian: tenants had no way to "customise what they want to view". Home was built for online
# orders, so a project business (DIGG) saw mostly zeros. The dashboard renders these ids in
# this order (VulaMerchantAdmin OverviewTab); unknown ids are dropped, so an old saved layout
# never breaks the page.
HOME_CARDS = ("today", "checklist", "attention", "sales", "trend", "jobcosting", "crosscheck",
              "customers", "assistant")
_HOME_DEFAULT_SHOP = ["checklist", "attention", "sales", "trend", "customers", "assistant"]
_HOME_DEFAULT_PROJECTS = ["checklist", "attention", "jobcosting", "crosscheck", "assistant"]
_HOME_DEFAULT_REP = ["today", "checklist", "attention", "assistant"]
_HOME_DEFAULT_GENERAL = ["checklist", "attention", "crosscheck", "assistant"]


def home_default(tenant_id: str) -> list:
    p = tenant_profile(tenant_id)
    if p["is_rep_business"]:
        return list(_HOME_DEFAULT_REP)
    if p["uses_projects"]:
        return list(_HOME_DEFAULT_PROJECTS)
    if p["sells_products"]:
        return list(_HOME_DEFAULT_SHOP)
    return list(_HOME_DEFAULT_GENERAL)


def _clean_cards(cards) -> list:
    out = []
    for c in cards or []:
        if isinstance(c, str) and c in HOME_CARDS and c not in out:
            out.append(c)
    return out


@router.get("/{tenant_id}/home", dependencies=[Depends(require_auth)])
async def get_home_layout(tenant_id: str) -> dict:
    saved = (get_config(tenant_id).get("home_layout") or {})
    cards = _clean_cards(saved.get("cards") if isinstance(saved, dict) else None)
    default = home_default(tenant_id)
    return {"cards": cards or default, "default": default, "custom": bool(cards),
            "available": list(HOME_CARDS)}


class HomeLayoutIn(BaseModel):
    cards: Optional[list] = None      # None or [] → back to the default


@router.put("/{tenant_id}/home", dependencies=[Depends(require_auth)])
async def put_home_layout(tenant_id: str, body: HomeLayoutIn) -> dict:
    cards = _clean_cards(body.cards)
    try:
        (_client().table("vula_tenant_config")
         .update({"home_layout": {"cards": cards} if cards else None, "updated_at": _now()})
         .eq("tenant_id", tenant_id).execute())
    except Exception as exc:
        raise HTTPException(503, f"Could not save the Home layout (run migration 186?): {exc}")
    _CACHE.pop(tenant_id, None)
    return await get_home_layout(tenant_id)
