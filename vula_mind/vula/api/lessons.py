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
    GET  /v1/flow/account                   a signed-in customer's own requests at every school (401 otherwise)
    GET  /v1/flow/directory?lat=&lng=       the schools that asked to be listed, nearest first with a position
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


def _settings_out(tenant_id: str, st: dict) -> dict:
    from vula.commerce import lesson_directory
    return {"settings": st, "directory": lesson_directory.status(tenant_id, st), "sports": list(lessons.SPORTS), **_choices()}


def _choices() -> dict:
    return {"levels": lessons.LEVELS, "parts": lessons.PARTS, "durations": lessons.DURATIONS}


@router.get("/{tenant_id}/admin/lessons/settings")
async def get_settings(tenant_id: str) -> dict:
    from vula.commerce import pay_page
    out = _settings_out(tenant_id, lessons.settings(tenant_id))
    try:                                               # what a link to pay would offer the customer
        out["pay"] = {"card": await pay_page.has_gateway(tenant_id), "eft": bool(pay_page.eft_details(tenant_id))}
    except Exception:  # noqa: BLE001
        out["pay"] = {"card": False, "eft": False}
    return out


@router.put("/{tenant_id}/admin/lessons/settings")
async def put_settings(tenant_id: str, body: dict, request: Request) -> dict:
    from vula.api.commerce import _may_apply_stock
    if not await _may_apply_stock(request, tenant_id):
        raise HTTPException(status_code=403, detail="Only the owner or a manager can change lessons, gear and prices.")
    try:
        return _settings_out(tenant_id, lessons.save_settings(tenant_id, body or {}))
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
            price_cents=b.get("price_cents"), invoice=bool(b.get("invoice")), by=await _who(request, tenant_id),
            allow_clash=b.get("allow_clash") is True, pay_link=b.get("pay_link") is True)}
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
        out = lessons.set_status(tenant_id, request_id, str((body or {}).get("status") or ""))
        if out["status"] == "cancelled":              # a coach from another school hears it is off
            await lessons._guest_cancelled(tenant_id, out)
        return {"request": out}
    except lessons.LessonError as exc:
        raise _refuse(exc) from exc


