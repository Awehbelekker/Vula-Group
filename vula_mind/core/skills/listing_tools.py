"""
core/skills/listing_tools.py — WhatsApp tools for real-estate businesses (the `listings` module,
vula/listings/service.py, migration 254).

Kept in one module, separate from the two big WhatsApp skills, so the add-on is easy to review
and switch off: commerce_assistant / commerce_admin only offer these tools when the tenant has
the `listings` module, and every other tenant's tool list and prompt are unchanged.

Customer side (someone messaging the agency): search listings, get one listing's details (with
its cover photo), ask for a viewing or a call back — which becomes an enquiry that alerts the
listing's agent and the team.

Agent side (staff messaging Vula): list listings, add one, change price / status / details, see
enquiries and move them along. Adding and changing a listing show a preview first and run only
after the agent confirms (the same commerce_pending_confirmations flow as every other admin
change); the result is read back from the database before Vula says it's done.

Every figure a customer or agent sees (prices, levies) is formatted here from integer cents —
never computed or reworded by the model.
"""
from __future__ import annotations

import logging
import re
from typing import Any, Dict, List, Optional, Tuple

from vula.listings import service as ls

log = logging.getLogger(__name__)

MAX_RESULTS = 6


# ── Shared helpers ────────────────────────────────────────────────────────────

def tenant_has_listings(tenant_id: str) -> bool:
    try:
        from vula.api.tenants import enabled_modules
        return "listings" in (enabled_modules(tenant_id) or [])
    except Exception:
        return False


def _n(v: Any) -> Any:
    """2.0 → 2, 2.5 → 2.5 (bedrooms/bathrooms)."""
    if v is None:
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return v
    return int(f) if f.is_integer() else f


def _summary(p: Dict[str, Any]) -> Dict[str, Any]:
    """The facts a person needs to tell listings apart — no address unless the agency allows it."""
    out = {
        "ref": p.get("slug"),
        "title": p.get("title"),
        "for": "rent" if p.get("listing_type") == "rent" else "sale",
        "price": ls.format_price(p),
        "status": (p.get("status") or "").replace("_", " "),
        "suburb": p.get("suburb"),
        "city": p.get("city"),
        "bedrooms": _n(p.get("bedrooms")),
        "bathrooms": _n(p.get("bathrooms")),
        "garages": p.get("garages"),
        "floor_m2": p.get("floor_m2"),
    }
    return {k: v for k, v in out.items() if v not in (None, "", [])}


_STOP = {"the", "a", "an", "in", "at", "on", "for", "of", "and", "house", "home", "property",
         "listing", "one", "that", "this", "please"}


def _tokens(text: str) -> set:
    return {t for t in re.findall(r"[a-z0-9]+", (text or "").lower()) if t not in _STOP and len(t) > 1}


def match_listings(rows: List[Dict[str, Any]], ref: str) -> List[Dict[str, Any]]:
    """Listings a person meant by `ref` ("the Durbanville house", a slug, a title). Exact slug or
    id first; otherwise every listing whose title/suburb holds all of the words they used,
    falling back to the best partial overlap. Pure — the callers decide what to do with 0 / 2+."""
    ref = (ref or "").strip()
    if not ref:
        return []
    low = ref.lower()
    by_ref = [r for r in rows if low in ((r.get("slug") or "").lower(), str(r.get("id") or "").lower())]
    if by_ref:
        return by_ref[:1]                       # slugs and ids are unique
    by_title = [r for r in rows if (r.get("title") or "").strip().lower() == low]
    if by_title:
        return by_title                         # two "2 Bed Apartment"s → caller asks which
    want = _tokens(ref)
    if not want:
        return []
    scored: List[Tuple[int, Dict[str, Any]]] = []
    for r in rows:
        have = _tokens(" ".join(str(r.get(k) or "") for k in ("title", "suburb", "city", "slug")))
        hits = len(want & have)
        if hits:
            scored.append((hits, r))
    if not scored:
        return []
    full = [r for h, r in scored if h == len(want)]
    if full:
        return full
    best = max(h for h, _ in scored)
    return [r for h, r in scored if h == best]


# ── Customer tools ────────────────────────────────────────────────────────────

