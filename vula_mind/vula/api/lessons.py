"""
vula/api/lessons.py — lessons and gear hire the business confirms by hand
(vula/commerce/lessons.py, 24 Nov 2026). Mounted at /v1/commerce — tenant-guarded.

    GET/PUT …/{t}/admin/lessons/settings        the lessons, the gear, their prices, the spot (PUT: owner/manager)
    GET     …/{t}/admin/lessons?status=&since=  the requests, the people who can coach, what's waiting
    POST    …/{t}/admin/lessons                 a request taken by phone or in the shop
    POST    …/{t}/admin/lessons/quote           what a request would cost (writes nothing)
    POST    …/{t}/admin/lessons/{id}/confirm    {starts_at, coach_id?, price_cents?, invoice?} — the customer is told
    POST    …/{t}/admin/lessons/{id}/decline    {reason} — the customer is told
    POST    …/{t}/admin/lessons/{id}/status     completed | no_show | cancelled
    GET     …/{t}/admin/lessons/conditions?date=  the forecast for the spot, hour by hour
    GET     …/{t}/admin/lessons/coaches?month=  every coach's month and share (owner/manager)
    GET     …/{t}/admin/lessons/mine?month=     the signed-in coach's own lessons and month
    GET     …/{t}/admin/lessons/sharing?month=  points for sharing beach reports: the team's board and your own
    GET/POST …/{t}/admin/lessons/beach-report   the latest reading from the beach; POST a photo to add one

The customer app (`public`, mounted at /v1/flow; the dashboard's /flow/<business> page). No login:
a request the phone made is opened only by the signed token it was given back.

    GET  /v1/flow/{shop}                    the business, its look, its lessons, gear and prices
    GET  /v1/flow/{shop}/conditions?date=   the forecast for its spot
    POST /v1/flow/{shop}/quote              what a request would cost (writes nothing)
    POST /v1/flow/{shop}/requests           ask for a lesson or gear → the request and its token
    POST /v1/flow/{shop}/mine               {tokens} → those requests as they are now
    POST /v1/flow/{shop}/cancel             {token} → cancelled
"""
from __future__ import annotations

from datetime import date as _date
from typing import Optional

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile

from vula.commerce import lessons

router = APIRouter(tags=["lessons"])             # mounted at /v1/commerce
public = APIRouter(tags=["lessons-public"])      # mounted at /v1/flow — the customer app (/flow/<business>)


def _refuse(exc: lessons.LessonError) -> HTTPException:
    return HTTPException(status_code=exc.status, detail=str(exc))


async def _who(request: Request, tenant_id: str) -> str:
    from vula import acting
    return await acting.from_dashboard(request.headers.get("authorization", ""), tenant_id) or ""


def _choices() -> dict:
    return {"levels": lessons.LEVELS, "parts": lessons.PARTS, "durations": lessons.DURATIONS}


@router.get("/{tenant_id}/admin/lessons/settings")
async def get_settings(tenant_id: str) -> dict:
    return {"settings": lessons.settings(tenant_id), **_choices()}


@router.put("/{tenant_id}/admin/lessons/settings")
async def put_settings(tenant_id: str, body: dict, request: Request) -> dict:
    from vula.api.commerce import _may_apply_stock
    if not await _may_apply_stock(request, tenant_id):
        raise HTTPException(status_code=403, detail="Only the owner or a manager can change lessons, gear and prices.")
    try:
        return {"settings": lessons.save_settings(tenant_id, body or {}), **_choices()}
    except lessons.LessonError as exc:
        raise _refuse(exc) from exc


@router.get("/{tenant_id}/admin/lessons")
async def list_requests(tenant_id: str, status: Optional[str] = None, since: Optional[str] = None) -> dict:
    if status and status not in lessons.STATUSES:
        raise HTTPException(status_code=400, detail="Unknown status.")
    if since:
        try:
            _date.fromisoformat(since)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="since must be a date (YYYY-MM-DD).") from exc
    rows = lessons.list_requests(tenant_id, status or "", since or "")
    return {"requests": rows, "waiting": sum(1 for r in rows if r["status"] == "requested"),
            "coaches": lessons.coaches(tenant_id), "enabled": lessons.settings(tenant_id)["enabled"]}


def _wanted(b: dict) -> dict:
    return {"kind": str(b.get("kind") or ""), "item_id": b.get("item_id"), "people": b.get("people", 1),
            "fmt": str(b.get("format") or "group"), "package": bool(b.get("package")),
            "extras": b.get("extras") if isinstance(b.get("extras"), dict) else None,
            "duration": str(b.get("duration") or "")}


@router.post("/{tenant_id}/admin/lessons/quote")
async def quote(tenant_id: str, body: dict) -> dict:
    w = _wanted(body or {})
    st = lessons.settings(tenant_id)
    try:
        if w["kind"] == "hire":
            cents, lines = lessons.quote_hire(st, str(w["item_id"] or ""), w["duration"], w["people"])
        else:
            cents, lines, _ = lessons.quote_lesson(st, str(w["item_id"] or ""), w["fmt"], w["people"], w["package"], w["extras"])
    except lessons.LessonError as exc:
        raise _refuse(exc) from exc
    return {"quote_cents": cents, "lines": lines}


