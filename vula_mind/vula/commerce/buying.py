"""vula/commerce/buying.py — Vula buys for a business: find the best price, an approver says yes,
then the purchase is paid by the approver or ordered on account. Migration 246.

5 Nov 2026: "find ink cartridges for the Canon printer at the best price, I'll confirm". Rules:
- Off until the owner switches it on (Settings › Buying). Only the shops on the business's list are
  searched; anything else is "not on your list".
- A price is shown only when it is printed on the shop's own page (extraction_quality's amount
  reader); the model may read the page, never supply a figure.
- Limits are checked here, in code: per purchase and per calendar month (South African time).
- Nothing is bought without an approver's yes: the owner/admins (or the people the owner named).
  A request from someone else goes to them on WhatsApp (vula/commerce/approvals.py, `purchase`).
- On yes: an online shop → the price is read again (more than 5% up or out of stock → ask again)
  and the approver gets the product link to pay (card, Google Pay, Apple Pay); a supplier on
  account → a purchase order is made and sent (vula/commerce/purchase_orders.py), read back.
- Vula never holds money, a card or a shop password, and never completes a checkout itself.
- Afterwards the receipt (an expense, or the bank line) is matched to the purchase and booked to
  its project; no receipt after 3 days → one reminder to the person who asked.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

log = logging.getLogger(__name__)

SETTINGS, SHOPS, PURCHASES = "vula_buying_settings", "vula_buying_shops", "vula_purchases"
WHO_CAN_ASK = ("owner", "managers", "team")
_OWNER_ROLES = {"owner", "admin"}
_MANAGER_ROLES = _OWNER_ROLES | {"manager"}
_OUTSIDERS = {None, "", "customer", "sales_rep", "client", "project_client"}
PRICE_RISE_LIMIT = 0.05          # more than 5% above the approved price → ask again
DRAFT_TTL = timedelta(hours=6)   # "the second one" refers to a search from the last 6 hours
REMIND_AFTER = timedelta(days=3)
MATCH_WINDOW = timedelta(days=14)
MAX_SHOPS = 6
MAX_OFFERS = 3
_SAST = timezone(timedelta(hours=2))

DEFAULTS: Dict[str, Any] = {
    "enabled": False, "per_purchase_limit_cents": 0, "monthly_limit_cents": 0,
    "who_can_ask": "managers", "approver_phones": [], "default_project": None, "default_account": None,
}


def _client():
    from vula.commerce.service import _client as c
    return c()


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _digits(phone: Optional[str]) -> str:
    d = re.sub(r"\D", "", phone or "")
    return "27" + d[1:] if d.startswith("0") and len(d) == 10 else d


def _same_phone(a: Optional[str], b: Optional[str]) -> bool:
    a, b = _digits(a), _digits(b)
    return bool(a and b) and a[-9:] == b[-9:]


def _r(cents: Optional[int]) -> str:
    return f"R{(cents or 0) / 100:,.2f}"


# ── settings and shops ───────────────────────────────────────────────────────────────────────

def settings_for(tenant_id: str) -> Dict[str, Any]:
    """The business's buying settings; off (the defaults) when there's no row or no table yet."""
    try:
        rows = (_client().table(SETTINGS).select("*").eq("tenant_id", tenant_id).limit(1).execute().data or [])
    except Exception as exc:  # noqa: BLE001 — a deployment without migration 246: buying is off
        log.debug("buying settings read skipped: %s", exc)
        rows = []
    out = dict(DEFAULTS)
    if rows:
        out.update({k: rows[0].get(k) for k in DEFAULTS if rows[0].get(k) is not None})
    out["approver_phones"] = list(out.get("approver_phones") or [])
    return out


def enabled(tenant_id: str) -> bool:
    return bool(settings_for(tenant_id).get("enabled"))


def _cents(v: Any) -> int:
    """Rands as typed ("R2,500", 2500, "2 500.50") → integer cents; anything else → ValueError."""
    if isinstance(v, bool):
        raise ValueError("not an amount")
    if isinstance(v, (int, float)):
        cents = int(round(float(v) * 100))
    else:
        s = re.sub(r"[R\s, ]", "", str(v or ""))
        if not re.fullmatch(r"\d+(\.\d{1,2})?", s):
            raise ValueError(f"'{v}' isn't an amount")
        cents = int(round(float(s) * 100))
    if cents < 0:
        raise ValueError("an amount can't be negative")
    return cents


def save_settings(tenant_id: str, patch: Dict[str, Any], by: Optional[str] = None) -> Dict[str, Any]:
    """Change the buying settings (owner/manager — checked by the caller). Rands in, cents stored.
    Read back."""
    row: Dict[str, Any] = {"tenant_id": tenant_id, "updated_by": (by or "")[:120] or None,
                           "updated_at": _now().isoformat()}
    if "enabled" in patch:
        row["enabled"] = bool(patch["enabled"])
    for key in ("per_purchase_limit", "monthly_limit"):
        if patch.get(key) is not None:
            row[f"{key}_cents"] = _cents(patch[key])
    if patch.get("who_can_ask") is not None:
        if patch["who_can_ask"] not in WHO_CAN_ASK:
            raise ValueError(f"who_can_ask must be one of {', '.join(WHO_CAN_ASK)}")
        row["who_can_ask"] = patch["who_can_ask"]
    if patch.get("approver_phones") is not None:
        phones = [_digits(p) for p in patch["approver_phones"] if _digits(p)]
        if any(len(p) < 9 for p in phones):
            raise ValueError("an approver's WhatsApp number looks too short")
        row["approver_phones"] = sorted(set(phones))
    for key in ("default_project", "default_account"):
        if key in patch:
            row[key] = (str(patch[key] or "").strip()[:120]) or None
    cur = settings_for(tenant_id)
    nxt = {**cur, **{k: v for k, v in row.items() if k in DEFAULTS}}
    if nxt.get("enabled") and not nxt.get("per_purchase_limit_cents"):
        raise ValueError("Set a limit per purchase before switching buying on.")
    _client().table(SETTINGS).upsert(row, on_conflict="tenant_id").execute()
    back = settings_for(tenant_id)
    if any(back.get(k) != v for k, v in row.items() if k in DEFAULTS):
        raise RuntimeError("The buying settings didn't read back as saved — please try again.")
    return back


def domain_of(value: str) -> str:
    """'https://www.Shop.example.co.za/ink?x=1' → 'shop.example.co.za'. ValueError if it isn't one."""
    s = (value or "").strip().lower()
    if not s:
        raise ValueError("Give the shop's website, e.g. shop.example.co.za")
    host = urlparse(s if "://" in s else f"https://{s}").hostname or ""
    host = host.removeprefix("www.")
    if not re.fullmatch(r"(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,}", host):
        raise ValueError(f"'{value}' doesn't look like a website address")
    return host