CUSTOMER_TOOL_SPECS: List[Dict[str, Any]] = [
    {"type": "function", "function": {
        "name": "search_listings",
        "description": (
            "Search this agency's properties that are on the market. Call it whenever someone asks "
            "what's available, for a type of home, an area, a budget or a number of bedrooms. "
            "Returns real listings with their exact price text — quote the price exactly as given."),
        "parameters": {"type": "object", "properties": {
            "listing_type": {"type": "string", "enum": ["sale", "rent"],
                             "description": "'sale' to buy, 'rent' to rent. Leave out if they didn't say."},
            "suburb": {"type": "string", "description": "Suburb or area they named."},
            "max_price": {"type": "string", "description": "Their budget as they said it, e.g. 'R2.5m' or '15000'."},
            "min_bedrooms": {"type": "number"},
            "property_type": {"type": "string",
                              "enum": ["house", "apartment", "townhouse", "land", "commercial", "farm"]},
        }},
    }},
    {"type": "function", "function": {
        "name": "listing_details",
        "description": (
            "Full details of ONE listing — price, rooms, sizes, levies/rates, features, description "
            "and the agent — and sends its cover photo. Call when they ask about a specific property "
            "(use the 'ref' from search_listings, or the name/area they used)."),
        "parameters": {"type": "object", "properties": {
            "listing": {"type": "string", "description": "The listing's ref, or the words they used for it."},
        }, "required": ["listing"]},
    }},
    {"type": "function", "function": {
        "name": "request_viewing",
        "description": (
            "Ask the agent to arrange a viewing of a listing. Call once they say they'd like to see "
            "it; include the days/times they prefer if they gave any. The listing's agent is told "
            "straight away and will confirm a time — never promise a specific time yourself."),
        "parameters": {"type": "object", "properties": {
            "listing": {"type": "string", "description": "The listing's ref, or the words they used for it."},
            "preferred_times": {"type": "string", "description": "When they'd like to view, as they said it."},
            "customer_name": {"type": "string", "description": "Their name, if they gave it."},
            "message": {"type": "string", "description": "Anything else they asked or said about it."},
        }, "required": ["listing"]},
    }},
    {"type": "function", "function": {
        "name": "request_agent_callback",
        "description": (
            "Pass a request to an agent who will contact them: wanting to sell or let their own "
            "property (a valuation), a question you can't answer from the listings, a rental "
            "application, or making an offer. Never make an offer or accept terms yourself."),
        "parameters": {"type": "object", "properties": {
            "message": {"type": "string", "description": "What they want, in a sentence."},
            "customer_name": {"type": "string", "description": "Their name, if they gave it."},
            "listing": {"type": "string", "description": "The listing it's about, if any."},
        }, "required": ["message"]},
    }},
]
CUSTOMER_TOOL_NAMES = {t["function"]["name"] for t in CUSTOMER_TOOL_SPECS}


async def _public_rows(tenant_id: str, include_sold: bool = False) -> List[Dict[str, Any]]:
    statuses = ls.PUBLIC_STATUSES + (ls.SOLD_STATUSES if include_sold else ())
    return await ls.list_properties(tenant_id, statuses=statuses, limit=300)


async def _resolve_public(tenant_id: str, ref: str) -> Tuple[Optional[Dict[str, Any]], Optional[Dict[str, Any]]]:
    """(listing, None) or (None, a result telling the model what to ask)."""
    rows = await _public_rows(tenant_id, include_sold=True)
    found = match_listings(rows, ref)
    if not found:
        return None, {"error": "No listing matches that.",
                      "hint": "Call search_listings to see what's available, or ask which property they mean."}
    if len(found) > 1:
        return None, {"which_one": [_summary(p) for p in found[:MAX_RESULTS]],
                      "hint": "More than one listing matches — ask which one they mean."}
    return found[0], None


