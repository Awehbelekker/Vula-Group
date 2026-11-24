"""
vula/api/tables.py — table bookings and specials (vula/commerce/tables.py, 24 Nov 2026).

Dashboard (`router`, mounted at /v1/commerce — tenant-guarded):

    GET/PUT …/{t}/admin/tables/settings        sittings, seats, how long a table is kept (PUT: owner/manager)
    GET     …/{t}/admin/tables?date=           the day's bookings and its times
    POST    …/{t}/admin/tables                 a booking taken by phone or at the door
    POST    …/{t}/admin/tables/{id}/status     seated | completed | no_show | cancelled | confirmed
    GET/POST …/{t}/admin/specials              the specials; POST saves one ({id?} to change it)
    DELETE  …/{t}/admin/specials/{id}

Public (`public`, no prefix):

    GET  /book/{tenant}                  the booking page, with today's specials
    GET  /book/{tenant}/slots?date=&party=   the times a party of that size can sit
    POST /book/{tenant}                  {starts_at, party, name, phone, notes?} — book it
"""
from __future__ import annotations

import html
import json
import time
from collections import deque
from typing import Optional

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse

from vula.commerce import tables

router = APIRouter(tags=["tables"])             # mounted at /v1/commerce
public = APIRouter(tags=["tables-public"])      # no prefix


def _refuse(exc: tables.TableError) -> HTTPException:
    return HTTPException(status_code=exc.status, detail=str(exc))


async def _who(request: Request, tenant_id: str) -> str:
    from vula import acting
    return await acting.from_dashboard(request.headers.get("authorization", ""), tenant_id) or ""


@router.get("/{tenant_id}/admin/tables/settings")
async def get_settings(tenant_id: str) -> dict:
    return {"settings": tables.settings(tenant_id), "booking_url": tables.booking_url(tenant_id)}


@router.put("/{tenant_id}/admin/tables/settings")
async def put_settings(tenant_id: str, body: dict, request: Request) -> dict:
    from vula.api.commerce import _may_apply_stock
    if not await _may_apply_stock(request, tenant_id):
        raise HTTPException(status_code=403, detail="Only the owner or a manager can change booking settings.")
    try:
        return {"settings": tables.save_settings(tenant_id, body or {}), "booking_url": tables.booking_url(tenant_id)}
    except tables.TableError as exc:
        raise _refuse(exc) from exc


@router.get("/{tenant_id}/admin/tables")
async def day(tenant_id: str, date: Optional[str] = None) -> dict:
    try:
        return tables.day_sheet(tenant_id, date or tables._now().astimezone(tables.SAST).date().isoformat())
    except tables.TableError as exc:
        raise _refuse(exc) from exc


@router.post("/{tenant_id}/admin/tables")
async def add_booking(tenant_id: str, body: dict, request: Request) -> dict:
    b = body or {}
    try:
        return {"booking": await tables.book(
            tenant_id, starts_at=b.get("starts_at"), party=b.get("party"), name=b.get("name"), phone=b.get("phone"),
            notes=b.get("notes") or "", channel="dashboard", booked_by=await _who(request, tenant_id), tell_team=False)}
    except tables.TableError as exc:
        raise _refuse(exc) from exc


@router.post("/{tenant_id}/admin/tables/{booking_id}/status")
async def set_status(tenant_id: str, booking_id: str, body: dict) -> dict:
    try:
        return {"booking": tables.set_status(tenant_id, booking_id, str((body or {}).get("status") or ""))}
    except tables.TableError as exc:
        raise _refuse(exc) from exc


@router.get("/{tenant_id}/admin/specials")
async def list_specials(tenant_id: str) -> dict:
    today = tables._now().astimezone(tables.SAST).date()
    return {"specials": [{**s, "today": tables.runs_on(s, today)} for s in tables.list_specials(tenant_id)]}


@router.post("/{tenant_id}/admin/specials")
async def save_special(tenant_id: str, body: dict) -> dict:
    try:
        return {"special": tables.save_special(tenant_id, body or {})}
    except tables.TableError as exc:
        raise _refuse(exc) from exc


@router.delete("/{tenant_id}/admin/specials/{special_id}")
async def delete_special(tenant_id: str, special_id: str) -> dict:
    tables.delete_special(tenant_id, special_id)
    return {"removed": special_id}


# ── the public booking page ───────────────────────────────────────────────────────────────────
def _esc(s) -> str:
    return html.escape(str(s or ""), quote=True)


_RECENT: dict = {}


def _throttled(key: str, per_minute: int) -> bool:
    now = time.monotonic()
    q = _RECENT.setdefault(key, deque())
    while q and now - q[0] > 60:
        q.popleft()
    if len(q) >= per_minute:
        return True
    q.append(now)
    if len(_RECENT) > 5000:
        _RECENT.clear()
    return False


