"""
vula/api/events.py — events and tickets (vula/commerce/events.py, 24 Nov 2026).

Dashboard (`router`, mounted at /v1/commerce, so the tenant guard applies):

    GET  …/{t}/admin/events                       the business's events with their numbers
    POST …/{t}/admin/events                       {title, starts_at, …, types:[…]} — a draft
    GET  …/{t}/admin/events/{id}                  one event
    PUT  …/{t}/admin/events/{id}                  change it and its ticket types
    POST …/{t}/admin/events/{id}/status           {status} draft | on_sale | closed | cancelled
    GET  …/{t}/admin/events/{id}/attendees        the door list and the takings
    POST …/{t}/admin/events/{id}/check-in         {code} — let one ticket in
    POST …/{t}/admin/events/{id}/tickets/{k}/undo a check-in made by mistake (owner/manager)

    GET/POST …/{t}/admin/events/{id}/door-link    the door link: read it, make it, renew it, stop it (owner/manager)

The door page (`door`, mounted at /v1/door — the link's secret is the only way in):

    GET  /v1/door/{key}            the event and its tickets as names, types and code hashes
    POST /v1/door/{key}/check-in   {code | ticket_id, device?, at?} — let one ticket in
    POST /v1/door/{key}/sync       {items:[{ref, code | ticket_id, at}], device?} — check-ins made with no signal

Public (`public`, no prefix — anyone with the link):

    GET  /events/{id}            the event's page: what, when, where, tickets left
    POST /events/{id}/buy        {items:[{type_id, quantity}], name, phone, email?} → where to pay
    GET  /tickets/{token}        the buyer's tickets (the token is the order's signed one)
    GET  /ticket/{code}          one ticket on its own page, for the buyer to send a friend
"""
from __future__ import annotations

import html
import json
import time
from collections import deque
from datetime import datetime
from typing import Optional
from zoneinfo import ZoneInfo

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse

from vula.commerce import events

router = APIRouter(tags=["events"])            # mounted at /v1/commerce
public = APIRouter(tags=["events-public"])     # no prefix

SAST = ZoneInfo("Africa/Johannesburg")


def _refuse(exc: events.EventError) -> HTTPException:
    return HTTPException(status_code=exc.status, detail=str(exc))


async def _who(request: Request, tenant_id: str) -> str:
    from vula import acting
    return await acting.from_dashboard(request.headers.get("authorization", ""), tenant_id) or ""


# ── dashboard ─────────────────────────────────────────────────────────────────────────────────
@router.get("/{tenant_id}/admin/events")
async def list_events(tenant_id: str) -> dict:
    return {"events": events.list_events(tenant_id)}


@router.post("/{tenant_id}/admin/events")
async def create_event(tenant_id: str, body: dict, request: Request) -> dict:
    try:
        return {"event": await events.create(tenant_id, body or {}, await _who(request, tenant_id))}
    except events.EventError as exc:
        raise _refuse(exc) from exc


@router.get("/{tenant_id}/admin/events/{event_id}")
async def get_event(tenant_id: str, event_id: str) -> dict:
    try:
        return {"event": events.get(tenant_id, event_id)}
    except events.EventError as exc:
        raise _refuse(exc) from exc


@router.put("/{tenant_id}/admin/events/{event_id}")
async def update_event(tenant_id: str, event_id: str, body: dict) -> dict:
    try:
        return {"event": await events.update(tenant_id, event_id, body or {})}
    except events.EventError as exc:
        raise _refuse(exc) from exc


@router.post("/{tenant_id}/admin/events/{event_id}/status")
async def set_event_status(tenant_id: str, event_id: str, body: dict) -> dict:
    try:
        return {"event": await events.set_status(tenant_id, event_id, str((body or {}).get("status") or ""))}
    except events.EventError as exc:
        raise _refuse(exc) from exc


@router.get("/{tenant_id}/admin/events/{event_id}/attendees")
async def event_attendees(tenant_id: str, event_id: str) -> dict:
    try:
        return events.attendees(tenant_id, event_id)
    except events.EventError as exc:
        raise _refuse(exc) from exc