async def run_customer_tool(name: str, args: Dict[str, Any], ctx: Dict[str, Any]) -> Dict[str, Any]:
    tid = ctx["tenant_id"]
    try:
        if name == "search_listings":
            max_cents = None
            if args.get("max_price") not in (None, ""):
                max_cents = ls.parse_rands_to_cents(args.get("max_price"))
                if max_cents is None:
                    return {"error": f"Couldn't read the budget '{args.get('max_price')}'.",
                            "hint": "Ask for their budget in rand, e.g. R2.5m."}
            kind = args.get("listing_type") if args.get("listing_type") in ls.LISTING_TYPES else None
            rows = await ls.list_properties(
                tid, statuses=ls.PUBLIC_STATUSES, listing_type=kind,
                property_type=args.get("property_type") or None, max_price_cents=max_cents,
                min_bedrooms=args.get("min_bedrooms"), suburb=args.get("suburb") or None, limit=50)
            if not rows:
                return {"results": [], "message": "Nothing on the market matches that right now.",
                        "hint": "Offer to widen the search, or request_agent_callback so an agent "
                                "lets them know when something comes up."}
            return {"results": [_summary(p) for p in rows[:MAX_RESULTS]],
                    "total_matches": len(rows)}

        if name == "listing_details":
            prop, problem = await _resolve_public(tid, args.get("listing") or "")
            if problem:
                return problem
            agent = await ls._agent(tid, prop.get("agent_id"))
            view = ls.public_view(prop, agent)
            details = _summary(prop)
            details.update({k: v for k, v in {
                "erf_m2": prop.get("erf_m2"), "parking": prop.get("parking"),
                "levies": ls.format_price({"price_cents": prop.get("levies_cents")}) + " pm"
                if prop.get("levies_cents") else None,
                "rates": ls.format_price({"price_cents": prop.get("rates_cents")}) + " pm"
                if prop.get("rates_cents") else None,
                "available_from": prop.get("available_from"),
                "address": view.get("address"),
                "features": prop.get("features") or None,
                "description": (prop.get("description") or "")[:900] or None,
                "agent": (agent or {}).get("name"),
                "photos": len(prop.get("images") or []) or None,
            }.items() if v not in (None, "", [])})
            if prop.get("images"):
                ctx["media_url"] = prop["images"][0]
            return details

        if name in ("request_viewing", "request_agent_callback"):
            prop = None
            if args.get("listing"):
                prop, problem = await _resolve_public(tid, args["listing"])
                if problem:
                    return problem
            phone = ctx.get("customer_phone")
            from vula.team_index import digits_za
            saved = await ls.create_enquiry(tid, {
                "name": (args.get("customer_name") or "").strip()[:80] or "WhatsApp contact",
                "whatsapp": digits_za(phone) if phone else None,
                "message": (args.get("message") or "").strip()[:1000] or None,
                "property_slug": prop.get("slug") if prop else None,
                "wants_viewing": name == "request_viewing",
                "preferred_times": (args.get("preferred_times") or "").strip()[:200] or None,
                "source": "whatsapp",
            })
            agent = await ls._agent(tid, (prop or {}).get("agent_id"))
            who = (agent or {}).get("name") or "An agent"
            return {"sent": bool(saved),
                    "message": (f"{who} has been told and will contact them on this number to "
                                + ("confirm a viewing time." if name == "request_viewing" else "follow up."))}
    except LookupError:
        return {"error": "That listing is no longer available."}
    except Exception as exc:
        log.warning("listing tool %s failed (%s): %s", name, tid, exc)
        return {"error": "Couldn't look that up just now."}
    return {"error": f"unknown tool {name}"}


FALLBACK_REPLY = ("Sorry, I couldn't look that up just now. Please try again in a moment — or "
                  "tell me what you're looking for and an agent will get back to you.")


def customer_prompt(lang_block: str = "", persona_block: str = "") -> str:
    """The customer-facing instructions for a real-estate business (no shop/cart/order talk)."""
    return (
        "You are a friendly, professional WhatsApp assistant for a South African estate agency. "
        "Your goal: help people find a property to buy or rent, answer questions about listings, "
        "and get them in touch with the right agent quickly.\n\n"
        "Guidelines:\n"
        "- Reply in the SAME language the person writes in; if unsure, use English.\n"
        "- For anything about what's available, call search_listings. For one property, call "
        "listing_details. Only describe listings those tools returned — never invent a property, "
        "price, size, feature or availability.\n"
        "- Quote prices exactly as the tools give them (e.g. 'R2 450 000', 'R12 500 pm').\n"
        "- When they want to see a property, call request_viewing — the agent confirms the time. "
        "Never promise a time, and never say a viewing is booked.\n"
        "- If they want to sell or let their own home, make an offer, apply to rent, or ask "
        "something the listings don't answer, call request_agent_callback. Never negotiate, accept "
        "an offer, or give legal, bond or valuation advice yourself.\n"
        "- Only share a street address if listing_details gave one.\n"
        "- Keep replies short and WhatsApp-friendly: a few lines, no markdown links (paste plain "
        "URLs if any)."
        + lang_block + persona_block
    )