def _on_domain(url: str, domain: str) -> bool:
    host = (urlparse(url or "").hostname or "").lower().removeprefix("www.")
    return urlparse(url or "").scheme == "https" and (host == domain or host.endswith("." + domain))


def shops(tenant_id: str) -> List[Dict[str, Any]]:
    try:
        return (_client().table(SHOPS).select("*").eq("tenant_id", tenant_id).eq("active", True)
                .order("name").execute().data or [])
    except Exception as exc:  # noqa: BLE001
        log.debug("buying shops read skipped: %s", exc)
        return []


def add_shop(tenant_id: str, name: str, *, domain: Optional[str] = None, how: str = "checkout_link",
             supplier_id: Optional[str] = None, by: Optional[str] = None) -> Dict[str, Any]:
    """Allow a shop. A website (Vula sends the approver its checkout link) or a supplier the
    business has an account with (Vula sends a purchase order). Read back."""
    name = (name or "").strip()[:120]
    if not name:
        raise ValueError("Give the shop's name.")
    row: Dict[str, Any] = {"tenant_id": tenant_id, "name": name, "how": how, "active": True,
                           "created_by": (by or "")[:120] or None}
    if how == "checkout_link":
        row["domain"] = domain_of(domain or "")
    elif how == "account":
        sup = (_client().table("commerce_suppliers").select("id,name").eq("tenant_id", tenant_id)
               .eq("id", str(supplier_id or "")).limit(1).execute().data or [])
        if not sup:
            raise ValueError("Pick one of your suppliers for an order on account.")
        row["supplier_id"] = sup[0]["id"]
        if domain:
            row["domain"] = domain_of(domain)
    else:
        raise ValueError("how must be checkout_link or account")
    if any(s["name"].lower() == name.lower() for s in shops(tenant_id)):
        raise ValueError(f"{name} is already on the list.")
    out = (_client().table(SHOPS).insert(row).execute().data or [{}])[0]
    if not any(s.get("id") == out.get("id") for s in shops(tenant_id)):
        raise RuntimeError("The shop didn't read back as added — please try again.")
    return out