@router.post("/{tenant_id}/admin/events/{event_id}/check-in")
async def event_check_in(tenant_id: str, event_id: str, body: dict, request: Request) -> dict:
    try:
        return events.check_in(tenant_id, event_id, (body or {}).get("code"), await _who(request, tenant_id))
    except events.EventError as exc:
        raise _refuse(exc) from exc


@router.post("/{tenant_id}/admin/events/{event_id}/tickets/{ticket_id}/undo")
async def event_undo_check_in(tenant_id: str, event_id: str, ticket_id: str, request: Request) -> dict:
    from vula.api.commerce import _may_apply_stock
    if not await _may_apply_stock(request, tenant_id):
        raise HTTPException(status_code=403, detail="Only the owner or a manager can undo a check-in.")
    try:
        return {"ticket": events.undo_check_in(tenant_id, event_id, ticket_id)}
    except events.EventError as exc:
        raise _refuse(exc) from exc


@router.get("/{tenant_id}/admin/events/{event_id}/door-link")
async def event_door_link(tenant_id: str, event_id: str) -> dict:
    try:
        return events.door_status(tenant_id, event_id)
    except events.EventError as exc:
        raise _refuse(exc) from exc


@router.post("/{tenant_id}/admin/events/{event_id}/door-link")
async def event_make_door_link(tenant_id: str, event_id: str, body: dict, request: Request) -> dict:
    """Make the door link ({"new": true}: a fresh one, the old stops working) or turn it off
    ({"on": false}). Whoever holds the link can let people in, so: owner or manager only."""
    from vula.api.commerce import _may_apply_stock
    from vula.api.merchant_audit import audit
    if not await _may_apply_stock(request, tenant_id):
        raise HTTPException(status_code=403, detail="Only the owner or a manager can make or stop a door link.")
    b = body or {}
    try:
        out = events.door_off(tenant_id, event_id) if b.get("on") is False \
            else events.door_link(tenant_id, event_id, new=bool(b.get("new")))
    except events.EventError as exc:
        raise _refuse(exc) from exc
    audit(tenant_id, {"email": await _who(request, tenant_id)}, "event_door_link", event_id=event_id,
          on=out["on"], new=bool(b.get("new")))
    return out


# ── the door page's own routes: the link's secret is the only way in ──────────────────────────
door = APIRouter(tags=["events-door"])          # mounted at /v1/door

_DOOR_RECENT: dict = {}


def _door_throttled(key: str, per_minute: int) -> bool:
    now = time.monotonic()
    q = _DOOR_RECENT.setdefault(key, deque())
    while q and now - q[0] > 60:
        q.popleft()
    if len(q) >= per_minute:
        return True
    q.append(now)
    if len(_DOOR_RECENT) > 5000:
        _DOOR_RECENT.clear()
    return False


def _door_guard(request: Request, key: str, per_minute: int) -> None:
    ip = (request.headers.get("x-forwarded-for") or "").split(",")[0].strip() or \
        (request.client.host if request.client else "")
    # by address (someone guessing keys) and by key (one link, however many phones share it)
    if _door_throttled(f"ip|{ip}", 240) or _door_throttled(f"k|{key[:80]}", per_minute):
        raise HTTPException(status_code=429, detail="Too many tries. Wait a moment.")


@door.get("/{key}")
async def door_list(key: str, request: Request) -> dict:
    _door_guard(request, key, 120)
    try:
        return events.door_list(key)
    except events.EventError as exc:
        raise _refuse(exc) from exc


@door.post("/{key}/check-in")
async def door_check_in(key: str, body: dict, request: Request) -> dict:
    _door_guard(request, key, 600)
    b = body or {}
    try:
        return events.door_check_in(key, b, b.get("device"))
    except events.EventError as exc:
        raise _refuse(exc) from exc


