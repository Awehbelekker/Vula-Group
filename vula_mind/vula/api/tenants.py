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
    # 2026-10-13: photo cards in WhatsApp, tap to order (colour, then how many), the shop's web
    # page with a basket that orders back into WhatsApp — vula/commerce/web_basket.py.
    "wa_shop":    "WhatsApp shop (photo cards, tap to order, web basket)",
    # 2026-10-14: materials, recipes, batches and the true cost per unit — vula/commerce/costing.py.
    "manufacturing": "Manufacturing & costing (recipes, batches, cost per unit, margin)",
    # 2026-11-10: properties for sale / to rent, agents, website enquiries — vula/listings/.
    "listings":   "Property listings (real estate)",
    # 2026-11-10: deals board for any business type (stages per business type) — vula/pipeline/.
    "pipeline":   "Deals pipeline",
    # 2026-11-24: restaurants — table bookings and today's specials — vula/commerce/tables.py.
    "tables":     "Table bookings & specials (restaurants)",
    # 2026-11-24: surf and kite schools, gear hire — requests the business confirms by hand,
    # quoted from its own price list — vula/commerce/lessons.py.
    "lessons":    "Lessons & gear hire (surf, kite and other schools)",
}

# Business type → the modules switched on by default at onboarding (master can override).
# 12 Nov 2026: every type gets "documents". A food, retail or health business had no Documents
# tab, so no way to give Vula its price list, policies or menu — and Vula can only answer a
# business's own facts from what the business has given it.
BUSINESS_TYPES: dict[str, dict] = {
    "food":     {"label": "Food / Restaurant / Takeaway",
                 "modules": ["products", "orders", "payments", "invoices", "delivery", "wa_shop",
                             "crm", "reports", "broadcasts", "marketing", "inbox", "team", "automations",
                             "documents"]},
    "retail":   {"label": "Retail / E-commerce",
                 "modules": ["products", "orders", "payments", "invoices", "pages", "wa_shop",
                             "crm", "reports", "broadcasts", "marketing", "inbox", "team", "automations",
                             "documents"]},
    "services": {"label": "Professional services (architecture, agency, consulting)",
                 "modules": ["invoices", "bookings", "projects", "documents", "finances", "fieldops",
                             "followups", "reports", "team", "estimating", "ai_draft", "training", "workspace"]},
    "trades":   {"label": "Trades / Construction / Field work",
                 "modules": ["invoices", "fieldops", "projects", "budget", "scanner",
                             "finances", "followups", "team", "estimating", "ai_draft", "training", "workspace",
                             "documents"]},
    "health":   {"label": "Health / Wellness / Bookings",
                 "modules": ["bookings", "invoices", "crm", "followups", "broadcasts", "marketing",
                             "inbox", "reports", "pages", "team", "documents"]},
    # 2026-09-29: a sales-rep business (Gerflor Western Cape): no products or orders of its own,
    # no projects — contacts, follow-ups, product knowledge and expense slips.
    "rep":      {"label": "Sales rep / Agency (represents a brand)",
                 "modules": ["crm", "followups", "documents", "team", "reports"]},
    # 2026-10-06 (finance brief, capability 7): a one-person business that only needs the money
    # side — quotes, invoices, payment links, expenses, books. Nothing else switched on.
    "solo":     {"label": "Solo operator (invoices & money only)",
                 "modules": ["invoices", "payments", "finances", "documents", "crm", "reports"]},
    # 2026-11-10: estate agencies and letting agents — listings, agents, enquiries, viewings
    # (bookings), a website to show the listings on, and follow-ups on every lead.
    "real_estate": {"label": "Real estate / Estate agency / Rentals",
                    "modules": ["listings", "pipeline", "bookings", "crm", "followups", "pages", "inbox",
                                "broadcasts", "marketing", "documents", "team", "reports", "invoices"]},
    # 2026-11-24: the light plans — one job each, none of the rest. A restaurant that only wants
    # bookings and specials; an organiser who only sells tickets. Both can move to a full type later
    # with nothing to migrate (the modules are switches, the data is the same). Documents stays on
    # for both (the 12 Nov rule): the menu or the event's terms is what Vula answers customers from.
    # A restaurant also gets Products (its menu) and, through `tables`, Tap to Pay: waiters, table
    # tags, calling and ordering from the table.
    "restaurant_lite": {"label": "Restaurant: table bookings & specials only",
                        "modules": ["tables", "products", "inbox", "crm", "team", "documents"]},
    "events_lite": {"label": "Events: tickets & the door only",
                    "modules": ["orders", "payments", "crm", "team", "reports", "documents"]},
    # 2026-11-24 (FlowCrew; first business Atlantic Surf Co): a surf or kite school with a shop —
    # lessons and gear hire asked for on WhatsApp and confirmed by a person, the shop's products,
    # the till, payments and invoices for the lessons.
    "watersports": {"label": "Surf / kite / water sports school, shop & gear hire",
                    "modules": ["lessons", "products", "orders", "payments", "invoices", "wa_shop", "crm",
                                "inbox", "broadcasts", "marketing", "reports", "team", "automations",
                                "documents", "pages"]},
    "other":    {"label": "Other / General",
                 "modules": ["invoices", "crm", "reports", "marketing", "followups", "team", "documents"]},
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
    if p["business_type"] == "real_estate":
        return f"{name}: properties for sale and to rent, viewings and enquiries."
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
        # the redesigned dashboard, opt-in per tenant, and its own labels (migration 215)
        "ui_v2": bool(cfg.get("ui_v2")),
        "ui_labels": cfg.get("ui_labels") or None,
        # the switches that are on for this business (names only) — the dashboard shows a
        # piloted screen such as Costing by trade only where it's on (15 Nov 2026)
        "features": _features_on(cfg.get("tenant_id")),
        "partner": partner_of(cfg),
    }


