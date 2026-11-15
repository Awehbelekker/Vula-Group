"""
vula/listings/sharing.py — listings as WhatsApp photo cards, people's wish lists, and matching
(migration 259; real-estate add-on, `listings` module).

  cards_for(rows)            listings → carousel cards (photo, price, rooms, "View details" button)
  save_wish(...)             what someone said they're looking for, merged into one row per person
  matches_for(tid, prop)     whose wish list a listing fits, minus people it was already sent to
  resolve_recipient(tid, s)  "Thandi" / "082 555 1234" → one person, or the candidates to ask about
  send_listings(...)         send one or more listings to a person on WhatsApp, and record it

Nothing here messages anyone on its own: a person's own search shows them cards in the chat they
started, and everything sent to someone else is an agent's decision (share_listings, previewed
and confirmed first). Money is integer cents; prices are formatted here, never by a model.
"""
from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from vula.listings import service as ls

log = logging.getLogger(__name__)

SHARES = "vula_listing_shares"
WISHES = "vula_buyer_wishes"
MAX_CARDS = 10                      # Meta's carousel limit

# Button ids sent back by WhatsApp when a card / button is tapped (whatsapp.py routes "lst_").
VIEW, BOOK, ASK = "lst_v_", "lst_b_", "lst_q_"


def _client():
    return ls._client()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _digits(phone: Optional[str]) -> str:
    from vula.team_index import digits_za
    return digits_za(phone or "")


# ── Cards ─────────────────────────────────────────────────────────────────────

def card_text(p: Dict[str, Any]) -> str:
    """'*4 Bed Family Home*\\nR3 450 000 · 4 bed · 3 bath · Durbanville' — ≤160 chars (Meta)."""
    bits = [ls.format_price(p)]
    if p.get("bedrooms") is not None:
        bits.append(f"{_n(p['bedrooms'])} bed")
    if p.get("bathrooms") is not None:
        bits.append(f"{_n(p['bathrooms'])} bath")
    if p.get("suburb"):
        bits.append(p["suburb"])
    return f"*{(p.get('title') or '')[:70]}*\n" + " · ".join(bits)


def _n(v: Any) -> Any:
    f = float(v)
    return int(f) if f.is_integer() else f