@door.post("/{key}/sync")
async def door_sync(key: str, body: dict, request: Request) -> dict:
    _door_guard(request, key, 60)
    b = body or {}
    try:
        return events.door_sync(key, b.get("items"), b.get("device"))
    except events.EventError as exc:
        raise _refuse(exc) from exc


# ── public pages ──────────────────────────────────────────────────────────────────────────────
def _esc(s) -> str:
    return html.escape(str(s or ""), quote=True)


def when_text(starts: str, ends: Optional[str] = None) -> str:
    """Sat 6 Dec 2026, 18:00 – 22:00 — South African time."""
    try:
        s = datetime.fromisoformat(str(starts).replace("Z", "+00:00")).astimezone(SAST)
    except ValueError:
        return ""
    out = f"{s.strftime('%a')} {s.day} {s.strftime('%b %Y')}, {s.strftime('%H:%M')}"
    if ends:
        try:
            e = datetime.fromisoformat(str(ends).replace("Z", "+00:00")).astimezone(SAST)
            out += f" – {e.strftime('%H:%M')}" if e.date() == s.date() else \
                f" – {e.strftime('%a')} {e.day} {e.strftime('%b')}, {e.strftime('%H:%M')}"
        except ValueError:
            pass
    return out


async def _brand(tenant_id: str) -> dict:
    import re
    from vula.commerce import service
    try:
        inv = await service.get_invoice_settings(tenant_id) or {}
    except Exception:  # noqa: BLE001
        inv = {}
    accent = str(inv.get("accent_color") or "2C5545")
    accent = accent if accent.startswith("#") else f"#{accent}"
    if not re.fullmatch(r"#[0-9a-fA-F]{6}", accent):
        accent = "#2C5545"
    logo = str(inv.get("logo_url") or "")
    return {"name": inv.get("trading_as") or inv.get("company_name") or tenant_id.replace("-", " ").replace("_", " ").title(),
            "accent": accent, "logo": logo if logo.startswith("https://") else ""}


def _shell(title: str, accent: str, head: str, body: str, script: str = "") -> HTMLResponse:
    from vula.commerce import powered_by
    page = (_SHELL.replace("{{PB_CSS}}", powered_by.pill_css()).replace("{{PB}}", powered_by.pill_html())
            .replace("{{ACCENT}}", accent).replace("{{SCRIPT}}", script)
            .replace("{{TITLE}}", _esc(title).replace("{{", "{ {"))
            .replace("{{HEAD}}", head.replace("{{", "{ {"))
            .replace("{{BODY}}", body.replace("{{", "{ {")))
    return HTMLResponse(page, headers={"Cache-Control": "no-store"})


