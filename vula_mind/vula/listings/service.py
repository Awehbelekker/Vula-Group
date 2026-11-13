"""
vula/listings/service.py — property listings, agents and enquiries (migration 257).

For real-estate businesses (business_type 'real_estate'): the business lists properties for sale
or to rent, shows its agents, and takes enquiries from its website. Viewings reuse the bookings
system; this module only records the enquiry and tells the right people on WhatsApp.

Rules kept here, not in the API layer, so WhatsApp tools can reuse them later:
  - every query is scoped by tenant_id (the tables are service-role only);
  - money is integer cents, parsed server-side ("R2.4m", "2 400 000", 2400000 → cents);
  - the public view never carries a street address unless the business ticked show_address,
    and never carries draft or archived listings.
"""
from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

log = logging.getLogger(__name__)

PROPERTIES = "vula_properties"
AGENTS = "vula_property_agents"
ENQUIRIES = "vula_property_enquiries"

LISTING_TYPES = ("sale", "rent")
STATUSES = ("draft", "active", "under_offer", "sold", "let", "archived")
PUBLIC_STATUSES = ("active", "under_offer")           # what the website shows by default
SOLD_STATUSES = ("sold", "let")                        # shown only when asked ("recently sold")
PROPERTY_TYPES = ("house", "apartment", "townhouse", "land", "commercial", "farm", "other")
ENQUIRY_STATUSES = ("new", "contacted", "viewing", "closed")

# Never sent to the public website.
_PRIVATE_FIELDS = ("address", "latitude", "longitude")

_PROPERTY_FIELDS = (
    "title", "listing_type", "status", "property_type", "price_cents", "price_on_request",
    "bedrooms", "bathrooms", "garages", "parking", "floor_m2", "erf_m2", "suburb", "city",
    "province", "address", "show_address", "latitude", "longitude", "description", "features",
    "images", "levies_cents", "rates_cents", "available_from", "agent_id", "featured", "sort",
)
_AGENT_FIELDS = ("name", "title", "whatsapp", "email", "photo_url", "bio", "ppra_ffc", "active", "sort")


def _client():
    from vula.commerce import service as cs
    return cs._client()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# ── Parsing helpers (pure) ────────────────────────────────────────────────────

def slugify(text: str) -> str:
    s = re.sub(r"[^\w\s-]", "", (text or "").lower()).strip()
    s = re.sub(r"[\s_-]+", "-", s).strip("-")
    return s[:60] or "property"


_MONEY_RE = re.compile(r"^\s*r?\s*([\d\s,]*\.?\d+)\s*(m|mil|million|k|thousand)?\s*$", re.IGNORECASE)


def parse_rands_to_cents(value: Any) -> Optional[int]:
    """A price as typed by an agent → integer cents, or None when blank / unreadable.

    Integers and floats are rands ("2400000" means R2.4 million — nobody types listing prices in
    cents). Strings may carry an "R", spaces or commas as thousands separators, and an m/k suffix:
    "R2.4m" → 240_000_000, "R 1 250 000" → 125_000_000, "8 500" → 850_000. Never guesses: anything
    else is None, so the caller can ask instead of listing a wrong price."""
    if value is None or value == "" or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return int(round(float(value) * 100)) if value >= 0 else None
    m = _MONEY_RE.match(str(value))
    if not m:
        return None
    number = m.group(1).replace(" ", "")
    suffix = (m.group(2) or "").lower()
    if "," in number:
        # Commas are thousands separators only in groups of three ("1,250,000"); a lone comma
        # like "2,4" is ambiguous (decimal comma?) — refuse rather than guess.
        if not re.fullmatch(r"\d{1,3}(,\d{3})+(\.\d+)?", number):
            return None
        number = number.replace(",", "")
    try:
        rands = float(number)
    except ValueError:
        return None
    if suffix in ("m", "mil", "million"):
        rands *= 1_000_000
    elif suffix in ("k", "thousand"):
        rands *= 1_000
    return int(round(rands * 100))


def _int_or_none(v: Any) -> Optional[int]:
    if v is None or v == "":
        return None
    try:
        return int(round(float(v)))
    except (TypeError, ValueError):
        return None