@router.get("/{tenant_id}/admin/lessons/conditions")
async def conditions_for(tenant_id: str, date: Optional[str] = None, sport: str = "surf") -> dict:
    from vula.commerce import conditions, spot_cameras
    spot = lessons.settings(tenant_id).get("spot")
    if not spot:
        return {"spot": None, "hours": [], "cameras": []}
    cameras = spot_cameras.near(spot.get("lat"), spot.get("lng"))     # live cameras near the spot
    try:
        day = _date.fromisoformat(date) if date else lessons._now().astimezone(lessons.SAST).date()
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="date must be YYYY-MM-DD.") from exc
    try:
        hours = conditions.with_shore(await conditions.forecast(spot["lat"], spot["lng"], day), spot.get("faces"))
    except Exception:  # noqa: BLE001 — the forecast is a guide; the screen says it couldn't be read
        return {"spot": spot, "date": day.isoformat(), "hours": [], "unavailable": True, "cameras": cameras}
    taught = sorted({x.get("sport") for x in (lessons.settings(tenant_id).get("lessons") or [])} - {None})
    models = await conditions.wind_models(spot["lat"], spot["lng"], day)
    return {"spot": spot, "date": day.isoformat(), "cameras": cameras,
            "summary": conditions.brief(hours, models, taught),     # the day in plain words
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


# ── each coach's week, and the coach's own side ───────────────────────────────────────────────
async def _me(request: Request, tenant_id: str) -> dict:
    """The team member behind this login — never taken from the request."""
    me = lessons.member_for(tenant_id, await _who(request, tenant_id))
    if not me or not me["id"]:
        raise HTTPException(status_code=403, detail="This is for a member of the school's team.")
    return me


@router.get("/{tenant_id}/admin/lessons/calendar")
async def lesson_calendar_week(tenant_id: str, request: Request, week: Optional[str] = None) -> dict:
    """The week for each coach: sessions, days away, working hours. An owner or manager sees every
    coach; anyone else on the team sees only their own."""
    from vula.api.commerce import _may_apply_stock
    from vula.commerce import lesson_calendar
    try:
        if await _may_apply_stock(request, tenant_id):
            return {**lesson_calendar.week(tenant_id, week), "everyone": True}
        me = await _me(request, tenant_id)
        return {**lesson_calendar.week(tenant_id, week, only_coach=me["id"]), "everyone": False}
    except lessons.LessonError as exc:
        raise _refuse(exc) from exc


@router.get("/{tenant_id}/admin/lessons/{request_id}/check")
async def lesson_check(tenant_id: str, request_id: str, coach_id: str, starts_at: str) -> dict:
    """What confirming this request with this coach at this time would run into, before it is saved."""
    from vula.commerce import lesson_calendar
    try:
        r = lessons._get(tenant_id, request_id)
        return lesson_calendar.check(tenant_id, coach_id, lessons._when(starts_at), r.get("item_id") or "",
                                     int(r.get("party_size") or 1), request_id)
    except (lessons.LessonError, ValueError, TypeError) as exc:
        raise HTTPException(status_code=400, detail="Choose the day, the time and the coach.") from exc


@router.get("/{tenant_id}/admin/lessons/my-profile")
async def my_coach_profile(tenant_id: str, request: Request) -> dict:
    from vula.commerce import lesson_directory
    me = await _me(request, tenant_id)
    return {"me": me, "profile": lesson_directory.own_profile(tenant_id, me["id"]), "sports": list(lessons.SPORTS), **_choices()}


@router.put("/{tenant_id}/admin/lessons/my-profile")
async def save_my_coach_profile(tenant_id: str, body: dict, request: Request) -> dict:
    """A coach saves their own profile and favourite spots — only their own, never whether it shows."""
    from vula.commerce import lesson_directory
    me = await _me(request, tenant_id)
    try:
        return {"me": me, "profile": lesson_directory.save_own(tenant_id, me["id"], body or {})}
    except lessons.LessonError as exc:
        raise _refuse(exc) from exc


def _roster():
    from vula.tap import api as tap_api
    return tap_api.get_service().shifts


@router.get("/{tenant_id}/admin/lessons/my-availability")
async def my_availability(tenant_id: str, request: Request) -> dict:
    """The hours the signed-in coach said they can work, and their days away (the roster's own)."""
    me = await _me(request, tenant_id)
    return {"me": me, **_roster().availability(tenant_id, me["id"])}


@router.post("/{tenant_id}/admin/lessons/my-availability")
async def add_my_availability(tenant_id: str, body: dict, request: Request) -> dict:
    """{weekday, start, end} adds weekly hours; {from, to, note} adds days away. Only the caller's own."""
    from vula.tap.shifts import ShiftError
    me = await _me(request, tenant_id)
    b = body or {}
    try:
        if "weekday" in b:
            return {"me": me, **_roster().add_weekly(tenant_id, me["id"], b.get("weekday"), b.get("start"), b.get("end"))}
        return {"me": me, **_roster().add_time_off(tenant_id, me["id"], b.get("from"), b.get("to"), b.get("note"))}
    except ShiftError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.delete("/{tenant_id}/admin/lessons/my-availability/{row_id}")
async def remove_my_availability(tenant_id: str, row_id: str, request: Request) -> dict:
    from vula.tap.shifts import ShiftError
    me = await _me(request, tenant_id)
    try:
        return {"me": me, **_roster().remove_availability(tenant_id, me["id"], row_id)}
    except ShiftError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


# ── a coach from another school (vula/commerce/coach_pool.py) ──────────────────────────────────
@router.get("/{tenant_id}/admin/lessons/pool/mine")
async def pool_mine(tenant_id: str, request: Request, month: Optional[str] = None) -> dict:
    """The signed-in coach: whether other schools may ask them, what they were asked and took."""
    from vula.commerce import coach_pool
    me = await _me(request, tenant_id)
    try:
        return coach_pool.mine(tenant_id, me["id"], month or _this_month())
    except lessons.LessonError as exc:
        raise _refuse(exc) from exc


@router.put("/{tenant_id}/admin/lessons/pool/mine")
async def pool_choice(tenant_id: str, body: dict, request: Request) -> dict:
    """The coach's own choice — their school has no say in it."""
    from vula.commerce import coach_pool
    me = await _me(request, tenant_id)
    try:
        coach_pool.set_open(tenant_id, me["id"], (body or {}).get("open"))
        return coach_pool.mine(tenant_id, me["id"], _this_month())
    except lessons.LessonError as exc:
        raise _refuse(exc) from exc


@router.post("/{tenant_id}/admin/lessons/pool/offers/{offer_id}/answer")
async def pool_answer(tenant_id: str, offer_id: str, body: dict, request: Request) -> dict:
    from vula.commerce import coach_pool
    me = await _me(request, tenant_id)
    try:
        return await coach_pool.answer(tenant_id, me["id"], offer_id, (body or {}).get("accept") is True)
    except lessons.LessonError as exc:
        raise _refuse(exc) from exc


async def _pool_manager(request: Request, tenant_id: str) -> None:
    from vula.api.commerce import _may_apply_stock
    if not await _may_apply_stock(request, tenant_id):
        raise HTTPException(status_code=403, detail="Only the owner or a manager can ask a coach from another school.")


@router.get("/{tenant_id}/admin/lessons/{request_id}/pool")
async def pool_candidates(tenant_id: str, request_id: str, request: Request, starts_at: Optional[str] = None) -> dict:
    """Who was asked for this lesson; with a time, the coaches of other schools who are free then."""
    from vula.commerce import coach_pool
    await _pool_manager(request, tenant_id)
    try:
        out = {"offers": coach_pool.for_request(tenant_id, request_id), "available": coach_pool.partner_id(tenant_id) is not None}
        if starts_at:
            out.update(coach_pool.candidates(tenant_id, request_id, starts_at))
        return out
    except lessons.LessonError as exc:
        raise _refuse(exc) from exc


@router.post("/{tenant_id}/admin/lessons/{request_id}/pool")
async def pool_ask(tenant_id: str, request_id: str, body: dict, request: Request) -> dict:
    from vula.commerce import coach_pool
    await _pool_manager(request, tenant_id)
    b = body or {}
    try:
        return await coach_pool.ask(tenant_id, request_id, b.get("starts_at"), b.get("coaches"), by=await _who(request, tenant_id))
    except lessons.LessonError as exc:
        raise _refuse(exc) from exc


@router.delete("/{tenant_id}/admin/lessons/{request_id}/pool")
async def pool_release(tenant_id: str, request_id: str, request: Request) -> dict:
    from vula.commerce import coach_pool
    await _pool_manager(request, tenant_id)
    try:
        return await coach_pool.release_and_tell(tenant_id, request_id)
    except lessons.LessonError as exc:
        raise _refuse(exc) from exc


@router.get("/{tenant_id}/admin/lessons/{request_id}/customer")
async def lesson_customer(tenant_id: str, request_id: str) -> dict:
    """The profile of the customer behind this booking, as far as a coach needs it for the lesson."""
    from vula.commerce import lesson_customers
    try:
        return lesson_customers.for_school(tenant_id, request_id)
    except lesson_customers.ProfileError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/{tenant_id}/admin/lessons/{request_id}/level")
async def lesson_confirm_level(tenant_id: str, request_id: str, body: dict, request: Request) -> dict:
    """A coach confirms the customer's level in this lesson's sport, once the lesson is done."""
    from vula.commerce import lesson_customers
    try:
        return lesson_customers.confirm_level(tenant_id, request_id, (body or {}).get("level"), await _who(request, tenant_id))
    except lesson_customers.ProfileError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.get("/{tenant_id}/admin/lessons/ratings")
async def lesson_ratings_for_school(tenant_id: str, request: Request) -> dict:
    """Customers' ratings: the school's and each coach's average, and the latest comments.
    Owner or manager only — the comments are the customers' words to the school."""
    from vula.api.commerce import _may_apply_stock
    from vula.commerce import lesson_ratings
    if not await _may_apply_stock(request, tenant_id):
        raise HTTPException(status_code=403, detail="Only the owner or a manager can see the ratings.")
    return lesson_ratings.for_school(tenant_id)


@router.get("/{tenant_id}/admin/lessons/partner-fees")
async def partner_fees_mine(tenant_id: str, request: Request) -> dict:
    """The school's own fee to its partner: the rate, this month so far, its statements and how to
    pay. Owner or manager only."""
    from vula.api.commerce import _may_apply_stock
    from vula.commerce import partner_fees
    if not await _may_apply_stock(request, tenant_id):
        raise HTTPException(status_code=403, detail="Only the owner or a manager can see this.")
    try:
        return partner_fees.for_school(tenant_id)
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


def _coord(raw: Optional[str], lo: float, hi: float) -> Optional[float]:
    try:
        v = float(raw) if raw not in (None, "") else None
    except ValueError:
        v = None
    if v is not None and not lo <= v <= hi:
        v = None
    return v


async def _customer(request: Request) -> Optional[str]:
    """The id of the signed-in customer behind this request, or None. A customer's login is an
    ordinary sign-in with no business behind it; here it only ever opens that person's own requests."""
    from vula.api.master_auth import _verify_jwt
    token = (request.headers.get("authorization") or "").removeprefix("Bearer ").strip()
    if not token:
        return None
    user = await _verify_jwt(token)
    return str(user["id"]) if user and user.get("id") else None


@public.get("/account")
async def flow_account(request: Request) -> dict:
    """The signed-in customer's own requests at every school. 401 without a valid sign-in."""
    _limit(request, "account", 30)
    uid = await _customer(request)
    if not uid:
        raise HTTPException(status_code=401, detail="Sign in to see your bookings.")
    return lessons.for_account(uid)


async def _signed_in(request: Request) -> dict:
    """The signed-in customer's own sign-in record, or 401."""
    from vula.api.master_auth import _verify_jwt
    token = (request.headers.get("authorization") or "").removeprefix("Bearer ").strip()
    user = await _verify_jwt(token) if token else None
    if not user or not user.get("id"):
        raise HTTPException(status_code=401, detail="Sign in to keep a profile.")
    return user


@public.get("/profile")
async def flow_profile(request: Request) -> dict:
    """The signed-in customer's own profile, the sports and skills it can hold, and their coaches."""
    _limit(request, "profile", 30)
    from vula.commerce import lesson_customers
    user = await _signed_in(request)
    meta = user.get("user_metadata") or {}
    return {"profile": lesson_customers.get(str(user["id"])), "catalog": lesson_customers.catalog(),
            "coaches": lesson_customers.my_coaches(str(user["id"])),
            "account_photo": bool(meta.get("avatar_url") or meta.get("picture"))}


@public.put("/profile")
async def flow_profile_save(body: dict, request: Request) -> dict:
    _limit(request, "profile_save", 12)
    from vula.commerce import lesson_customers
    user = await _signed_in(request)
    meta = user.get("user_metadata") or {}
    try:
        return {"profile": lesson_customers.save(str(user["id"]), body or {}, meta.get("avatar_url") or meta.get("picture"))}
    except lesson_customers.ProfileError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@public.delete("/profile")
async def flow_profile_delete(request: Request) -> dict:
    """The customer deletes their own profile. Their lesson records stay with the schools."""
    _limit(request, "profile_save", 12)
    from vula.commerce import lesson_customers
    user = await _signed_in(request)
    try:
        return {"deleted": lesson_customers.erase(str(user["id"]))}
    except lesson_customers.ProfileError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@public.get("/directory")
async def flow_directory(request: Request, lat: Optional[str] = None, lng: Optional[str] = None) -> dict:
    """The schools that asked to be listed: by name, or nearest first when the customer's position
    is given. That position sorts this one answer and is not kept."""
    from vula.commerce import lesson_directory
    _limit(request, "dir", 30)
    la, lo = _coord(lat, -90, 90), _coord(lng, -180, 180)
    if la is None or lo is None:                    # half a position is no position
        la = lo = None
    rows = lesson_directory.near(await lesson_directory.entries(), la, lo)
    return {"schools": await lesson_directory.with_conditions(rows), "sorted_by": "distance" if la is not None else "name"}


@public.get("/{shop}")
async def flow_shop(shop: str, request: Request) -> dict:
    from vula.api.commerce import public_brand
    _limit(request, "shop", 60)
    from vula.commerce import lesson_directory, lesson_ratings
    tenant_id = _shop(shop)
    st = lessons.settings(tenant_id)
    brand = await public_brand(tenant_id)
    digits = "".join(ch for ch in str(brand.get("whatsapp") or "") if ch.isdigit())
    if len(digits) == 10 and digits.startswith("0"):
        digits = "27" + digits[1:]
    return {"shop": tenant_id.replace("_", "-"), "name": brand.get("name"), "partner": brand.get("partner"),
            "brand": {k: brand.get(k) for k in ("accent_color", "secondary_color", "ink_color", "logo_url", "tagline")},
            # the school's OWN colours, when it has chosen any: its page wears them once it is picked
            "own_look": brand.get("own_look"),
            "theme": lesson_directory.theme_of(st),          # a ready-made look, when the school picked one
            "rating": lesson_ratings.public(tenant_id),       # customers' stars, once anyone has rated it
            "whatsapp": digits if 9 <= len(digits) <= 15 else None,
            "coaches": lesson_directory.public_coaches(tenant_id, st), **lessons.public_settings(st)}


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
            channel="app", customer_user_id=await _customer(request))
    except lessons.LessonError as exc:
        raise _refuse(exc) from exc
    return {"request": lessons.for_customer(made)}