@public.get("/events/{event_id}", response_class=HTMLResponse, include_in_schema=False)
async def event_page(event_id: str, cancelled: int = 0) -> HTMLResponse:
    ev = events.public_event(event_id)
    if not ev:
        raise HTTPException(status_code=404, detail="Not Found")
    brand = await _brand(ev["tenant_id"])
    when = when_text(ev["starts_at"], ev.get("ends_at"))
    where = ", ".join(x for x in (ev["venue"], ev["address"]) if x)
    img = ev["image_url"] if ev["image_url"].startswith("https://") else ""
    lowest = min((t["price_cents"] for t in ev["types"]), default=0)
    ld = {"@context": "https://schema.org", "@type": "Event", "name": ev["title"], "startDate": ev["starts_at"],
          "eventStatus": "https://schema.org/EventCancelled" if ev["status"] == "cancelled"
          else "https://schema.org/EventScheduled",
          "organizer": {"@type": "Organization", "name": brand["name"]}}
    if ev.get("ends_at"):
        ld["endDate"] = ev["ends_at"]
    if where:
        ld["location"] = {"@type": "Place", "name": ev["venue"] or where, "address": ev["address"] or where}
    if img:
        ld["image"] = img
    if lowest:
        ld["offers"] = {"@type": "Offer", "price": f"{lowest // 100}.{lowest % 100:02d}", "priceCurrency": "ZAR",
                        "url": ev["url"], "availability": "https://schema.org/InStock" if ev["selling"]
                        and any(t["left"] for t in ev["types"]) else "https://schema.org/SoldOut"}
    desc = f"{ev['title']} — {when}" + (f" at {where}" if where else "")
    head = (f'<meta name="description" content="{_esc(desc[:300])}">'
            f'<meta property="og:title" content="{_esc(ev["title"])}"><meta property="og:type" content="website">'
            f'<meta property="og:description" content="{_esc(desc[:300])}">'
            + (f'<link rel="canonical" href="{_esc(ev["url"])}"><meta property="og:url" content="{_esc(ev["url"])}">'
               if ev["url"] else "")
            + (f'<meta property="og:image" content="{_esc(img)}">' if img else "")
            + f'<script type="application/ld+json">{json.dumps(ld).replace("<", chr(92) + "u003c")}</script>')

    if ev["status"] == "cancelled":
        state = '<p class="note bad">This event has been cancelled.</p>'
    elif not ev["selling"]:
        state = '<p class="note">Ticket sales are closed.</p>'
    else:
        state = ""
    rows = ""
    for t in ev["types"]:
        left = t["left"]
        most = min(left, t["max_per_order"])
        tail = ('<span class="so">Sold out</span>' if not left else
                f'<div class="qty"><button type="button" class="st" data-d="-1" aria-label="One fewer {_esc(t["name"])}">−</button>'
                f'<output aria-live="polite">0</output>'
                f'<button type="button" class="st" data-d="1" aria-label="One more {_esc(t["name"])}">+</button></div>')
        few = f'<span class="few">{left} left</span>' if 0 < left <= 10 else ""
        rows += (f'<li class="tt" data-id="{_esc(t["id"])}" data-c="{int(t["price_cents"])}" data-max="{most}">'
                 f'<div><strong>{_esc(t["name"])}</strong>{few}<br><span class="pr">{events.rand(t["price_cents"])}</span></div>'
                 f'{tail}</li>')
    form = ""
    if ev["selling"] and ev["types"]:
        form = (f'<form id="buy" novalidate><ul class="tts">{rows}</ul>'
                '<label>Your name<input id="nm" autocomplete="name" required maxlength="80"></label>'
                '<label>Cellphone number<input id="ph" type="tel" autocomplete="tel" inputmode="tel" required '
                'placeholder="082 123 4567"></label>'
                '<label>Email (optional)<input id="em" type="email" autocomplete="email" maxlength="200"></label>'
                '<p class="hint">Your tickets are sent to this number on WhatsApp and shown after you pay.</p>'
                '<p id="err" class="note bad" role="alert" hidden></p>'
                '<button id="go" type="submit" disabled>Choose your tickets</button></form>')
    elif ev["types"]:
        form = f'<ul class="tts">{rows}</ul>'
    back = '<p class="note">Payment was cancelled. Your tickets were not bought.</p>' if cancelled else ""
    body = ((f'<img class="hero" src="{_esc(img)}" alt="">' if img else "")
            + '<div class="by">' + (f'<img class="lg" src="{_esc(brand["logo"])}" alt="">' if brand["logo"] else "")
            + f'<span>{_esc(brand["name"])}</span></div>'
            f'<h1>{_esc(ev["title"])}</h1>'
            f'<p class="meta"><span>{_esc(when)}</span>' + (f'<span>{_esc(where)}</span>' if where else "") + '</p>'
            + back + state
            + (f'<p class="desc">{_esc(ev["description"])}</p>' if ev["description"] else "")
            + form)
    script = _BUY_JS.replace("{{EVENT}}", json.dumps(ev["id"])) if form.startswith("<form") else ""
    return _shell(ev["title"], brand["accent"], head, body, script)


_RECENT: dict = {}
_BUYS_PER_MINUTE = 6


def _throttled(key: str) -> bool:
    now = time.monotonic()
    q = _RECENT.setdefault(key, deque())
    while q and now - q[0] > 60:
        q.popleft()
    if len(q) >= _BUYS_PER_MINUTE:
        return True
    q.append(now)
    if len(_RECENT) > 5000:
        _RECENT.clear()
    return False