# ── Agent (staff) tools ───────────────────────────────────────────────────────

_LISTING_FIELDS_SCHEMA = {
    "title": {"type": "string", "description": "e.g. '3 bedroom family home with pool'"},
    "listing_type": {"type": "string", "enum": ["sale", "rent"]},
    "price": {"type": "string", "description": "As the agent said it: 'R2.4m', '2 450 000', or monthly rent '12500'."},
    "property_type": {"type": "string", "enum": ["house", "apartment", "townhouse", "land", "commercial", "farm", "other"]},
    "suburb": {"type": "string"}, "city": {"type": "string"},
    "bedrooms": {"type": "number"}, "bathrooms": {"type": "number"},
    "garages": {"type": "integer"}, "parking": {"type": "integer"},
    "floor_m2": {"type": "integer"}, "erf_m2": {"type": "integer"},
    "levies": {"type": "string", "description": "Monthly levies in rand."},
    "rates": {"type": "string", "description": "Monthly rates in rand."},
    "description": {"type": "string"},
    "features": {"type": "array", "items": {"type": "string"}, "description": "e.g. ['Pool', 'Solar', 'Pet friendly']"},
    "agent": {"type": "string", "description": "Name of the agent on this listing."},
    "featured": {"type": "boolean"},
}

ADMIN_TOOL_SPECS: List[Dict[str, Any]] = [
    {"type": "function", "function": {
        "name": "list_listings",
        "description": "The agency's property listings, optionally only one status or matching some words.",
        "parameters": {"type": "object", "properties": {
            "status": {"type": "string", "enum": list(ls.STATUSES)},
            "query": {"type": "string", "description": "Words to match in the title/suburb."},
        }},
    }},
    {"type": "function", "function": {
        "name": "create_listing",
        "description": (
            "Add a new property listing. Shows the agent a preview first; it is only saved when they "
            "confirm. Ask for anything essential that's missing (what it is, sale or rent, price, "
            "area). status 'active' puts it on the website, 'draft' keeps it hidden."),
        "parameters": {"type": "object", "properties": {
            **_LISTING_FIELDS_SCHEMA,
            "status": {"type": "string", "enum": ["draft", "active"], "description": "Default 'active'."},
            "confirm": {"type": "boolean", "description": "Only true after the agent confirmed the preview."},
        }, "required": ["title", "listing_type"]},
    }},
    {"type": "function", "function": {
        "name": "update_listing",
        "description": (
            "Change a listing: price, status (active, under_offer, sold, let, draft, archived) or any "
            "details. Shows a preview first; saved only when the agent confirms."),
        "parameters": {"type": "object", "properties": {
            "listing": {"type": "string", "description": "Which listing — its ref or the words used for it."},
            **_LISTING_FIELDS_SCHEMA,
            "status": {"type": "string", "enum": list(ls.STATUSES)},
            "confirm": {"type": "boolean", "description": "Only true after the agent confirmed the preview."},
        }, "required": ["listing"]},
    }},
    {"type": "function", "function": {
        "name": "remove_listing_photo",
        "description": ("Remove a photo from a listing (photo 1 is the cover). Agents add photos by "
                        "sending them captioned 'photos <listing>'. Shows a preview first."),
        "parameters": {"type": "object", "properties": {
            "listing": {"type": "string"},
            "photo_number": {"type": "integer", "description": "1 = first/cover photo. Leave out for the last one."},
            "confirm": {"type": "boolean"},
        }, "required": ["listing"]},
    }},
    {"type": "function", "function": {
        "name": "property_enquiries",
        "description": "Recent enquiries and viewing requests from buyers/tenants (website and WhatsApp).",
        "parameters": {"type": "object", "properties": {
            "status": {"type": "string", "enum": list(ls.ENQUIRY_STATUSES)},
        }},
    }},
    {"type": "function", "function": {
        "name": "update_enquiry",
        "description": "Move an enquiry along: contacted, viewing (arranged), or closed.",
        "parameters": {"type": "object", "properties": {
            "enquiry_id": {"type": "string", "description": "The id from property_enquiries."},
            "status": {"type": "string", "enum": ["contacted", "viewing", "closed"]},
        }, "required": ["enquiry_id", "status"]},
    }},
]
ADMIN_TOOL_NAMES = {t["function"]["name"] for t in ADMIN_TOOL_SPECS}
# Need the agent's preview + confirm (see commerce_admin._PREVIEW_FIRST).
ADMIN_PREVIEW_FIRST = frozenset({"create_listing", "update_listing", "remove_listing_photo"})
ADMIN_KEYWORDS = {"listing", "listings", "property", "properties", "house", "flat", "apartment",
                  "townhouse", "under offer", "sold", "for sale", "to let", "rental", "rent ",
                  "viewing", "enquir", "mandate", "bedroom", "erf"}