def _num_or_none(v: Any) -> Optional[float]:
    if v is None or v == "":
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _str_list(v: Any) -> List[str]:
    if isinstance(v, str):
        v = [p for p in re.split(r"[,\n]", v)]
    if not isinstance(v, list):
        return []
    return [s.strip() for s in v if isinstance(s, str) and s.strip()]


def clean_property(body: Dict[str, Any]) -> Dict[str, Any]:
    """Validate + normalise a property from the dashboard / API. Only known fields survive.
    Raises ValueError with a message a person can act on."""
    row: Dict[str, Any] = {k: body[k] for k in _PROPERTY_FIELDS if k in body}
    if "title" in row:
        row["title"] = (row["title"] or "").strip()[:160]
        if not row["title"]:
            raise ValueError("A listing needs a title, e.g. '3 bedroom house in Durbanville'.")
    if "listing_type" in row and row["listing_type"] not in LISTING_TYPES:
        raise ValueError("listing_type must be 'sale' or 'rent'.")
    if "status" in row and row["status"] not in STATUSES:
        raise ValueError(f"status must be one of {', '.join(STATUSES)}.")
    if "property_type" in row and row["property_type"]:
        pt = str(row["property_type"]).strip().lower()
        row["property_type"] = pt if pt in PROPERTY_TYPES else "other"
    for money in ("price_cents", "levies_cents", "rates_cents"):
        if money in row and row[money] is not None and row[money] != "":
            cents = _int_or_none(row[money])
            if cents is None or cents < 0:
                raise ValueError(f"{money} must be a whole number of cents.")
            row[money] = cents
        elif money in row:
            row[money] = None
    # Rands as typed ("R2.4m") are accepted on the way in and converted here, server-side.
    for rands_key, cents_key in (("price", "price_cents"), ("levies", "levies_cents"), ("rates", "rates_cents")):
        if rands_key in body and cents_key not in body:
            cents = parse_rands_to_cents(body[rands_key])
            if body[rands_key] not in (None, "") and cents is None:
                raise ValueError(f"Couldn't read the {rands_key} '{body[rands_key]}' — try e.g. R2 450 000.")
            row[cents_key] = cents
    for k in ("bedrooms", "bathrooms", "latitude", "longitude"):
        if k in row:
            row[k] = _num_or_none(row[k])
    for k in ("garages", "parking", "floor_m2", "erf_m2", "sort"):
        if k in row:
            row[k] = _int_or_none(row[k])
    for k in ("features", "images"):
        if k in row:
            row[k] = _str_list(row[k])[:60]
    for k in ("price_on_request", "show_address", "featured"):
        if k in row:
            row[k] = bool(row[k])
    for k in ("suburb", "city", "province", "address", "description"):
        if k in row and row[k] is not None:
            row[k] = str(row[k]).strip() or None
    if "available_from" in row and not row["available_from"]:
        row["available_from"] = None
    if "agent_id" in row and not row["agent_id"]:
        row["agent_id"] = None
    return row