@public.post("/events/{event_id}/buy", include_in_schema=False)
async def event_buy(event_id: str, body: dict, request: Request) -> dict:
    """A public endpoint that holds seats: a small per-address throttle on top of the server-wide one."""
    ip = (request.headers.get("x-forwarded-for") or "").split(",")[0].strip() or \
        (request.client.host if request.client else "")
    if _throttled(f"{ip}|{event_id}"):
        raise HTTPException(status_code=429, detail="Too many tries. Wait a minute and try again.")
    b = body or {}
    try:
        return await events.buy(event_id, items=b.get("items"), name=b.get("name"), phone=b.get("phone"),
                                email=b.get("email") or "")
    except events.EventError as exc:
        raise _refuse(exc) from exc


@public.get("/tickets/{token}", response_class=HTMLResponse, include_in_schema=False)
async def tickets_page(token: str) -> HTMLResponse:
    data = await events.order_tickets(token)
    if not data:
        raise HTTPException(status_code=404, detail="Not Found")
    brand = await _brand(data["tenant_id"])
    head = '<meta name="robots" content="noindex">'
    if not data["tickets"]:
        if data["status"] == "pending_payment":
            head += '<meta http-equiv="refresh" content="6">'
            body = (f'<h1>Waiting for your payment</h1><p class="desc">Order {_esc(data["order"])}. '
                    'This page shows your tickets as soon as the payment comes through. It checks by itself.</p>')
        else:
            body = (f'<h1>No tickets on this order</h1><p class="desc">Order {_esc(data["order"])} '
                    'was cancelled or refunded.</p>')
        return _shell("Your tickets", brand["accent"], head, body)
    many = len(data["tickets"]) > 1
    cards = "".join(_ticket_card(k, f'ticket {n} of {len(data["tickets"])}', data["name"], share=many)
                    for n, k in enumerate(data["tickets"], 1))
    body = ('<div class="by">' + (f'<img class="lg" src="{_esc(brand["logo"])}" alt="">' if brand["logo"] else "")
            + f'<span>{_esc(brand["name"])}</span></div>'
            f'<h1>Your tickets</h1><p class="desc">Order {_esc(data["order"])}. Show each QR code at the door. '
            'Each one gets one person in, once.'
            + (' Coming with others? Send each person their own ticket with the button under it.' if many else '')
            + ' Keep this page to yourself: anyone with it can use the tickets.</p>'
            f'<ul class="tks">{cards}</ul>')
    return _shell("Your tickets", brand["accent"], head, body)


def _ticket_card(k: dict, label: str, holder: str, share: bool = False) -> str:
    from urllib.parse import quote
    ev = k["event"]
    where = ", ".join(x for x in (ev.get("venue"), ev.get("address")) if x)
    when = when_text(ev.get("starts_at") or "", ev.get("ends_at"))
    if k["status"] == "valid":
        face, tag = f'<div class="qr">{events.qr_svg(k["code"])}</div>', ""
    elif k["status"] == "checked_in":
        face, tag = '<div class="qr used">Used</div>', '<span class="tag">Already used</span>'
    else:
        face, tag = '<div class="qr used">Cancelled</div>', '<span class="tag bad">Cancelled</span>'
    send = ""
    if share and k["status"] == "valid" and k.get("url"):
        text = f"Your ticket for {ev.get('title') or 'the event'}" + (f", {when}" if when else "") + f": {k['url']}"
        send = (f'<a class="sh" href="https://wa.me/?text={quote(text)}" target="_blank" rel="noopener">'
                'Send this ticket on WhatsApp</a>')
    return (f'<li class="tk">{face}<div class="ti"><strong>{_esc(ev.get("title") or "Event")}</strong>{tag}'
            f'<span>{_esc(k["type"])}' + (f' · {_esc(label)}' if label else "") + '</span>'
            f'<span>{_esc(when)}</span>' + (f'<span>{_esc(where)}</span>' if where else "")
            + (f'<span>{_esc(holder)}</span>' if holder else "") + send + '</div></li>')