def remove_shop(tenant_id: str, shop_id: str) -> None:
    """Take a shop off the list (kept on record, not deleted)."""
    _client().table(SHOPS).update({"active": False}).eq("tenant_id", tenant_id).eq("id", shop_id).execute()
    if any(s.get("id") == shop_id for s in shops(tenant_id)):
        raise RuntimeError("The shop is still on the list — please try again.")


# ── who may ask, who approves, the limits ────────────────────────────────────────────────────

def approvers(tenant_id: str, cfg: Optional[Dict[str, Any]] = None) -> List[Dict[str, str]]:
    """The people who approve a purchase: the owner's named approvers, else the owners/admins."""
    cfg = cfg or settings_for(tenant_id)
    if cfg.get("approver_phones"):
        try:
            from vula.integrations.notify import _members
            names = {_digits(m.get("whatsapp"))[-9:]: m for m in _members(tenant_id) if m.get("whatsapp")}
        except Exception:  # noqa: BLE001
            names = {}
        return [{"phone": p, "name": (names.get(p[-9:]) or {}).get("name", ""),
                 "role": (names.get(p[-9:]) or {}).get("role", "approver")} for p in cfg["approver_phones"]]
    from vula.integrations.notify import _members, _fallback_phone
    out = [{"phone": _digits(m["whatsapp"]), "name": m.get("name", ""), "role": m.get("role", "owner")}
           for m in _members(tenant_id) if m.get("whatsapp") and m.get("role") in _OWNER_ROLES]
    if not out:
        fb = _fallback_phone(tenant_id)
        if fb:
            out = [{"phone": _digits(fb), "name": "", "role": "owner"}]
    return out


def is_approver(tenant_id: str, phone: Optional[str], cfg: Optional[Dict[str, Any]] = None) -> bool:
    return any(_same_phone(a["phone"], phone) for a in approvers(tenant_id, cfg))


def may_ask(tenant_id: str, phone: Optional[str], role: Optional[str], cfg: Optional[Dict[str, Any]] = None) -> bool:
    cfg = cfg or settings_for(tenant_id)
    if is_approver(tenant_id, phone, cfg):
        return True
    role = (role or "").lower() or None
    who = cfg.get("who_can_ask") or "managers"
    if who == "owner":
        return role in _OWNER_ROLES
    if who == "managers":
        return role in _MANAGER_ROLES
    return role not in _OUTSIDERS


def _month_start() -> datetime:
    now = _now().astimezone(_SAST)
    return now.replace(day=1, hour=0, minute=0, second=0, microsecond=0).astimezone(timezone.utc)


def month_spent(tenant_id: str) -> int:
    """Approved, ordered and paid purchases this calendar month (South African time), in cents."""
    rows = (_client().table(PURCHASES).select("total_cents,status,approved_at,created_at")
            .eq("tenant_id", tenant_id).execute().data or [])
    start = _month_start()
    total = 0
    for r in rows:
        if r.get("status") not in ("approved", "ordered", "paid"):
            continue
        when = _when(r.get("approved_at") or r.get("created_at"))
        if when and when >= start:
            total += int(r.get("total_cents") or 0)
    return total


def limit_problem(cfg: Dict[str, Any], total_cents: int, spent_cents: int) -> Optional[str]:
    """Why this purchase can't go ahead under the business's limits, or None."""
    per = int(cfg.get("per_purchase_limit_cents") or 0)
    month = int(cfg.get("monthly_limit_cents") or 0)
    if not per:
        return "No limit per purchase is set yet — the owner sets one in Settings › Buying."
    if total_cents > per:
        return f"{_r(total_cents)} is over the limit of {_r(per)} per purchase (Settings › Buying)."
    if month and spent_cents + total_cents > month:
        return (f"That would take this month's buying to {_r(spent_cents + total_cents)}, over the "
                f"monthly limit of {_r(month)} ({_r(max(0, month - spent_cents))} left).")
    return None


def _when(v: Any) -> Optional[datetime]:
    if not v:
        return None
    try:
        d = datetime.fromisoformat(str(v).replace("Z", "+00:00"))
    except ValueError:
        return None
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


# ── finding prices ───────────────────────────────────────────────────────────────────────────

async def _search(query: str, limit: int = 4) -> List[Dict[str, str]]:
    from core.skills.web_search import _ddg_search
    return await _ddg_search(query, limit=limit)