@public.post("/{shop}/mine")
async def flow_mine(shop: str, body: dict, request: Request) -> dict:
    _limit(request, "mine", 30)
    tokens = (body or {}).get("tokens")
    if not isinstance(tokens, list) or not all(isinstance(t, str) for t in tokens):
        raise HTTPException(status_code=400, detail="tokens must be a list.")
    tenant_id = _shop(shop)
    return {"requests": lessons.by_tokens(tenant_id, tokens), "progress": lessons.progress_by_tokens(tenant_id, tokens)}


@public.post("/{shop}/rate")
async def flow_rate(shop: str, body: dict, request: Request) -> dict:
    """The customer rates the finished lesson their token opens: the school, and its coach. Once."""
    _limit(request, "rate", 10)
    from vula.commerce import lesson_ratings
    b = body or {}
    try:
        return {"rating": lesson_ratings.rate(_shop(shop), b.get("token"), b.get("stars"), b.get("coach_stars"), b.get("comment"))}
    except lesson_ratings.RatingError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@public.get("/{shop}/next-good-day")
async def flow_next_good_day(shop: str, request: Request, level: str = "") -> dict:
    """The first good part of a day in the next five, at the school's spot, for its first sport."""
    _limit(request, "nextday", 20)
    from vula.commerce import lesson_progress
    st = lessons.settings(_shop(shop))
    kinds = st.get("lessons") or []
    if not st.get("spot") or not kinds:
        return {"next": None}
    return {"next": await lesson_progress.next_good_day(st["spot"], kinds[0].get("sport") or "surf",
                                                        level if level in lessons.LEVELS else ""),
            "lesson_id": kinds[0]["id"]}


@public.post("/{shop}/cancel")
async def flow_cancel(shop: str, body: dict, request: Request) -> dict:
    _limit(request, "cancel", 10)
    done = await lessons.cancel_by_token(_shop(shop), (body or {}).get("token"))
    if not done or done["status"] != "cancelled":
        raise HTTPException(status_code=409, detail="That request can't be cancelled here. Message us.")
    return {"request": done}