def _ip(request: Request) -> str:
    return (request.headers.get("x-forwarded-for") or "").split(",")[0].strip() or \
        (request.client.host if request.client else "")


def _known(tenant_id: str) -> dict:
    """The business behind a public booking address — 404 for one that doesn't exist, is paused,
    or has neither bookings on nor a special to show."""
    from vula.api import tenants
    cfg = tenants.get_config(tenant_id) or {}
    if cfg.get("tenant_id") != tenant_id or not tenants.is_active(tenant_id):
        raise HTTPException(status_code=404, detail="Not Found")
    return cfg


@public.get("/book/{tenant_id}", response_class=HTMLResponse, include_in_schema=False)
async def booking_page(tenant_id: str) -> HTMLResponse:
    from vula.api.events import _brand, _shell
    _known(tenant_id)
    st = tables.settings(tenant_id)
    specials = tables.todays_specials(tenant_id)
    if not st["enabled"] and not specials:
        raise HTTPException(status_code=404, detail="Not Found")
    brand = await _brand(tenant_id)
    head = (f'<meta name="description" content="Book a table at {_esc(brand["name"])}.">'
            f'<meta property="og:title" content="{_esc(brand["name"])}"><meta property="og:type" content="website">')
    sp = ""
    for s in specials[:12]:
        img = s.get("image_url") or ""
        sp += ('<li class="tt ad">' + (f'<img class="ai" src="{_esc(img)}" alt="" loading="lazy">' if img.startswith("https://") else "")
               + f'<div class="an"><strong>{_esc(s["title"])}</strong>'
               + (f'<br><span class="pr">{tables.rand(s["price_cents"])}</span>' if s.get("price_cents") is not None else "")
               + (f'<br><span class="hint">{_esc(s["description"])}</span>' if s.get("description") else "") + '</div></li>')
    if sp:
        sp = f'<h2 class="sec">Today’s specials</h2><ul class="tts">{sp}</ul>'
    form = ""
    script = ""
    if st["enabled"]:
        days = tables.open_days(tenant_id, 14, st)
        opts = "".join(f'<option value="{d.isoformat()}">{d.strftime("%a")} {d.day} {d.strftime("%b")}</option>' for d in days)
        party = "".join(f'<option value="{n}"{" selected" if n == 2 else ""}>{n}</option>' for n in range(1, int(st["max_party"]) + 1))
        form = ('<h2 class="sec">Book a table</h2>' + (f'<p class="hint">{_esc(st["note"])}</p>' if st.get("note") else "")
                + '<form id="bk" novalidate><div class="two">'
                f'<label>Day<select id="day">{opts}</select></label>'
                f'<label>People<select id="party">{party}</select></label></div>'
                '<div id="slots" class="slots" role="radiogroup" aria-label="Time"></div>'
                '<label>Your name<input id="nm" autocomplete="name" required maxlength="80"></label>'
                '<label>Cellphone number<input id="ph" type="tel" autocomplete="tel" inputmode="tel" required placeholder="082 123 4567"></label>'
                '<label>Anything we should know? (optional)<input id="nt" maxlength="300" placeholder="Birthday, high chair, allergies"></label>'
                '<p id="err" class="note bad" role="alert" hidden></p>'
                '<button id="go" type="submit" disabled>Choose a time</button></form>'
                '<div id="done" class="note" role="status" hidden></div>') if days else \
            '<p class="note">Bookings aren’t open for the coming days. Message us on WhatsApp.</p>'
        script = _BOOK_JS.replace("{{TENANT}}", json.dumps(tenant_id)) if days else ""
    body = ((f'<div class="by"><img class="lg" src="{_esc(brand["logo"])}" alt=""></div>' if brand["logo"] else "")
            + f'<h1>{_esc(brand["name"])}</h1>' + sp + form)
    return _shell(brand["name"], brand["accent"], head + _BOOK_CSS, body, script)


@public.get("/book/{tenant_id}/slots", include_in_schema=False)
async def booking_slots(tenant_id: str, date: str, party: int, request: Request) -> dict:
    if _throttled(f"s|{_ip(request)}", 60):
        raise HTTPException(status_code=429, detail="Too many tries. Wait a minute.")
    _known(tenant_id)
    try:
        return {"slots": [{"sitting": s["sitting"], "time": s["time"], "starts_at": s["starts_at"]}
                          for s in tables.slots(tenant_id, date, party)]}
    except tables.TableError as exc:
        raise _refuse(exc) from exc