# 2026-11-24: a business brought to Vula by a partner that sells it under its own name (FlowCrew:
# surf and kite schools). The business's own brand stays first on its dashboard; the partner is
# named in the credit at the foot — "FlowCrew · Powered by Vula". Set per business in
# vula_tenant_config.features.partner (an id from this list, beside the switches).
# The partner's own look (FlowCrew's, from its design system: ocean blue, fresh green, its ink) is
# what a business it brought wears until the business sets its own in Settings › Brand kit — the
# business's own colour, logo and name always win. The logo itself is a file the dashboard ships
# (public/partners/<id>-mark.png).
PARTNERS: dict[str, dict] = {
    "flowcrew": {"name": "FlowCrew",
                 "brand": {"accent_color": "#2196F3", "secondary_color": "#7ED321", "ink_color": "#1E2A39"},
                 # the partner's own web address (24 Nov 2026): its businesses sign in there and its
                 # customers' app lives there, under the partner's name and look
                 "domain": "flowcrew.co.za"},
}


def partner_for_host(host: str) -> Optional[dict]:
    """The partner a web address belongs to — its own domain or any name under it
    (app.flowcrew.co.za) — as {"id", "name", "brand"}; None for every other address."""
    h = (host or "").strip().lower().split(":")[0].rstrip(".")
    for pid, p in PARTNERS.items():
        d = p.get("domain")
        if d and (h == d or h.endswith("." + d)):
            return {"id": pid, "name": p["name"], "brand": dict(p.get("brand") or {})}
    return None


def partner_of(cfg: Optional[dict]) -> Optional[dict]:
    """{"id", "name"} of the partner this business came through, or None. An id that isn't a
    known partner is nobody."""
    features = (cfg or {}).get("features")
    pid = features.get("partner") if isinstance(features, dict) else None
    return {"id": pid, "name": PARTNERS[pid]["name"]} if isinstance(pid, str) and pid in PARTNERS else None


def partner_brand(tenant_id: Optional[str]) -> dict:
    """The colours a business wears when it has set none of its own: its partner's, else {}."""
    try:
        partner = partner_of(get_config(tenant_id)) if tenant_id else None
    except Exception:  # noqa: BLE001 — the brand must still load: Vula's own defaults
        partner = None
    return dict(PARTNERS[partner["id"]].get("brand") or {}) if partner else {}


def _features_on(tenant_id: Optional[str]) -> list:
    if not tenant_id:
        return []
    try:
        from vula import features
        return [name for name in features.FEATURES if features.on(tenant_id, name)]
    except Exception:  # noqa: BLE001 — no switches shown rather than a broken tenant read
        return []


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


# Businesses whose dashboard address predates `domains` (mirrors vula_dashboard/src/theme/tenantThemes.js).
_KNOWN_DASHBOARDS = {
    "off-the-hook": "https://admin.offthehook.co.za",
    "digg-demo": "https://digg.vula-ai.com",
}
_DASHBOARD_PREFIXES = ("admin.", "app.", "dashboard.")


def dashboard_url_for(tenant_id: str) -> str:
    """Where this business's people log in — sent in the WhatsApp team welcome and the signup
    email. 15 Oct 2026 (Ian: every business gets <name>.vula-ai.com): the tenant's own dashboard
    host from `domains` once it's live (added there only after it's on Vercel and its DNS works),
    then the two older addresses, then the shared dashboard. Shop hosts in `domains`
    (shop./order. — shop_domain.py) are never offered as the dashboard."""
    from config import settings
    try:
        doms = (get_config(tenant_id) or {}).get("domains") or []
    except Exception:
        doms = []
    for d in doms:
        host = str(d or "").strip().lower()
        if host.endswith(".vula-ai.com") or host.startswith(_DASHBOARD_PREFIXES):
            return f"https://{host}"
    return _KNOWN_DASHBOARDS.get(tenant_id) or settings.dashboard_url.rstrip("/")


def dashboard_link(tenant_id: str, page: str) -> str:
    """A link from WhatsApp straight to one page of this business's dashboard, e.g. its Invoices:
    https://<dashboard>/#/go/invoices. `page` is a dashboard page id (vula_dashboard/src/
    navConfig.jsx). The dashboard keeps it through the login and opens it if this person may see
    that page. Sent as a raw URL — WhatsApp doesn't render markdown links."""
    return f"{dashboard_url_for(tenant_id)}/#/go/{page}"


@router.get("/by-domain")
async def get_tenant_by_domain(host: str) -> dict:
    """Public: the tenant a dashboard hostname belongs to (no secrets — just the id), for a
    branded login page; or the partner whose own address it is (name and colours)."""
    partner = partner_for_host(host)
    # a partner's own address is nobody's business in particular: the login wears the partner's look
    return {"tenant_id": None if partner else tenant_for_host(host), "partner": partner}


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
    website: Optional[str] = None      # the business's own site: brand + import (website_setup.py)


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
    out = {"tenant": _public(row)}
    if (body.website or "").strip():
        # 5 Nov 2026: one address sets the brand and starts the website import (products,
        # pages, profile drafts). A site that can't be read never fails the new business.
        try:
            from vula.commerce import website_setup
            out["website"] = await website_setup.start(body.tenant_id, body.website)
        except Exception as exc:
            out["website"] = {"error": str(exc)[:200]}
    return out


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
HOME_CARDS = ("today", "checklist", "attention", "sales", "trend", "shop", "jobcosting", "crosscheck",
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
        cards = list(_HOME_DEFAULT_SHOP)
        if "wa_shop" in (p.get("modules") or []):      # the WhatsApp shop's funnel, after the trend
            cards.insert(cards.index("trend") + 1, "shop")
        return cards
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