@public.get("/ticket/{code}", response_class=HTMLResponse, include_in_schema=False)
async def ticket_page(code: str) -> HTMLResponse:
    """One ticket on its own page: what a buyer sends each friend."""
    k = events.single_ticket(code)
    if not k:
        raise HTTPException(status_code=404, detail="Not Found")
    brand = await _brand(k["tenant_id"])
    body = ('<div class="by">' + (f'<img class="lg" src="{_esc(brand["logo"])}" alt="">' if brand["logo"] else "")
            + f'<span>{_esc(brand["name"])}</span></div><h1>Your ticket</h1>'
            '<p class="desc">Show this QR code at the door. It gets one person in, once.</p>'
            f'<ul class="tks">{_ticket_card(k, "", "")}</ul>')
    return _shell("Your ticket", brand["accent"], '<meta name="robots" content="noindex">', body)


_SHELL = r"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{{TITLE}}</title>
{{HEAD}}
<style>
:root{--bg:#f6f5f2;--card:#fff;--ink:#17201c;--mute:#5d6964;--line:#e3e1db;--accent:{{ACCENT}};--bad:#a3271f}
@media (prefers-color-scheme:dark){:root{--bg:#111614;--card:#1a211e;--ink:#eef2ef;--mute:#9aa7a1;--line:#2a332f;--bad:#ff9d94}}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);font:16px/1.5 system-ui,-apple-system,"Segoe UI",Roboto,sans-serif}
main{max-width:640px;margin:0 auto;padding:24px 16px 8px}
.hero{display:block;width:100%;aspect-ratio:16/9;object-fit:cover;border-radius:16px;margin:0 0 20px;background:var(--line)}
.by{display:flex;align-items:center;gap:10px;color:var(--mute);font-size:14px;margin:0 0 8px}
.lg{width:28px;height:28px;border-radius:6px;object-fit:contain;background:#fff}
h1{font-size:28px;line-height:1.15;margin:0 0 10px;letter-spacing:-.02em;overflow-wrap:anywhere}
.meta{display:flex;flex-direction:column;gap:2px;margin:0 0 16px;font-weight:600}
.meta span+span{font-weight:400;color:var(--mute)}
.desc{white-space:pre-line;margin:0 0 20px;overflow-wrap:anywhere}
.note{padding:12px 14px;border-radius:12px;background:var(--card);border:1px solid var(--line);margin:0 0 16px}
.note.bad{color:var(--bad);border-color:var(--bad)}
.tts,.tks{list-style:none;margin:0 0 20px;padding:0;display:grid;gap:10px}
.tt{display:flex;align-items:center;justify-content:space-between;gap:12px;padding:14px;background:var(--card);border:1px solid var(--line);border-radius:14px}
.pr{color:var(--mute);font-variant-numeric:tabular-nums}
.few{margin-left:8px;font-size:13px;color:var(--bad)}
.so{color:var(--mute);font-size:14px}
.qty{display:flex;align-items:center;gap:6px}
.qty output{min-width:28px;text-align:center;font-weight:600;font-variant-numeric:tabular-nums}
.st{width:44px;height:44px;border-radius:12px;border:1px solid var(--line);background:var(--bg);color:inherit;font-size:22px;line-height:1;cursor:pointer}
.st:disabled{opacity:.35;cursor:default}
label{display:block;font-size:14px;font-weight:500;margin:0 0 14px}
input{display:block;width:100%;margin-top:6px;padding:12px 14px;font:inherit;color:inherit;background:var(--card);border:1px solid var(--line);border-radius:12px}
.hint{font-size:13px;color:var(--mute);margin:-4px 0 16px}
#go{width:100%;padding:15px;font:inherit;font-weight:600;border:0;border-radius:14px;background:var(--accent);color:#fff;cursor:pointer}
#go:disabled{opacity:.5;cursor:default}
button:focus-visible,input:focus-visible{outline:2px solid var(--accent);outline-offset:2px}
.tk{display:flex;gap:14px;align-items:center;padding:14px;background:var(--card);border:1px solid var(--line);border-radius:16px}
.qr{flex:none;width:132px;height:132px;background:#fff;border-radius:10px;display:grid;place-items:center;padding:4px}
.qr svg{width:100%;height:100%}
.qr.used{background:var(--line);color:var(--mute);font-weight:600}
.ti{display:flex;flex-direction:column;gap:2px;min-width:0;font-size:14px;color:var(--mute);overflow-wrap:anywhere}
.ti strong{font-size:16px;color:var(--ink)}
.tag{align-self:flex-start;font-size:12px;font-weight:600;padding:2px 8px;border-radius:999px;border:1px solid var(--line)}
.tag.bad{color:var(--bad);border-color:var(--bad)}
.sh{align-self:flex-start;margin-top:8px;padding:9px 14px;border-radius:999px;border:1px solid var(--line);color:var(--ink);font-weight:600;text-decoration:none}
.sh:focus-visible{outline:2px solid var(--accent);outline-offset:2px}
[hidden]{display:none!important}
@media (max-width:420px){.tk{flex-direction:column;align-items:flex-start}.qr{width:180px;height:180px}}
@media print{.vpb{display:none}body{background:#fff}}
{{PB_CSS}}
</style></head><body><main>
{{BODY}}
</main>{{PB}}{{SCRIPT}}</body></html>"""

# The page sends ticket-type ids and quantities; the total shown here is only a preview of the
# prices the page was built with — the server prices the order from its own records.
_BUY_JS = r"""<script>
(function(){var f=document.getElementById('buy');if(!f)return;var EVENT={{EVENT}};
var go=document.getElementById('go'),err=document.getElementById('err'),rows=[].slice.call(f.querySelectorAll('.tt'));
function money(c){var r=Math.floor(c/100),k=c%100;return 'R'+String(r).replace(/\B(?=(\d{3})+(?!\d))/g,',')+'.'+(k<10?'0':'')+k;}
function sum(){var n=0,c=0;rows.forEach(function(r){var o=r.querySelector('output');if(!o)return;var q=+o.textContent;
n+=q;c+=q*(+r.getAttribute('data-c'));var b=r.querySelectorAll('.st');b[0].disabled=q<=0;b[1].disabled=q>=+r.getAttribute('data-max');});
go.disabled=n===0;go.textContent=n?('Pay '+money(c)+' for '+n+' ticket'+(n===1?'':'s')):'Choose your tickets';}
rows.forEach(function(r){[].slice.call(r.querySelectorAll('.st')).forEach(function(b){b.addEventListener('click',function(){
var o=r.querySelector('output'),q=Math.max(0,Math.min(+r.getAttribute('data-max'),+o.textContent+(+b.getAttribute('data-d'))));
o.textContent=q;sum();});});});
sum();
f.addEventListener('submit',function(e){e.preventDefault();err.hidden=true;
var items=rows.map(function(r){var o=r.querySelector('output');return o?{type_id:r.getAttribute('data-id'),quantity:+o.textContent}:null;})
.filter(function(x){return x&&x.quantity>0;});
var body={items:items,name:document.getElementById('nm').value,phone:document.getElementById('ph').value,email:document.getElementById('em').value};
go.disabled=true;var label=go.textContent;go.textContent='One moment…';
fetch('/events/'+EVENT+'/buy',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)})
.then(function(r){return r.json().then(function(d){return {ok:r.ok,d:d};});})
.then(function(x){if(x.ok&&x.d.redirect_url){location.href=x.d.redirect_url;return;}
err.textContent=(x.d&&typeof x.d.detail==='string')?x.d.detail:"That didn't go through. Try again.";err.hidden=false;go.textContent=label;go.disabled=false;})
.catch(function(){err.textContent="Couldn't reach the shop. Check your connection and try again.";err.hidden=false;go.textContent=label;go.disabled=false;});
});})();
</script>"""