_EDITABLE = ("title", "listing_type", "property_type", "suburb", "city", "bedrooms", "bathrooms",
             "garages", "parking", "floor_m2", "erf_m2", "description", "features", "featured")


async def _agent_id_for(tid: str, name: Optional[str]) -> Tuple[Optional[str], Optional[str]]:
    """(agent_id, None) or (None, error). Blank name → (None, None)."""
    if not name:
        return None, None
    agents = await ls.list_agents(tid, active_only=False)
    low = name.strip().lower()
    hits = [a for a in agents if low in (a.get("name") or "").lower()]
    if len(hits) == 1:
        return hits[0]["id"], None
    if not hits:
        known = ", ".join(a["name"] for a in agents) or "none set up yet"
        return None, f"No agent called '{name}'. Agents: {known}."
    return None, f"'{name}' matches more than one agent: {', '.join(a['name'] for a in hits)}."


def _body_from_args(args: Dict[str, Any]) -> Dict[str, Any]:
    body = {k: args[k] for k in _EDITABLE if k in args and args[k] not in (None, "")}
    for key in ("price", "levies", "rates"):
        if args.get(key) not in (None, ""):
            body[key] = args[key]
    if args.get("status"):
        body["status"] = args["status"]
    return body


def _preview_fields(row: Dict[str, Any]) -> Dict[str, Any]:
    """What the agent checks before confirming — formatted from cents here, not by a model."""
    fields = {
        "listing": row.get("title"),
        "for": ("rent" if row.get("listing_type") == "rent" else "sale") if row.get("listing_type") else None,
        "price": ls.format_price(row) if ("price_cents" in row or row.get("price_on_request")) else None,
        "status": (row.get("status") or "").replace("_", " ") or None,
        "area": ", ".join(x for x in (row.get("suburb"), row.get("city")) if x) or None,
        "rooms": " · ".join(x for x in (
            f"{_n(row['bedrooms'])} bed" if row.get("bedrooms") is not None else "",
            f"{_n(row['bathrooms'])} bath" if row.get("bathrooms") is not None else "",
            f"{row['floor_m2']} m²" if row.get("floor_m2") else "") if x) or None,
        "levies": ls.format_price({"price_cents": row["levies_cents"]}) + " pm" if row.get("levies_cents") else None,
        "features": ", ".join(row.get("features") or []) or None,
    }
    return {k: v for k, v in fields.items() if v}