def cards_for(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Carousel cards for the listings that have a photo (a card needs one), at most 10.
    Empty when fewer than two have photos — a carousel needs 2–10 cards."""
    cards = [{"image": p["images"][0], "text": card_text(p), "id": f"{VIEW}{p['slug']}",
              "button": "View details"}
             for p in rows if (p.get("images") or []) and p.get("slug")][:MAX_CARDS]
    return cards if len(cards) >= 2 else []


def details_text(p: Dict[str, Any], agent: Optional[Dict[str, Any]] = None) -> str:
    """One listing's facts for WhatsApp, built from the row (no model). ≤1024 chars (Meta)."""
    lines = [f"*{p.get('title')}*", ls.format_price(p)]
    rooms = " · ".join(x for x in (
        f"{_n(p['bedrooms'])} bed" if p.get("bedrooms") is not None else "",
        f"{_n(p['bathrooms'])} bath" if p.get("bathrooms") is not None else "",
        f"{p['garages']} garage{'s' if p['garages'] != 1 else ''}" if p.get("garages") else "",
        f"{p['floor_m2']} m²" if p.get("floor_m2") else "",
        f"erf {p['erf_m2']} m²" if p.get("erf_m2") else "") if x)
    if rooms:
        lines.append(rooms)
    where = ", ".join(x for x in (p.get("suburb"), p.get("city")) if x)
    if p.get("show_address") and p.get("address"):
        where = p["address"]
    if where:
        lines.append(f"📍 {where}")
    money = " · ".join(x for x in (
        f"Levies {ls.format_price({'price_cents': p['levies_cents']})} pm" if p.get("levies_cents") else "",
        f"Rates {ls.format_price({'price_cents': p['rates_cents']})} pm" if p.get("rates_cents") else "") if x)
    if money:
        lines.append(money)
    if p.get("features"):
        lines.append("✓ " + ", ".join(p["features"][:8]))
    if p.get("description"):
        lines.append("")
        lines.append(p["description"][:450])
    if agent and agent.get("name"):
        lines.append("")
        lines.append(f"Agent: {agent['name']}")
    return "\n".join(lines)[:1000]


# ── Wish lists ────────────────────────────────────────────────────────────────

def _norm_list(values) -> List[str]:
    out: List[str] = []
    for v in values or []:
        s = re.sub(r"\s+", " ", str(v)).strip()
        if s and s.lower() not in {o.lower() for o in out}:
            out.append(s[:60])
    return out[:12]


async def get_wish(tenant_id: str, phone: str) -> Optional[dict]:
    rows = (_client().table(WISHES).select("*").eq("tenant_id", tenant_id)
            .eq("phone", _digits(phone)).limit(1).execute().data or [])
    return rows[0] if rows else None


async def save_wish(tenant_id: str, phone: str, *, name: Optional[str] = None,
                    listing_type: Optional[str] = None, max_price_cents: Optional[int] = None,
                    min_bedrooms: Optional[float] = None, suburbs: Optional[List[str]] = None,
                    property_types: Optional[List[str]] = None, source: str = "whatsapp",
                    replace_areas: bool = False) -> Optional[dict]:
    """Merge what this person said into their wish list (one row per person). Only the fields
    they gave change; a new area is added to the ones they already named unless replace_areas.
    Returns the row read back, or None when there was nothing to save."""
    phone = _digits(phone)
    if not phone:
        return None
    current = await get_wish(tenant_id, phone)
    row: Dict[str, Any] = {}
    if listing_type in ls.LISTING_TYPES:
        row["listing_type"] = listing_type
    if max_price_cents is not None and max_price_cents > 0:
        row["max_price_cents"] = int(max_price_cents)
    if min_bedrooms is not None:
        try:
            if float(min_bedrooms) > 0:
                row["min_bedrooms"] = float(min_bedrooms)
        except (TypeError, ValueError):
            pass
    if suburbs:
        base = [] if replace_areas or not current else list(current.get("suburbs") or [])
        row["suburbs"] = _norm_list(base + list(suburbs))
    if property_types:
        row["property_types"] = _norm_list([t.lower() for t in property_types if t])
    if not row:
        return current
    if name and not (current or {}).get("name"):
        row["name"] = name.strip()[:80]
    row["updated_at"] = _now()
    row["active"] = True
    if current:
        _client().table(WISHES).update(row).eq("tenant_id", tenant_id).eq("id", current["id"]).execute()
    else:
        _client().table(WISHES).insert({**row, "tenant_id": tenant_id, "phone": phone,
                                        "source": source}).execute()
    return await get_wish(tenant_id, phone)


def wish_fits(wish: Dict[str, Any], p: Dict[str, Any]) -> bool:
    """Does this listing meet everything the person asked for? Pure. Anything they didn't say
    is no constraint; a listing missing a fact they asked about (no price, no bedrooms) fails."""
    if not wish.get("active", True):
        return False
    if wish.get("listing_type") and p.get("listing_type") != wish["listing_type"]:
        return False
    if wish.get("max_price_cents") is not None:
        if p.get("price_on_request") or p.get("price_cents") is None or p["price_cents"] > wish["max_price_cents"]:
            return False
    if wish.get("min_bedrooms") is not None:
        if p.get("bedrooms") is None or float(p["bedrooms"]) < float(wish["min_bedrooms"]):
            return False
    areas = [a.lower() for a in (wish.get("suburbs") or [])]
    if areas:
        here = " ".join(str(p.get(k) or "") for k in ("suburb", "city")).lower()
        if not any(a in here for a in areas):
            return False
    types = [t.lower() for t in (wish.get("property_types") or [])]
    if types and (p.get("property_type") or "").lower() not in types:
        return False
    return True


def describe_wish(w: Dict[str, Any]) -> str:
    """'3+ bed house to buy in Durbanville or Bellville, up to R3 000 000'."""
    beds = f"{_n(w['min_bedrooms'])}+ bed " if w.get("min_bedrooms") is not None else ""
    kind = "/".join(w.get("property_types") or []) or "property"
    deal = {"sale": " to buy", "rent": " to rent"}.get(w.get("listing_type") or "", "")
    areas = w.get("suburbs") or []
    where = f" in {' or '.join(areas)}" if areas else ""
    price = (f", up to {ls.format_price({'price_cents': w['max_price_cents'], 'listing_type': w.get('listing_type')})}"
             if w.get("max_price_cents") is not None else "")
    return f"{beds}{kind}{deal}{where}{price}".strip()


async def shared_before(tenant_id: str, phone: str) -> Dict[str, str]:
    """property_id → when it was last sent to this person."""
    rows = (_client().table(SHARES).select("property_id,created_at").eq("tenant_id", tenant_id)
            .eq("phone", _digits(phone)).execute().data or [])
    out: Dict[str, str] = {}
    for r in rows:
        when = r.get("created_at") or ""
        if r["property_id"] not in out or when > out[r["property_id"]]:
            out[r["property_id"]] = when
    return out


async def matches_for(tenant_id: str, prop: Dict[str, Any]) -> List[Dict[str, Any]]:
    """People whose wish list this listing fits and who haven't been sent it yet."""
    if prop.get("status") not in ls.PUBLIC_STATUSES:
        return []
    try:
        wishes = (_client().table(WISHES).select("*").eq("tenant_id", tenant_id)
                  .eq("active", True).execute().data or [])
        sent = {(r["phone"]) for r in (_client().table(SHARES).select("phone").eq("tenant_id", tenant_id)
                                         .eq("property_id", prop["id"]).execute().data or [])}
    except Exception as exc:
        log.debug("matches skipped (run migration 259?): %s", exc)
        return []
    from vula.consent import opted_out
    return [w for w in wishes if w["phone"] not in sent and wish_fits(w, prop)
            and not opted_out(tenant_id, w["phone"])]


# ── Who to send to ────────────────────────────────────────────────────────────

async def resolve_recipient(tenant_id: str, who: str) -> Tuple[Optional[Dict[str, str]], List[Dict[str, str]]]:
    """(person, []) when `who` is a number or names exactly one person; (None, candidates)
    otherwise. People come from this business's enquiries and wish lists, then its customers."""
    who = (who or "").strip()
    digits = _digits(who)
    if len(digits) >= 9 and len(re.sub(r"\D", "", who)) >= 9:
        return {"phone": digits, "name": None}, []
    if not who:
        return None, []
    low = who.lower()
    people: Dict[str, Dict[str, str]] = {}
    for table, cols in ((ls.ENQUIRIES, "name,whatsapp"), (WISHES, "name,phone")):
        try:
            rows = _client().table(table).select(cols).eq("tenant_id", tenant_id).limit(1000).execute().data or []
        except Exception:
            rows = []
        for r in rows:
            phone = _digits(r.get("whatsapp") or r.get("phone"))
            name = (r.get("name") or "").strip()
            if phone and name and low in name.lower():
                people.setdefault(phone, {"phone": phone, "name": name})
    if not people:
        try:
            from vula.api.commerce import _aggregate_customers
            for c in (await _aggregate_customers(tenant_id)).values():
                name = (c.get("name") or "").strip()
                phone = _digits(c.get("phone"))
                if phone and name and low in name.lower():
                    people.setdefault(phone, {"phone": phone, "name": name})
        except Exception as exc:
            log.debug("customer lookup for sharing skipped: %s", exc)
    found = list(people.values())
    if len(found) == 1:
        return found[0], []
    return None, found[:6]


# ── Sending ───────────────────────────────────────────────────────────────────

async def send_listings(tenant_id: str, phone: str, props: List[Dict[str, Any]], *,
                        intro: str, shared_by: Optional[str] = None, source: str = "agent") -> Dict[str, Any]:
    """Send listings to one person on WhatsApp and record what went. One listing: its photo,
    details and a "Book a viewing" button. Two or more: a swipeable carousel of the ones with
    photos (listings without a photo are named in the intro). Returns {"sent": [...], "ok": bool}.
    WhatsApp refuses a business-started message more than 24 hours after the person's last
    message (it then needs an approved template) — that comes back as ok=False."""
    from vula.api import whatsapp as wa
    from vula.consent import opted_out
    if opted_out(tenant_id, phone):
        return {"ok": False, "sent": [], "reason": "they opted out of messages from this business"}
    creds = await wa._resolve_wa(tenant_id, to=phone)
    if not creds:
        return {"ok": False, "sent": [], "reason": "no WhatsApp number is connected for this business"}
    number = wa._wa_number(phone)
    sent: List[Dict[str, Any]] = []
    if len(props) == 1:
        p = props[0]
        agent = await ls._agent(tenant_id, p.get("agent_id"))
        body = (intro.strip() + "\n\n" if intro.strip() else "") + details_text(p, agent)
        ok = await wa._send_wa_buttons(creds, number, body[:1024],
                                       [{"id": f"{BOOK}{p['slug']}", "title": "Book a viewing"},
                                        {"id": f"{ASK}{p['slug']}", "title": "Ask a question"}],
                                       image_url=(p.get("images") or [None])[0])
        if ok:
            sent = [p]
    else:
        with_photo = [p for p in props if p.get("images")]
        without = [p for p in props if not p.get("images")]
        cards = cards_for(with_photo)
        text = intro.strip() or "Here are some properties you might like — swipe to see them all."
        if without:
            text += "\n\nAlso: " + "; ".join(f"{p['title']} ({ls.format_price(p)})" for p in without)
        if cards and await wa._send_wa_carousel(creds, number, text[:1024], cards):
            sent = list(with_photo[:MAX_CARDS]) + without
        elif not cards:
            lines = "\n".join(f"• *{p['title']}* — {ls.format_price(p)}" for p in props)
            if await wa._send_reply(number, f"{text}\n\n{lines}"[:4000], tenant_id):
                sent = list(props)
    for p in sent:
        try:
            _client().table(SHARES).insert({"tenant_id": tenant_id, "phone": _digits(phone),
                                            "property_id": p["id"], "shared_by": shared_by,
                                            "source": source}).execute()
        except Exception as exc:
            log.warning("listing share not recorded (%s): %s", tenant_id, exc)
    return {"ok": bool(sent), "sent": sent}