@router.post("/{tenant_id}/admin/lessons")
async def add_request(tenant_id: str, body: dict) -> dict:
    b = body or {}
    try:
        return {"request": await lessons.request(
            tenant_id, **_wanted(b), wanted_date=b.get("wanted_date"), wanted_part=str(b.get("wanted_part") or "any"),
            level=str(b.get("level") or ""), name=b.get("name"), phone=b.get("phone"), notes=b.get("notes") or "",
            channel="dashboard", tell_team=False)}
    except lessons.LessonError as exc:
        raise _refuse(exc) from exc


@router.post("/{tenant_id}/admin/lessons/{request_id}/confirm")
async def confirm(tenant_id: str, request_id: str, body: dict, request: Request) -> dict:
    b = body or {}
    try:
        return {"request": await lessons.confirm(
            tenant_id, request_id, starts_at=b.get("starts_at"), coach_id=b.get("coach_id") or "",
            price_cents=b.get("price_cents"), invoice=bool(b.get("invoice")), by=await _who(request, tenant_id))}
    except lessons.LessonError as exc:
        raise _refuse(exc) from exc


@router.post("/{tenant_id}/admin/lessons/{request_id}/decline")
async def decline(tenant_id: str, request_id: str, body: dict, request: Request) -> dict:
    try:
        return {"request": await lessons.decline(tenant_id, request_id, (body or {}).get("reason"),
                                                 by=await _who(request, tenant_id))}
    except lessons.LessonError as exc:
        raise _refuse(exc) from exc


@router.post("/{tenant_id}/admin/lessons/{request_id}/status")
async def set_status(tenant_id: str, request_id: str, body: dict) -> dict:
    try:
        return {"request": lessons.set_status(tenant_id, request_id, str((body or {}).get("status") or ""))}
    except lessons.LessonError as exc:
        raise _refuse(exc) from exc


@router.get("/{tenant_id}/admin/lessons/conditions")
async def conditions_for(tenant_id: str, date: Optional[str] = None, sport: str = "surf") -> dict:
    from vula.commerce import conditions
    spot = lessons.settings(tenant_id).get("spot")
    if not spot:
        return {"spot": None, "hours": []}
    try:
        day = _date.fromisoformat(date) if date else lessons._now().astimezone(lessons.SAST).date()
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="date must be YYYY-MM-DD.") from exc
    try:
        hours = await conditions.forecast(spot["lat"], spot["lng"], day)
    except Exception:  # noqa: BLE001 — the forecast is a guide; the screen says it couldn't be read
        return {"spot": spot, "date": day.isoformat(), "hours": [], "unavailable": True}
    return {"spot": spot, "date": day.isoformat(),
            "hours": conditions.day_view(hours, sport if sport in lessons.SPORTS else "surf")}


def _this_month() -> str:
    return lessons._now().astimezone(lessons.SAST).strftime("%Y-%m")


@router.get("/{tenant_id}/admin/lessons/coaches")
async def coaches_month(tenant_id: str, request: Request, month: Optional[str] = None) -> dict:
    """Every coach's month: lessons done, their value and the coach's share. Owner or manager only."""
    from vula.api.commerce import _may_apply_stock
    if not await _may_apply_stock(request, tenant_id):
        raise HTTPException(status_code=403, detail="Only the owner or a manager can see every coach's figures.")
    try:
        return lessons.coach_month(tenant_id, month or _this_month())
    except lessons.LessonError as exc:
        raise _refuse(exc) from exc


@router.get("/{tenant_id}/admin/lessons/mine")
async def my_lessons(tenant_id: str, request: Request, month: Optional[str] = None) -> dict:
    """The signed-in coach's own lessons to come and their own month. Who they are comes from the
    login, never from the request."""
    try:
        return lessons.mine(tenant_id, await _who(request, tenant_id), month or _this_month())
    except lessons.LessonError as exc:
        raise _refuse(exc) from exc


@router.get("/{tenant_id}/admin/lessons/sharing")
async def sharing(tenant_id: str, request: Request, month: Optional[str] = None) -> dict:
    """Points for sharing reports from the beach: the board the team shares (first names, counts
    and points — no money) and the signed-in person's own row."""
    from vula.commerce import beach_reports
    try:
        return beach_reports.sharing_points(tenant_id, month or _this_month(), await _who(request, tenant_id))
    except lessons.LessonError as exc:
        raise _refuse(exc) from exc


@router.get("/{tenant_id}/admin/lessons/beach-report")
async def beach_report(tenant_id: str) -> dict:
    from vula.commerce import beach_reports
    return {"report": beach_reports.latest(tenant_id)}