async def run_admin_tool(name: str, args: Dict[str, Any], ctx: Dict[str, Any]) -> Dict[str, Any]:
    tid = ctx["tenant_id"]
    confirm = bool(args.get("confirm"))
    try:
        if name == "list_listings":
            statuses = (args["status"],) if args.get("status") in ls.STATUSES else None
            rows = await ls.list_properties(tid, statuses=statuses, limit=300)
            if args.get("query"):
                rows = match_listings(rows, args["query"])
            agents = {a["id"]: a["name"] for a in await ls.list_agents(tid, active_only=False)}
            out = []
            for p in rows[:20]:
                s = _summary(p)
                if agents.get(p.get("agent_id")):
                    s["agent"] = agents[p["agent_id"]]
                out.append(s)
            return {"listings": out, "total": len(rows)}

        if name == "create_listing":
            body = _body_from_args(args)
            body.setdefault("status", "active")
            if body["status"] not in ("draft", "active"):
                body["status"] = "active"
            agent_id, err = await _agent_id_for(tid, args.get("agent"))
            if err:
                return {"error": err}
            if agent_id:
                body["agent_id"] = agent_id
            row = ls.clean_property(body)          # raises ValueError with a fixable message
            if not row.get("title"):
                return {"error": "A listing needs a title, e.g. '3 bedroom house in Durbanville'."}
            if not confirm:
                prev = _preview_fields(row)
                if args.get("agent"):
                    prev["agent"] = args["agent"]
                prev["on_website"] = "yes" if row.get("status") == "active" else "no (draft)"
                return {"preview": True, **prev}
            saved = await ls.upsert_property(tid, body)      # reads the row back
            return _readback(tid, "create_listing", saved, row,
                             f"✅ Listed *{saved['title']}* — {ls.format_price(saved)}"
                             + (" — live on the website." if saved.get("status") == "active" else " (draft, not on the website yet)."))

        if name == "update_listing":
            rows = await ls.list_properties(tid, limit=500)
            found = match_listings(rows, args.get("listing") or "")
            if not found:
                return {"error": "No listing matches that.", "hint": "Call list_listings to find it."}
            if len(found) > 1:
                return {"which_one": [_summary(p) for p in found[:MAX_RESULTS]],
                        "hint": "Ask which listing they mean."}
            current = found[0]
            body = _body_from_args(args)
            agent_id, err = await _agent_id_for(tid, args.get("agent"))
            if err:
                return {"error": err}
            if agent_id:
                body["agent_id"] = agent_id
            if not body:
                return {"error": "Nothing to change — say what should change (price, status, details)."}
            changes = ls.clean_property(body)
            if not confirm:
                before, after = _preview_fields(current), _preview_fields({**current, **changes})
                diff = {k: f"{before.get(k, '—')} → {v}" for k, v in after.items() if before.get(k) != v}
                if args.get("agent"):
                    diff["agent"] = args["agent"]
                return {"preview": True, "listing": current.get("title"), **diff}
            saved = await ls.upsert_property(tid, {**body, "id": current["id"]})   # reads back
            return _readback(tid, "update_listing", saved, changes,
                             f"✅ *{saved['title']}* updated — {ls.format_price(saved)}, "
                             f"{(saved.get('status') or '').replace('_', ' ')}.")

        if name == "remove_listing_photo":
            rows = await ls.list_properties(tid, limit=500)
            found = match_listings(rows, args.get("listing") or "")
            if len(found) != 1:
                return ({"which_one": [_summary(p) for p in found[:MAX_RESULTS]], "hint": "Ask which listing."}
                        if found else {"error": "No listing matches that."})
            prop = found[0]
            images = list(prop.get("images") or [])
            if not images:
                return {"error": f"{prop['title']} has no photos."}
            n = int(args.get("photo_number") or len(images))
            if not 1 <= n <= len(images):
                return {"error": f"{prop['title']} has {len(images)} photos — pick 1 to {len(images)}."}
            if not confirm:
                return {"preview": True, "listing": prop["title"],
                        "remove": f"photo {n} of {len(images)}" + (" (the cover)" if n == 1 else "")}
            removed = images.pop(n - 1)
            saved = await ls.upsert_property(tid, {"id": prop["id"], "images": images})
            ok = removed not in (saved.get("images") or []) and len(saved.get("images") or []) == len(images)
            _gate(tid, "remove_listing_photo", ok, {"id": prop["id"], "photos": len(images)},
                  {"id": saved.get("id"), "photos": len(saved.get("images") or [])})
            if not ok:
                return {"error": "The photo wasn't removed.", "reply_verbatim": "⚠️ The photo wasn't removed — please try again."}
            return {"verified": True, "reply_verbatim": f"🗑️ Removed photo {n} from *{prop['title']}* — {len(images)} left."}

        if name == "property_enquiries":
            rows = await ls.list_enquiries(tid, status=args.get("status"), limit=20)
            titles = {p["id"]: p["title"] for p in await ls.list_properties(tid, limit=500)}
            return {"enquiries": [{k: v for k, v in {
                "id": e.get("id"), "name": e.get("name"), "whatsapp": e.get("whatsapp"),
                "email": e.get("email"), "listing": titles.get(e.get("property_id")),
                "wants_viewing": e.get("wants_viewing") or None,
                "preferred_times": e.get("preferred_times"), "message": e.get("message"),
                "status": e.get("status"), "source": e.get("source"), "when": e.get("created_at"),
            }.items() if v not in (None, "")} for e in rows]}

        if name == "update_enquiry":
            row = await ls.set_enquiry_status(tid, args.get("enquiry_id") or "", args.get("status") or "")
            ok = row.get("status") == args.get("status")
            _gate(tid, "update_enquiry", ok, {"status": args.get("status")}, {"status": row.get("status")})
            if not ok:
                return {"error": "The enquiry didn't update — please try again.",
                        "reply_verbatim": "⚠️ The enquiry didn't update — please try again."}
            return {"verified": True, "reply_verbatim": f"✅ Enquiry from {row.get('name')} marked *{row['status']}*."}
    except ValueError as exc:
        return {"error": str(exc)}
    except LookupError:
        return {"error": "Not found — it may have been removed."}
    except Exception as exc:
        log.warning("listing admin tool %s failed (%s): %s", name, tid, exc)
        return {"error": "Couldn't do that just now."}
    return {"error": f"unknown tool {name}"}