async def _page_text(url: str) -> str:
    from core.skills.web_search import _fetch_text
    return await _fetch_text(url, max_chars=8000)


async def _extract(page: str, want: str, url: str) -> Dict[str, Any]:
    """The model reads one shop page: {product, price, in_stock, delivery} for the item asked for,
    or {} when the page isn't that item. Its price is checked against the page by the caller."""
    import litellm
    from core.llm_router import resolve_generation_route
    from core.prompt_safety import UNTRUSTED_CONTENT_RULE, fence
    litellm.drop_params = True
    model, api_key, api_base = await resolve_generation_route(task_type="commerce_admin")
    system = ("You read ONE online shop's product page. Reply with JSON only: "
              '{"product": "<the product name as on the page>", "price": "<its selling price exactly as printed, '
              'e.g. R1,299.00>", "in_stock": true|false|null, "delivery": "<delivery cost or time if the page '
              'states it, else empty>"}. Use the product the page is about; if it is not the item asked for '
              "(or the page shows no price), reply {}. Never guess a price.\n\n" + UNTRUSTED_CONTENT_RULE)
    resp = await litellm.acompletion(
        model=model, temperature=0, max_tokens=300, api_key=api_key, api_base=api_base,
        messages=[{"role": "system", "content": system},
                  {"role": "user", "content": f"Item asked for: {want}\nPage: {url}" + fence("SHOP_PAGE", page)}])
    raw = (resp.choices[0].message.content or "").strip()
    m = re.search(r"\{.*\}", raw, re.S)
    try:
        out = json.loads(m.group(0)) if m else {}
    except ValueError:
        out = {}
    return out if isinstance(out, dict) else {}


async def read_offer(url: str, want: str) -> Optional[Dict[str, Any]]:
    """One product page → {product, unit_price_cents, in_stock, delivery, url}, only when the price
    the model read is printed on the page."""
    from vula.commerce.extraction_quality import _amounts_in_text
    page = await _page_text(url)
    if not page:
        return None
    try:
        got = await _extract(page, want, url)
    except Exception as exc:  # noqa: BLE001 — a page we can't read is just not an option
        log.info("buying: page not read (%s): %s", urlparse(url).hostname, type(exc).__name__)
        return None
    try:
        cents = _cents(got.get("price"))
    except ValueError:
        return None
    if not cents or cents not in _amounts_in_text(page):
        return None                                       # not printed on the page: never shown
    return {"product": str(got.get("product") or want).strip()[:160], "unit_price_cents": cents,
            "in_stock": got.get("in_stock") if isinstance(got.get("in_stock"), bool) else None,
            "delivery": str(got.get("delivery") or "").strip()[:80], "url": url}


async def _offers_from(shop: Dict[str, Any], want: str) -> List[Dict[str, Any]]:
    domain = shop.get("domain") or ""
    hits = [h for h in await _search(f"{want} site:{domain}") if _on_domain(h.get("url", ""), domain)]
    found = await asyncio.gather(*(read_offer(h["url"], want) for h in hits[:2]))
    return [{**o, "shop_id": shop["id"], "shop": shop["name"], "how": "checkout_link"}
            for o in found if o and o.get("in_stock") is not False]