@router.post("/{tenant_id}/admin/lessons/beach-report")
async def add_beach_report(tenant_id: str, request: Request, file: UploadFile = File(...), note: str = Form("")) -> dict:
    """A photo of the water from the beach → a reading in fixed words. The photo is not kept."""
    from vula.commerce import beach_reports
    data = await file.read(beach_reports.MAX_UPLOAD + 1)
    try:
        return {"report": await beach_reports.add(tenant_id, data, who=await _who(request, tenant_id), note=note)}
    except beach_reports.ReportError as exc:
        raise HTTPException(status_code=exc.status, detail=str(exc)) from exc


# ── the customer app ──────────────────────────────────────────────────────────────────────────
_RECENT: dict = {}


def _throttled(key: str, per_minute: int) -> bool:
    import time
    from collections import deque
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


def _shop(shop: str) -> str:
    """The business behind an app address (its id, with - or _) — the same 404 for one that
    doesn't exist, is paused, or isn't taking lesson requests."""
    import re
    from vula.api import tenants
    label = str(shop or "").strip().lower()
    if re.fullmatch(r"[a-z0-9][a-z0-9_-]{1,62}", label):
        for cand in dict.fromkeys((label, label.replace("-", "_"))):
            cfg = tenants.get_config(cand) or {}
            if cfg.get("tenant_id") == cand and tenants.is_active(cand) and lessons.settings(cand)["enabled"]:
                return cand
    raise HTTPException(status_code=404, detail="Not Found")


def _limit(request: Request, what: str, per_minute: int) -> None:
    if _throttled(f"{what}|{_ip(request)}", per_minute):
        raise HTTPException(status_code=429, detail="Too many tries. Wait a minute and try again.")


@public.get("/{shop}")
async def flow_shop(shop: str, request: Request) -> dict:
    from vula.api.commerce import public_brand
    _limit(request, "shop", 60)
    tenant_id = _shop(shop)
    brand = await public_brand(tenant_id)
    digits = "".join(ch for ch in str(brand.get("whatsapp") or "") if ch.isdigit())
    if len(digits) == 10 and digits.startswith("0"):
        digits = "27" + digits[1:]
    return {"shop": tenant_id.replace("_", "-"), "name": brand.get("name"), "partner": brand.get("partner"),
            "brand": {k: brand.get(k) for k in ("accent_color", "secondary_color", "ink_color", "logo_url", "tagline")},
            "whatsapp": digits if 9 <= len(digits) <= 15 else None,
            **lessons.public_settings(lessons.settings(tenant_id))}


@public.get("/{shop}/conditions")
async def flow_conditions(shop: str, request: Request, date: Optional[str] = None) -> dict:
    _limit(request, "cond", 30)
    from vula.commerce import beach_reports
    tenant_id = _shop(shop)
    out = await conditions_for(tenant_id, date)
    if out.get("spot"):
        out["spot"] = {"name": out["spot"]["name"]}
    out["report"] = beach_reports.for_customers(beach_reports.latest(tenant_id))   # seen from the beach, last 4 hours
    return out


@public.post("/{shop}/quote")
async def flow_quote(shop: str, body: dict, request: Request) -> dict:
    _limit(request, "quote", 60)
    return await quote(_shop(shop), body)


@public.post("/{shop}/requests")
async def flow_request(shop: str, body: dict, request: Request) -> dict:
    """A public endpoint that makes a request the team must answer: throttled per address, and one
    number can have at most five open."""
    _limit(request, "ask", 6)
    tenant_id = _shop(shop)
    b = body or {}
    try:
        if len(lessons.upcoming_for(tenant_id, str(b.get("phone") or ""))) >= 5:
            raise lessons.LessonError("You already have five requests open. Message us to make another.", 409)
        made = await lessons.request(
            tenant_id, **_wanted(b), wanted_date=b.get("wanted_date"), wanted_part=str(b.get("wanted_part") or "any"),
            level=str(b.get("level") or ""), name=b.get("name"), phone=b.get("phone"), notes=b.get("notes") or "",
            channel="app")
    except lessons.LessonError as exc:
        raise _refuse(exc) from exc
    return {"request": lessons.for_customer(made)}


@public.post("/{shop}/mine")
async def flow_mine(shop: str, body: dict, request: Request) -> dict:
    _limit(request, "mine", 30)
    tokens = (body or {}).get("tokens")
    if not isinstance(tokens, list) or not all(isinstance(t, str) for t in tokens):
        raise HTTPException(status_code=400, detail="tokens must be a list.")
    return {"requests": lessons.by_tokens(_shop(shop), tokens)}


@public.post("/{shop}/cancel")
async def flow_cancel(shop: str, body: dict, request: Request) -> dict:
    _limit(request, "cancel", 10)
    done = await lessons.cancel_by_token(_shop(shop), (body or {}).get("token"))
    if not done or done["status"] != "cancelled":
        raise HTTPException(status_code=409, detail="That request can't be cancelled here. Message us.")
    return {"request": done}