@public.post("/book/{tenant_id}", include_in_schema=False)
async def booking_make(tenant_id: str, body: dict, request: Request) -> dict:
    """A public endpoint that takes seats: throttled per address, and one number can hold at most
    three bookings still to come."""
    if _throttled(f"b|{_ip(request)}", 6):
        raise HTTPException(status_code=429, detail="Too many tries. Wait a minute and try again.")
    _known(tenant_id)
    b = body or {}
    try:
        if len(tables.upcoming_for(tenant_id, str(b.get("phone") or ""))) >= 3:
            raise tables.TableError("You already have three bookings coming up. Message us to make another.", 409)
        made = await tables.book(tenant_id, starts_at=b.get("starts_at"), party=b.get("party"), name=b.get("name"),
                                 phone=b.get("phone"), notes=b.get("notes") or "", channel="web")
    except tables.TableError as exc:
        raise _refuse(exc) from exc
    return {"ref": made["ref"], "when": made["when"], "party_size": made["party_size"]}


_BOOK_CSS = ('<style>.two{display:grid;grid-template-columns:1fr 1fr;gap:12px}'
             'select{display:block;width:100%;margin-top:6px;padding:12px 14px;font:inherit;color:inherit;background:var(--card);'
             'border:1px solid var(--line);border-radius:12px}'
             '.slots{display:flex;flex-wrap:wrap;gap:8px;margin:0 0 18px}.slots .sn{flex-basis:100%;font-size:13px;color:var(--mute);margin-top:6px}'
             '.sl{min-width:72px;min-height:44px;padding:0 12px;border-radius:12px;border:1px solid var(--line);background:var(--card);color:inherit;'
             'font:inherit;font-variant-numeric:tabular-nums;cursor:pointer}.sl[aria-checked=true]{background:var(--accent);border-color:var(--accent);color:#fff}'
             'select:focus-visible,.sl:focus-visible{outline:2px solid var(--accent);outline-offset:2px}</style>')

_BOOK_JS = r"""<script>
(function(){var f=document.getElementById('bk');if(!f)return;var T={{TENANT}};
var day=document.getElementById('day'),party=document.getElementById('party'),box=document.getElementById('slots'),
go=document.getElementById('go'),err=document.getElementById('err'),done=document.getElementById('done'),pick=null;
function label(){go.disabled=!pick;go.textContent=pick?('Book for '+pick.time):'Choose a time';}
function show(list){box.textContent='';pick=null;label();
if(!list.length){var p=document.createElement('p');p.className='hint';p.textContent='No tables free for that many people on this day. Try another day.';box.appendChild(p);return;}
var last='';list.forEach(function(s){if(s.sitting!==last){last=s.sitting;var h=document.createElement('span');h.className='sn';h.textContent=s.sitting;box.appendChild(h);}
var b=document.createElement('button');b.type='button';b.className='sl';b.setAttribute('role','radio');b.setAttribute('aria-checked','false');b.textContent=s.time;
b.addEventListener('click',function(){[].forEach.call(box.querySelectorAll('.sl'),function(x){x.setAttribute('aria-checked','false');});
b.setAttribute('aria-checked','true');pick=s;label();});box.appendChild(b);});}
function load(){err.hidden=true;box.textContent='Looking for tables…';pick=null;label();
fetch('/book/'+T+'/slots?date='+encodeURIComponent(day.value)+'&party='+encodeURIComponent(party.value))
.then(function(r){return r.json().then(function(d){return {ok:r.ok,d:d};});})
.then(function(x){if(x.ok)show(x.d.slots||[]);else{box.textContent='';err.textContent=typeof x.d.detail==='string'?x.d.detail:"Couldn't load the times.";err.hidden=false;}})
.catch(function(){box.textContent='';err.textContent="Couldn't reach the restaurant. Check your connection.";err.hidden=false;});}
day.addEventListener('change',load);party.addEventListener('change',load);load();
f.addEventListener('submit',function(e){e.preventDefault();if(!pick)return;err.hidden=true;go.disabled=true;go.textContent='One moment…';
fetch('/book/'+T,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({starts_at:pick.starts_at,party:+party.value,
name:document.getElementById('nm').value,phone:document.getElementById('ph').value,notes:document.getElementById('nt').value})})
.then(function(r){return r.json().then(function(d){return {ok:r.ok,d:d};});})
.then(function(x){if(x.ok){f.hidden=true;done.textContent='Booked: a table for '+x.d.party_size+' on '+x.d.when+'. Your reference is '+x.d.ref+'. To change or cancel, message us on WhatsApp.';done.hidden=false;return;}
err.textContent=typeof x.d.detail==='string'?x.d.detail:"That didn't go through. Try again.";err.hidden=false;label();if(x.d&&/filled|available/.test(String(x.d.detail)))load();})
.catch(function(){err.textContent="Couldn't reach the restaurant. Check your connection and try again.";err.hidden=false;label();});});
})();
</script>"""