# "photos Bellville house" / "listing photo for family-home" — a team member's photo for a listing.
LISTING_PHOTO_RE = re.compile(
    r"^\s*(?:listing\s+)?(?:photos?|pictures?|pics?|images?)\s+(?:for\s+|of\s+)?(?P<name>.{2,80}?)\s*$",
    re.IGNORECASE)
MAX_PHOTOS = 40


async def add_listing_photo(tenant_id: str, ref: str, image_url: str) -> Dict[str, Any]:
    """Append a stored photo to the listing `ref` names, read back. Returns
    {"reply": text, "ok": bool} — or {"no_match": True} so the caller can fall through."""
    rows = await ls.list_properties(tenant_id, limit=500)
    found = match_listings(rows, ref)
    if not found:
        return {"no_match": True}
    if len(found) > 1:
        names = ", ".join(f"{p['title']} ({p['slug']})" for p in found[:MAX_RESULTS])
        return {"ok": False, "reply": f"That matches more than one listing: {names}. "
                                      "Send the photo again captioned with one of those."}
    prop = found[0]
    images = list(prop.get("images") or [])
    if len(images) >= MAX_PHOTOS:
        return {"ok": False, "reply": f"*{prop['title']}* already has {MAX_PHOTOS} photos — remove one first."}
    if image_url not in images:
        images.append(image_url)
    saved = await ls.upsert_property(tenant_id, {"id": prop["id"], "images": images})
    ok = image_url in (saved.get("images") or [])
    _gate(tenant_id, "add_listing_photo", ok, {"id": prop["id"], "photos": len(images)},
          {"id": saved.get("id"), "photos": len(saved.get("images") or [])})
    if not ok:
        return {"ok": False, "reply": "⚠️ That photo didn't save — please send it again."}
    n = len(saved["images"])
    live = " It's on the website now." if saved.get("status") in ls.PUBLIC_STATUSES else ""
    return {"ok": True, "reply": f"📸 Added to *{saved['title']}* — photo {n}{' (the cover)' if n == 1 else ''}.{live}"
                                 " To take one off, say e.g. \"remove photo 2 from " + saved["title"] + "\"."}


def _gate(tid: str, tool: str, ok: bool, expected: Dict[str, Any], observed: Dict[str, Any]) -> None:
    try:
        from core.skills.commerce_admin import _readback_gate
        _readback_gate(tid, tool, ok, expected, observed)
    except Exception:
        pass


_CHECK_KEYS = ("price_cents", "status", "listing_type", "bedrooms", "bathrooms", "suburb", "title")


def _readback(tid: str, tool: str, saved: Dict[str, Any], intended: Dict[str, Any], text: str) -> Dict[str, Any]:
    """Compare what was meant to be saved with the row read back from the database."""
    def norm(v: Any) -> Any:
        if isinstance(v, bool) or v is None:
            return v
        try:
            return float(v)          # 3 == 3.0 == "3.0" (numeric columns can come back as text)
        except (TypeError, ValueError):
            return v
    expected = {k: intended[k] for k in _CHECK_KEYS if k in intended}
    observed = {k: saved.get(k) for k in expected}
    ok = all(norm(expected[k]) == norm(observed[k]) for k in expected)
    _gate(tid, tool, ok, {"id": saved.get("id"), **{k: v for k, v in expected.items() if k != "title"}},
          {"id": saved.get("id"), **{k: v for k, v in observed.items() if k != "title"}})
    if not ok:
        msg = "The listing didn't save as intended — please check it in the dashboard."
        return {"error": msg, "reply_verbatim": f"⚠️ {msg}"}
    return {"verified": True, "listing": _summary(saved), "reply_verbatim": text}