def last_paid(tenant_id: str, want: str, supplier: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """What this business last paid for something like it (vula_price_observations)."""
    words = sorted((w for w in re.findall(r"[a-z0-9]{3,}", (want or "").lower())
                    if w not in {"the", "and", "for", "our", "buy", "some", "printer"}), key=len, reverse=True)
    if not words:
        return None
    try:
        q = (_client().table("vula_price_observations").select("description,supplier,unit_price_cents,observed_on")
             .eq("tenant_id", tenant_id).ilike("description", f"%{words[0]}%"))
        if supplier:
            q = q.ilike("supplier", f"%{supplier.split()[0]}%")
        rows = q.order("observed_on", desc=True).limit(20).execute().data or []
    except Exception as exc:  # noqa: BLE001
        log.debug("last paid read skipped: %s", exc)
        return None
    rows = [r for r in rows if all(w in (r.get("description") or "").lower() for w in words[:2])] or rows
    return rows[0] if rows else None


async def find(tenant_id: str, want: str, *, by_phone: Optional[str], by_name: Optional[str] = None) -> Dict[str, Any]:
    """Search the allowed shops for `want`; keep up to 3 options (cheapest first) as a draft the
    next message can pick from ("the second one, 2 of them")."""
    want = (want or "").strip()[:200]
    if not want:
        raise ValueError("What should I look for?")
    allowed = shops(tenant_id)
    if not allowed:
        raise ValueError("No shops are on your buying list yet — add them in Settings › Buying.")
    online = [s for s in allowed if s.get("how") == "checkout_link" and s.get("domain")][:MAX_SHOPS]
    found: List[Dict[str, Any]] = []
    for batch in await asyncio.gather(*(_offers_from(s, want) for s in online)):
        found += batch
    found.sort(key=lambda o: o["unit_price_cents"])
    offers = found[:MAX_OFFERS]
    for s in (s for s in allowed if s.get("how") == "account"):
        if len(offers) >= MAX_OFFERS + 1:
            break
        seen = last_paid(tenant_id, want, s["name"])
        offers.append({"product": want, "shop_id": s["id"], "shop": s["name"], "how": "account",
                       "unit_price_cents": int(seen["unit_price_cents"]) if seen else 0,
                       "price_note": (f"last paid {_r(seen['unit_price_cents'])} on {seen.get('observed_on')}"
                                      if seen else "price to be confirmed by the supplier"), "url": None})
    paid = last_paid(tenant_id, want)
    row = {"tenant_id": tenant_id, "status": "draft", "request": want, "offers": offers,
           "requested_by": _digits(by_phone) or None, "requested_name": (by_name or "")[:120] or None}
    draft = (_client().table(PURCHASES).insert(row).execute().data or [{}])[0]
    return {"draft_id": draft.get("id"), "offers": offers, "searched": [s["name"] for s in online],
            "last_paid": paid}


def describe_offers(res: Dict[str, Any]) -> str:
    """The options as one WhatsApp message (raw links, no markdown links)."""
    offers = res.get("offers") or []
    if not offers:
        names = ", ".join(res.get("searched") or []) or "your shops"
        return f"I couldn't find that with a price on {names}. Try other words, or add another shop in Settings › Buying."
    lines = []
    for i, o in enumerate(offers, 1):
        if o.get("how") == "account":
            lines.append(f"{i}. {o['shop']} (order on account): {o.get('price_note')}")
            continue
        extra = f" · delivery: {o['delivery']}" if o.get("delivery") else ""
        lines.append(f"{i}. {o['shop']}: {o['product']}, {_r(o['unit_price_cents'])} each{extra}\n   {o['url']}")
    paid = res.get("last_paid")
    tail = (f"\n\nYou last paid {_r(paid['unit_price_cents'])}"
            + (f" at {paid['supplier']}" if paid.get("supplier") else "")
            + (f" on {paid['observed_on']}" if paid.get("observed_on") else "") + "." if paid else "")
    return ("Prices as shown on each shop's page:\n\n" + "\n".join(lines) + tail
            + "\n\nWhich one, and how many? Nothing is bought until it's approved.")


# ── a purchase: prepare, submit, carry out ───────────────────────────────────────────────────

def latest_draft(tenant_id: str, phone: Optional[str]) -> Optional[Dict[str, Any]]:
    rows = (_client().table(PURCHASES).select("*").eq("tenant_id", tenant_id).eq("status", "draft")
            .eq("requested_by", _digits(phone)).order("created_at", desc=True).limit(1).execute().data or [])
    if not rows or (_when(rows[0].get("created_at")) or _now()) < _now() - DRAFT_TTL:
        return None
    return rows[0]


def prepare(tenant_id: str, phone: Optional[str], option: int, quantity: float,
            project: Optional[str] = None) -> Dict[str, Any]:
    """The purchase the person picked, checked against the shop list and the limits."""
    draft = latest_draft(tenant_id, phone)
    if not draft:
        raise ValueError("Search first (what should I look for?) — then pick one of the options.")
    offers = draft.get("offers") or []
    if not 1 <= int(option or 0) <= len(offers):
        raise ValueError(f"Pick an option from 1 to {len(offers)}.")
    offer = offers[int(option) - 1]
    shop = next((s for s in shops(tenant_id) if s.get("id") == offer.get("shop_id")), None)
    if not shop:
        raise ValueError(f"{offer.get('shop')} is no longer on your buying list.")
    try:
        qty = float(quantity or 1)
    except (TypeError, ValueError):
        raise ValueError("How many?")
    if qty <= 0 or qty > 1000 or (qty != int(qty) and offer.get("how") == "checkout_link"):
        raise ValueError("How many? (a whole number)")
    qty = int(qty) if qty == int(qty) else qty
    unit = int(offer.get("unit_price_cents") or 0)
    total = int(round(unit * qty))
    cfg = settings_for(tenant_id)
    if offer.get("how") == "checkout_link" or unit:
        problem = limit_problem(cfg, total, month_spent(tenant_id))
        if problem:
            raise ValueError(problem)
    if project:
        from vula.commerce import expenses
        known = expenses.known_projects(tenant_id) or []
        match = next((p for p in known if p.lower() == project.strip().lower()), None)
        if not match:
            raise ValueError(f"There's no project called {project}.")
        project = match
    return {"draft": draft, "shop": shop, "offer": offer, "quantity": qty, "unit_price_cents": unit,
            "total_cents": total, "project": project or cfg.get("default_project"),
            "account_code": cfg.get("default_account"), "cfg": cfg}


def summary(p: Dict[str, Any]) -> str:
    o, qty = p["offer"], p["quantity"]
    price = (f"{_r(p['total_cents'])}" if p["total_cents"] else "price to be confirmed by the supplier")
    where = "order on account" if o.get("how") == "account" else "pay online"
    book = f", booked to {p['project']}" if p.get("project") else ""
    return f"Buy {qty} × {o['product']} from {o['shop']} ({where}), {price}{book}"


async def submit(tenant_id: str, p: Dict[str, Any], *, phone: Optional[str], name: Optional[str]) -> str:
    """Record the purchase. The requester is an approver → carried out now (their yes is the
    approval). Otherwise it goes to the approvers on WhatsApp and waits."""
    o = p["offer"]
    row = {"tenant_id": tenant_id, "status": "pending_approval", "request": p["draft"].get("request"),
           "shop_id": p["shop"]["id"], "shop_name": p["shop"]["name"], "how": o.get("how"),
           "items": [{"name": o["product"], "quantity": p["quantity"], "unit_price_cents": p["unit_price_cents"],
                      "url": o.get("url")}],
           "total_cents": p["total_cents"], "link": o.get("url"), "project": p.get("project"),
           "account_code": p.get("account_code"), "requested_by": _digits(phone) or None,
           "requested_name": (name or "")[:120] or None}
    pur = (_client().table(PURCHASES).insert(row).execute().data or [{}])[0]
    _client().table(PURCHASES).update({"status": "cancelled", "notes": "superseded by a purchase"}) \
        .eq("tenant_id", tenant_id).eq("id", p["draft"]["id"]).eq("status", "draft").execute()
    title = summary(p)
    if is_approver(tenant_id, phone, p["cfg"]):
        return await carry_out(tenant_id, pur["id"], approved_by=name or _digits(phone))
    from vula.commerce import approvals
    people = [a for a in approvers(tenant_id, p["cfg"]) if not _same_phone(a["phone"], phone)]
    if not people:
        _set(tenant_id, pur["id"], {"status": "cancelled", "notes": "no approver to ask"})
        raise ValueError("There's no one to approve purchases — the owner sets approvers in Settings › Buying.")
    appr = await approvals.create_approval(tenant_id, "purchase", pur["id"], title, people,
                                           requested_by=_digits(phone), meta={"purchase_id": pur["id"]})
    _set(tenant_id, pur["id"], {"approval_id": appr.get("id")})
    who = ", ".join(a.get("name") or a["phone"] for a in people)
    return f"Sent to {who} to approve: {title}. Nothing is bought until they say yes."


def _set(tenant_id: str, pid: str, patch: Dict[str, Any]) -> Dict[str, Any]:
    _client().table(PURCHASES).update(patch).eq("tenant_id", tenant_id).eq("id", pid).execute()
    return get(tenant_id, pid) or {}


def get(tenant_id: str, pid: str) -> Optional[Dict[str, Any]]:
    rows = (_client().table(PURCHASES).select("*").eq("tenant_id", tenant_id).eq("id", pid)
            .limit(1).execute().data or [])
    return rows[0] if rows else None


async def carry_out(tenant_id: str, pid: str, *, approved_by: Optional[str]) -> str:
    """Approved: an online shop → price read again, then the approver gets the link to pay; a
    supplier on account → a purchase order, made and sent, read back. Returns the reply."""
    pur = get(tenant_id, pid)
    if not pur:
        return "⚠️ That purchase wasn't found."
    if pur.get("status") != "pending_approval":
        return f"That purchase is already {pur.get('status')}."
    item = (pur.get("items") or [{}])[0]
    stamp = {"approved_by": (approved_by or "")[:120] or None, "approved_at": _now().isoformat()}
    if pur.get("how") == "account":
        return await _order_on_account(tenant_id, pur, item, stamp)
    now = await read_offer(pur.get("link") or "", item.get("name") or "")
    approved_unit = int(item.get("unit_price_cents") or 0)
    if now and (now.get("in_stock") is False or now["unit_price_cents"] > approved_unit * (1 + PRICE_RISE_LIMIT)):
        why = ("it's out of stock" if now.get("in_stock") is False else
               f"the price is now {_r(now['unit_price_cents'])} (was {_r(approved_unit)})")
        _set(tenant_id, pid, {"status": "cancelled", "notes": f"not bought: {why}"[:300], **stamp})
        return f"⚠️ Not bought: at {pur['shop_name']} {why}. Ask me to look again if you still want it."
    back = _set(tenant_id, pid, {"status": "approved", **stamp})
    if back.get("status") != "approved":
        return "⚠️ Approved, but the purchase didn't read back as approved — please check Settings › Buying."
    check = "" if now else (f" I couldn't read the price again just now — check it's {_r(approved_unit)} "
                            "each before you pay.")
    book = f" It'll be booked to {pur['project']} when the receipt comes in." if pur.get("project") else ""
    return (f"✅ Approved: {item.get('quantity')} × {item.get('name')} from {pur['shop_name']}, "
            f"{_r(pur.get('total_cents'))}. Open this, add it to the cart and pay (card, Google Pay or "
            f"Apple Pay):\n{pur.get('link')}\n\nForward the receipt here when it comes.{book}{check}")


async def _order_on_account(tenant_id: str, pur: Dict[str, Any], item: Dict[str, Any], stamp: Dict[str, Any]) -> str:
    from vula.api.commerce import admin_create_purchase_order
    from vula.commerce import purchase_orders
    shop = next((s for s in shops(tenant_id) if s.get("id") == pur.get("shop_id")), None)
    sup = (_client().table("commerce_suppliers").select("id,name,contact_email,contact_phone")
           .eq("tenant_id", tenant_id).eq("id", str((shop or {}).get("supplier_id") or "")).limit(1).execute().data or [])
    if not sup:
        _set(tenant_id, pur["id"], {"status": "cancelled", "notes": "supplier not found", **stamp})
        return f"⚠️ Approved, but {pur.get('shop_name')} isn't linked to one of your suppliers — nothing ordered."
    po = await admin_create_purchase_order(tenant_id, {
        "supplier_id": sup[0]["id"], "supplier_name": sup[0]["name"],
        "items": [{"name": item.get("name"), "quantity": item.get("quantity"),
                   "unit_cost_cents": int(item.get("unit_price_cents") or 0)}],
        "notes": f"Ordered through Vula (purchase {str(pur['id'])[:8]})"})
    channel = "email" if sup[0].get("contact_email") else "whatsapp"
    sent = await purchase_orders.send_purchase_order(tenant_id, po.get("id"), channel)
    back = (_client().table("commerce_purchase_orders").select("id,status").eq("tenant_id", tenant_id)
            .eq("id", po.get("id")).limit(1).execute().data or [{}])[0]
    if back.get("status") != "sent":
        _set(tenant_id, pur["id"], {"status": "approved", "po_id": po.get("id"), **stamp,
                                    "notes": f"PO not sent: {sent.get('error') or 'unknown'}"[:300]})
        return (f"⚠️ Approved, and purchase order {str(po.get('id'))[:8]} is drafted, but it wasn't sent to "
                f"{sup[0]['name']} ({sent.get('error') or 'no way to reach them'}). Send it from Purchase orders.")
    _set(tenant_id, pur["id"], {"status": "ordered", "po_id": po.get("id"), **stamp})
    return (f"✅ Approved and ordered: purchase order {str(po.get('id'))[:8]} sent to {sup[0]['name']} by "
            f"{channel} for {item.get('quantity')} × {item.get('name')}. They'll confirm the price and delivery.")


def reject(tenant_id: str, pid: str, by: Optional[str], notes: str = "") -> None:
    _set(tenant_id, pid, {"status": "rejected", "approved_by": (by or "")[:120] or None,
                          "notes": (notes or "rejected")[:300]})


def recent(tenant_id: str, limit: int = 50) -> List[Dict[str, Any]]:
    rows = (_client().table(PURCHASES).select("*").eq("tenant_id", tenant_id)
            .order("created_at", desc=True).limit(limit * 2).execute().data or [])
    return [r for r in rows if r.get("status") != "draft"][:limit]


# ── afterwards: match the receipt, remind once ───────────────────────────────────────────────

def _shop_word(name: str) -> str:
    words = [w for w in re.findall(r"[a-z0-9]{3,}", (name or "").lower()) if w not in {"the", "pty", "ltd", "shop"}]
    return words[0] if words else ""


def _amount_fits(cents: int, total: int) -> bool:
    return total > 0 and total <= cents <= total + max(int(total * PRICE_RISE_LIMIT), 15000)


def match_receipt(tenant_id: str, pur: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """An expense (a forwarded receipt) or a bank line from the shop for about the approved
    amount, since the approval → linked; the expense gets the purchase's project when it has none."""
    word, total = _shop_word(pur.get("shop_name") or ""), int(pur.get("total_cents") or 0)
    since = _when(pur.get("approved_at")) or _now()
    if not word or not total:
        return None
    sb = _client()
    for e in (sb.table("commerce_expenses").select("id,supplier,description,amount_cents,date,project,created_at,status")
              .eq("tenant_id", tenant_id).ilike("supplier", f"%{word}%").execute().data or []):
        made = _when(e.get("created_at"))
        if e.get("status") == "cancelled" or not made or made < since - timedelta(hours=1):
            continue
        if _amount_fits(int(e.get("amount_cents") or 0), total):
            patch = {"project": pur["project"]} if pur.get("project") and not e.get("project") else {}
            if patch:
                sb.table("commerce_expenses").update(patch).eq("tenant_id", tenant_id).eq("id", e["id"]).execute()
            _set(tenant_id, pur["id"], {"status": "paid", "expense_id": e["id"], "paid_at": _now().isoformat()})
            return {"expense_id": e["id"]}
    for t in (sb.table("commerce_bank_transactions").select("id,description,payee,amount_cents,direction,txn_date,project")
              .eq("tenant_id", tenant_id).eq("direction", "out").ilike("description", f"%{word}%").execute().data or []):
        day = _when(f"{t.get('txn_date')}T23:59:59+02:00")
        if not day or day < since:
            continue
        if _amount_fits(abs(int(t.get("amount_cents") or 0)), total):
            _set(tenant_id, pur["id"], {"status": "paid", "bank_txn_id": t["id"], "paid_at": _now().isoformat()})
            return {"bank_txn_id": t["id"]}
    return None


async def follow_up() -> Dict[str, int]:
    """For every approved online purchase: match its receipt; none after 3 days → one reminder."""
    try:
        rows = (_client().table(PURCHASES).select("*").eq("status", "approved").execute().data or [])
    except Exception as exc:  # noqa: BLE001 — no migration 246 yet: nothing to follow up
        log.debug("purchases follow-up skipped: %s", exc)
        return {"matched": 0, "reminded": 0}
    matched = reminded = 0
    for pur in rows:
        if pur.get("how") != "checkout_link":
            continue
        try:
            if match_receipt(pur["tenant_id"], pur):
                matched += 1
                continue
            approved = _when(pur.get("approved_at"))
            if pur.get("reminded_at") or not approved or approved > _now() - REMIND_AFTER:
                continue
            if approved < _now() - MATCH_WINDOW or not pur.get("requested_by"):
                continue
            from vula.api.whatsapp import _send_reply
            item = (pur.get("items") or [{}])[0]
            text = (f"Did you buy the {item.get('name')} from {pur.get('shop_name')} "
                    f"({_r(pur.get('total_cents'))})? Forward the receipt here and I'll book it"
                    + (f" to {pur['project']}" if pur.get("project") else "") + ".")
            if await _send_reply(pur["requested_by"], text, tenant_id=pur["tenant_id"],
                                 idem_key=f"buy-remind:{pur['id']}"):
                reminded += 1
            _set(pur["tenant_id"], pur["id"], {"reminded_at": _now().isoformat()})
        except Exception as exc:  # noqa: BLE001 — one purchase never stops the rest
            log.warning("purchase follow-up %s failed: %s", str(pur.get("id"))[:8], exc)
    return {"matched": matched, "reminded": reminded}