def public_view(prop: Dict[str, Any], agent: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """What a website visitor may see of a listing."""
    out = {k: v for k, v in prop.items() if k not in _PRIVATE_FIELDS}
    if prop.get("show_address"):
        # The exact spot is as private as the street address: the map pin goes with it.
        out["address"] = prop.get("address")
        out["latitude"], out["longitude"] = prop.get("latitude"), prop.get("longitude")
    if prop.get("price_on_request"):
        out["price_cents"] = None
    if agent:
        out["agent"] = {k: agent.get(k) for k in ("id", "name", "title", "whatsapp", "email",
                                                  "photo_url", "ppra_ffc")}
    return out


def format_price(prop: Dict[str, Any]) -> str:
    """'R2 450 000', 'R12 500 pm', or 'Price on request' — for WhatsApp alerts."""
    cents = prop.get("price_cents")
    if prop.get("price_on_request") or cents is None:
        return "Price on request"
    rands = int(cents) // 100
    text = "R" + f"{rands:,}".replace(",", " ")
    return text + (" pm" if prop.get("listing_type") == "rent" else "")


# ── Agents ────────────────────────────────────────────────────────────────────

async def list_agents(tenant_id: str, active_only: bool = True) -> List[dict]:
    q = _client().table(AGENTS).select("*").eq("tenant_id", tenant_id)
    if active_only:
        q = q.eq("active", True)
    try:
        return q.order("sort").order("name").execute().data or []
    except Exception as exc:
        log.debug("list_agents skipped (run migration 257?): %s", exc)
        return []


async def upsert_agent(tenant_id: str, body: Dict[str, Any]) -> dict:
    row = {k: body[k] for k in _AGENT_FIELDS if k in body}
    row["name"] = (row.get("name") or "").strip()[:120]
    if not row["name"]:
        raise ValueError("An agent needs a name.")
    if "sort" in row:
        row["sort"] = _int_or_none(row["sort"]) or 0
    row["updated_at"] = _now()
    if body.get("id"):
        res = (_client().table(AGENTS).update(row)
               .eq("tenant_id", tenant_id).eq("id", body["id"]).execute())
        if not res.data:
            raise LookupError("agent not found")
        return res.data[0]
    res = _client().table(AGENTS).insert({**row, "tenant_id": tenant_id}).execute()
    return (res.data or [row])[0]


async def delete_agent(tenant_id: str, agent_id: str) -> None:
    _client().table(AGENTS).delete().eq("tenant_id", tenant_id).eq("id", agent_id).execute()


async def _agent(tenant_id: str, agent_id: Optional[str]) -> Optional[dict]:
    if not agent_id:
        return None
    try:
        rows = (_client().table(AGENTS).select("*").eq("tenant_id", tenant_id)
                .eq("id", agent_id).limit(1).execute().data or [])
        return rows[0] if rows else None
    except Exception:
        return None


# ── Properties ────────────────────────────────────────────────────────────────

async def list_properties(
    tenant_id: str,
    *,
    statuses: Optional[tuple] = None,
    listing_type: Optional[str] = None,
    property_type: Optional[str] = None,
    min_price_cents: Optional[int] = None,
    max_price_cents: Optional[int] = None,
    min_bedrooms: Optional[float] = None,
    suburb: Optional[str] = None,
    featured_only: bool = False,
    limit: int = 200,
) -> List[dict]:
    q = _client().table(PROPERTIES).select("*").eq("tenant_id", tenant_id)
    if statuses:
        q = q.in_("status", list(statuses))
    if listing_type in LISTING_TYPES:
        q = q.eq("listing_type", listing_type)
    if property_type:
        q = q.eq("property_type", property_type)
    if min_price_cents is not None:
        q = q.gte("price_cents", min_price_cents)
    if max_price_cents is not None:
        q = q.lte("price_cents", max_price_cents)
    if min_bedrooms is not None:
        q = q.gte("bedrooms", min_bedrooms)
    if suburb:
        # ilike wildcards come from the caller's text — strip them so "%" can't match everything.
        q = q.ilike("suburb", f"%{re.sub(r'[%_]', '', suburb.strip())}%")
    if featured_only:
        q = q.eq("featured", True)
    try:
        return (q.order("featured", desc=True).order("sort").order("created_at", desc=True)
                .limit(min(max(limit, 1), 500)).execute().data or [])
    except Exception as exc:
        log.debug("list_properties skipped (run migration 257?): %s", exc)
        return []


async def get_property(tenant_id: str, *, slug: Optional[str] = None,
                       property_id: Optional[str] = None) -> Optional[dict]:
    q = _client().table(PROPERTIES).select("*").eq("tenant_id", tenant_id)
    if property_id:
        q = q.eq("id", property_id)
    elif slug:
        q = q.eq("slug", slug)
    else:
        return None
    rows = q.limit(1).execute().data or []
    return rows[0] if rows else None


async def _unique_slug(tenant_id: str, base: str, exclude_id: Optional[str] = None) -> str:
    slug, n = base, 2
    while True:
        rows = (_client().table(PROPERTIES).select("id").eq("tenant_id", tenant_id)
                .eq("slug", slug).limit(1).execute().data or [])
        if not rows or (exclude_id and rows[0].get("id") == exclude_id):
            return slug
        slug, n = f"{base}-{n}", n + 1


async def upsert_property(tenant_id: str, body: Dict[str, Any]) -> dict:
    """Create or update a listing. Returns the row as read back from the DB."""
    row = clean_property(body)
    if row.get("agent_id") and not await _agent(tenant_id, row["agent_id"]):
        raise ValueError("That agent isn't one of this business's agents.")
    row["updated_at"] = _now()
    prop_id = body.get("id")
    if prop_id:
        if body.get("slug"):
            row["slug"] = await _unique_slug(tenant_id, slugify(body["slug"]), exclude_id=prop_id)
        res = (_client().table(PROPERTIES).update(row)
               .eq("tenant_id", tenant_id).eq("id", prop_id).execute())
        if not res.data:
            raise LookupError("listing not found")
    else:
        if not row.get("title"):
            raise ValueError("A listing needs a title, e.g. '3 bedroom house in Durbanville'.")
        row["slug"] = await _unique_slug(tenant_id, slugify(body.get("slug") or row["title"]))
        res = _client().table(PROPERTIES).insert({**row, "tenant_id": tenant_id}).execute()
        if not res.data:
            raise RuntimeError("listing was not saved")
        prop_id = res.data[0]["id"]
    # Verified, not reported: read the row back rather than trusting the write's echo.
    saved = await get_property(tenant_id, property_id=prop_id)
    if not saved:
        raise RuntimeError("listing was not saved")
    return saved


async def delete_property(tenant_id: str, property_id: str) -> None:
    _client().table(PROPERTIES).delete().eq("tenant_id", tenant_id).eq("id", property_id).execute()


async def public_properties(tenant_id: str, *, include_sold: bool = False, **filters) -> List[dict]:
    statuses = PUBLIC_STATUSES + (SOLD_STATUSES if include_sold else ())
    rows = await list_properties(tenant_id, statuses=statuses, **filters)
    agents = {a["id"]: a for a in await list_agents(tenant_id, active_only=False)}
    return [public_view(p, agents.get(p.get("agent_id"))) for p in rows]


async def public_property(tenant_id: str, slug: str) -> Optional[dict]:
    prop = await get_property(tenant_id, slug=slug)
    if not prop or prop.get("status") not in PUBLIC_STATUSES + SOLD_STATUSES:
        return None
    return public_view(prop, await _agent(tenant_id, prop.get("agent_id")))


# ── Enquiries ─────────────────────────────────────────────────────────────────

async def create_enquiry(tenant_id: str, body: Dict[str, Any], ip: str = "") -> dict:
    """Store a website enquiry and tell the listing's agent + the team. The caller has already
    validated name/contact and rate-limited."""
    prop = None
    if body.get("property_slug"):
        prop = await get_property(tenant_id, slug=body["property_slug"])
        if not prop or prop.get("status") not in PUBLIC_STATUSES + SOLD_STATUSES:
            raise LookupError("listing not found")
    row = {
        "tenant_id": tenant_id,
        "property_id": prop["id"] if prop else None,
        "name": body["name"],
        "whatsapp": body.get("whatsapp") or None,
        "email": body.get("email") or None,
        "message": body.get("message") or None,
        "wants_viewing": bool(body.get("wants_viewing")),
        "preferred_times": body.get("preferred_times") or None,
        "source": body.get("source") or "website",
        "ip": ip or None,
    }
    if body.get("external_ref"):
        row["external_ref"] = body["external_ref"]          # e.g. a portal email's Message-ID
    res = _client().table(ENQUIRIES).insert(row).execute()
    saved = (res.data or [row])[0]
    await _tell_people(tenant_id, saved, prop)
    await _acknowledge(tenant_id, saved, prop)
    return saved


async def _acknowledge(tenant_id: str, enquiry: Dict[str, Any], prop: Optional[Dict[str, Any]]) -> bool:
    """Thank a website enquirer on WhatsApp with the approved enquiry template, so the agent can
    talk to them there. Only for enquiries from the website form (someone who messaged on
    WhatsApp is already in a conversation), only with a WhatsApp number they gave for this, and
    only once settings.whatsapp_enquiry_template names an approved template. Best-effort: the
    enquiry is stored and the agent alerted either way."""
    from config import settings
    name = (settings.whatsapp_enquiry_template or "").strip()
    if not name or enquiry.get("source", "website") != "website" or not enquiry.get("whatsapp"):
        return False
    try:
        from vula.api.tenants import get_config
        business = (get_config(tenant_id) or {}).get("display_name") or "us"
    except Exception:
        business = "us"
    from vula.consent import opted_out
    if opted_out(tenant_id, enquiry["whatsapp"]):
        return False
    first = (enquiry.get("name") or "").split()[0] if (enquiry.get("name") or "").strip() else "there"
    about = prop["title"] if prop else "your enquiry"
    try:
        from vula.api.whatsapp import send_template
        return await send_template(tenant_id, enquiry["whatsapp"], name, [first, business, about],
                                   lang=settings.whatsapp_enquiry_template_lang or "en")
    except Exception as exc:
        log.warning("enquiry acknowledgement not sent (%s): %s", tenant_id, exc)
        return False


def enquiry_alert_text(enquiry: Dict[str, Any], prop: Optional[Dict[str, Any]]) -> str:
    what = "wants to view" if enquiry.get("wants_viewing") else "asked about"
    lines = [f"🏠 New property enquiry — {enquiry['name']} {what} "
             + (f"{prop['title']} ({format_price(prop)})" if prop else "your listings")]
    if enquiry.get("preferred_times"):
        lines.append(f"Preferred times: {enquiry['preferred_times']}")
    if enquiry.get("message"):
        lines.append(f"“{enquiry['message']}”")
    if enquiry.get("source") == "portal":
        lines.append("Came in by email from a property portal.")
    elif enquiry.get("source") and enquiry["source"] != "website":
        lines.append(f"Came in via {enquiry['source']}.")
    if enquiry.get("whatsapp"):
        lines.append(f"Reply on WhatsApp: https://wa.me/{enquiry['whatsapp']}")
    if enquiry.get("email"):
        lines.append(f"Email: {enquiry['email']}")
    return "\n".join(lines)


async def _tell_people(tenant_id: str, enquiry: Dict[str, Any], prop: Optional[Dict[str, Any]]) -> None:
    """The listing's own agent first (if they have a WhatsApp number), then whoever on the team
    subscribed to property enquiries — falling back to the owner, because a lead nobody hears
    about is a lost sale. Best-effort: the enquiry is already stored either way."""
    text = enquiry_alert_text(enquiry, prop)
    from vula.integrations import notify
    team: List[str] = []
    try:
        team = notify.recipients_for(tenant_id, "property_enquiry")
        # WhatsApp + dashboard push to the team (owner fallback: property_enquiry is urgent).
        await notify.notify_team(tenant_id, "property_enquiry", text)
    except Exception as exc:
        log.warning("enquiry alert to team failed (%s): %s", tenant_id, exc)
    try:
        agent = await _agent(tenant_id, (prop or {}).get("agent_id"))
        if agent and agent.get("whatsapp"):
            from vula.team_index import digits_za
            agent_to = digits_za(agent["whatsapp"])
            if agent_to and agent_to not in team:          # don't send the same alert twice
                from vula.api.whatsapp import _send_reply
                await _send_reply(agent_to, text, tenant_id=tenant_id)
    except Exception as exc:
        log.warning("enquiry alert to agent failed (%s): %s", tenant_id, exc)


async def list_enquiries(tenant_id: str, status: Optional[str] = None, limit: int = 200) -> List[dict]:
    q = _client().table(ENQUIRIES).select("*").eq("tenant_id", tenant_id)
    if status in ENQUIRY_STATUSES:
        q = q.eq("status", status)
    try:
        return q.order("created_at", desc=True).limit(min(max(limit, 1), 500)).execute().data or []
    except Exception as exc:
        log.debug("list_enquiries skipped (run migration 257?): %s", exc)
        return []


async def set_enquiry_status(tenant_id: str, enquiry_id: str, status: str) -> dict:
    if status not in ENQUIRY_STATUSES:
        raise ValueError(f"status must be one of {', '.join(ENQUIRY_STATUSES)}.")
    res = (_client().table(ENQUIRIES).update({"status": status})
           .eq("tenant_id", tenant_id).eq("id", enquiry_id).execute())
    if not res.data:
        raise LookupError("enquiry not found")
    return res.data[0]
