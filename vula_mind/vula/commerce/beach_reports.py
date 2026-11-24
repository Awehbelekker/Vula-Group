"""What the water looks like right now, from a photo a coach takes on the beach (24 Nov 2026,
migration 304).

FlowCrew's first design had this screen ("AI-powered condition analysis") returning random
numbers. Here the photo is really read: a vision model answers in a few fixed words — the surface,
a rough wave size, how busy the water is — and anything outside those words is dropped in code. A
picture that doesn't show the water gets no reading at all. The reading is a person's-eye
description, never a measurement: sizes are body heights (knee, waist, head), not metres.

The photo is made smaller, its location and camera details are removed, and it is NOT kept — it may
show people. Only the reading, who took it and when are stored. Customers see the reading and the
coach's own note beside the forecast; a reading older than four hours is no longer shown.
"""
from __future__ import annotations

import base64
import io
import json
import logging
import re
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional
from zoneinfo import ZoneInfo

log = logging.getLogger(__name__)

SAST = ZoneInfo("Africa/Johannesburg")
SURFACE = {"glassy": "glassy", "clean": "clean", "bumpy": "a bit bumpy", "choppy": "choppy", "blown_out": "blown out"}
WAVE_SIZE = {"flat": "flat", "knee": "about knee high", "waist": "about waist high", "chest": "about chest high",
             "head": "about head high", "overhead": "overhead"}
CROWD = {"empty": "nobody out", "few": "a few people out", "busy": "busy", "packed": "packed"}
CONFIDENCE = ("low", "medium", "high")
FRESH_HOURS = 4
MAX_UPLOAD = 12 * 1024 * 1024
# Points for sharing a report. Fixed here and shown to the team, so everyone knows what counts.
POINTS_REPORT = 10            # each report that was good enough to show customers
POINTS_FIRST = 5              # extra for the day's first report from this business
POINTS_GAP_HOURS = 2          # the same person's next report counts only this long after their last
POINTS_DAILY = 3              # at most this many of one person's reports count in a day
POINTS_RULE = (f"{POINTS_REPORT} points for each beach report customers could see, {POINTS_FIRST} more for the "
               f"first one of the day. Up to {POINTS_DAILY} a day count, at least {POINTS_GAP_HOURS} hours apart.")
_PROMPT = (
    "This photo was taken from a beach by a surf coach to record the water conditions. "
    "Reply with JSON only, using exactly these keys and only the listed values:\n"
    '{"water_visible": true or false (false if the photo does not clearly show the sea or the waves),\n'
    ' "surface": one of "glassy", "clean", "bumpy", "choppy", "blown_out",\n'
    ' "wave_size": one of "flat", "knee", "waist", "chest", "head", "overhead" '
    "(the breaking waves against a standing adult; use people or boards in the picture for scale),\n"
    ' "crowd": one of "empty", "few", "busy", "packed" (people in the water),\n'
    ' "confidence": one of "low", "medium", "high"}\n'
    "Use null for anything you cannot judge from the picture. Do not guess. Describe only what is visible.")


class ReportError(Exception):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


def _db():
    from vula.commerce import service
    return service._client()


def _now() -> datetime:
    return datetime.now(timezone.utc)


def prepare(image_bytes: bytes) -> bytes:
    """The photo as a small JPEG with nothing attached to it (no location, no camera details).
    Raises ReportError when the upload isn't a picture."""
    from PIL import Image, ImageOps
    if not image_bytes or len(image_bytes) > MAX_UPLOAD:
        raise ReportError("Choose a photo under 12 MB.")
    try:
        im = Image.open(io.BytesIO(image_bytes))
        im = ImageOps.exif_transpose(im).convert("RGB")           # upright, then the details are left behind
    except Exception as exc:  # noqa: BLE001
        raise ReportError("That file isn't a photo we can read.") from exc
    if min(im.size) < 200:
        raise ReportError("That photo is too small to read.")
    im.thumbnail((1280, 1280))
    out = io.BytesIO()
    im.save(out, format="JPEG", quality=82)
    return out.getvalue()


def clean(raw: Any) -> Dict[str, Any]:
    """The model's answer, kept only where it used the fixed words. A picture with no water in it
    carries no reading at all."""
    d = raw if isinstance(raw, dict) else {}
    seen = d.get("water_visible") is True
    pick = (lambda key, allowed: d.get(key) if seen and isinstance(d.get(key), str) and d.get(key) in allowed else None)
    return {"water_visible": seen, "surface": pick("surface", SURFACE), "wave_size": pick("wave_size", WAVE_SIZE),
            "crowd": pick("crowd", CROWD), "confidence": pick("confidence", CONFIDENCE)}


async def read_photo(jpeg: bytes) -> Optional[Dict[str, Any]]:
    """{"reading", "model"} from the vision model, or None when it couldn't be asked or didn't
    answer in JSON — then nothing is saved (never a made-up reading)."""
    import litellm
    from core.llm_router import cloud_generation_kwargs, resolve_cloud_vision_route
    route = resolve_cloud_vision_route()
    if not route:
        return None
    model, api_key, api_base = route
    litellm.drop_params = True
    try:
        resp = await litellm.acompletion(
            model=model, temperature=0.0, max_tokens=120, api_key=api_key, api_base=api_base,
            **cloud_generation_kwargs(model),              # only providers that keep nothing
            messages=[{"role": "user", "content": [
                {"type": "text", "text": _PROMPT},
                {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64," + base64.b64encode(jpeg).decode()}}]}])
        found = re.search(r"\{.*\}", resp.choices[0].message.content or "", re.DOTALL)
        return {"reading": clean(json.loads(found.group(0))), "model": model} if found else None
    except Exception as exc:  # noqa: BLE001
        log.info("beach photo not read: %s", type(exc).__name__)
        return None


def words(r: Dict[str, Any]) -> str:
    """The reading in plain words, built here: 'clean, about waist high, a few people out'."""
    bits = [SURFACE.get(r.get("surface") or ""), WAVE_SIZE.get(r.get("wave_size") or ""), CROWD.get(r.get("crowd") or "")]
    return ", ".join(b for b in bits if b)


def _reporter(tenant_id: str, who: str) -> tuple:
    """(id, first name) of the team member behind a WhatsApp number or login, where one is found."""
    try:
        from vula import team_index
        digits = re.sub(r"\D", "", who or "")
        for m in team_index.active_members(tenant_id):
            mine = re.sub(r"\D", "", str(m.get("whatsapp") or ""))
            if (digits and mine and mine[-9:] == digits[-9:]) or (who and who == (m.get("email") or "")):
                return str(m.get("id") or ""), (m.get("name") or "").split(" ")[0]
    except Exception as exc:  # noqa: BLE001
        log.debug("reporter not found for %s: %s", tenant_id, exc)
    return (who or None), None


def _out(row: Dict[str, Any]) -> Dict[str, Any]:
    at = datetime.fromisoformat(str(row["taken_at"]).replace("Z", "+00:00")).astimezone(SAST)
    return {"id": row.get("id"), "taken_at": row["taken_at"], "time": at.strftime("%H:%M"), "day": at.date().isoformat(),
            "by": row.get("reporter_name"), "water_visible": bool(row.get("water_visible")),
            "surface": row.get("surface"), "wave_size": row.get("wave_size"), "crowd": row.get("crowd"),
            "confidence": row.get("confidence"), "coach_note": row.get("coach_note"), "text": words(row)}


async def add(tenant_id: str, image_bytes: bytes, *, who: str = "", note: Any = "") -> Dict[str, Any]:
    """Read a beach photo and keep the reading. The photo is not kept. Refused, saving nothing,
    when the photo can't be read or doesn't show the water."""
    from vula.commerce import lessons
    jpeg = prepare(image_bytes)
    got = await read_photo(jpeg)
    if not got:
        raise ReportError("The photo couldn't be read just now. Try again in a moment.", 502)
    reading = got["reading"]
    # a picture that is mostly people or sand gets a "low": not something to tell customers
    if not reading["water_visible"] or not (reading["surface"] or reading["wave_size"]) or reading["confidence"] == "low":
        raise ReportError("That photo doesn't show the water clearly enough to read. Take one facing the waves.", 422)
    member_id, first = _reporter(tenant_id, who)
    spot = (lessons.settings(tenant_id).get("spot") or {}).get("name")
    row = {"tenant_id": tenant_id, "spot_name": spot, "taken_at": _now().isoformat(), "reported_by": member_id,
           "reporter_name": first, "coach_note": re.sub(r"\s+", " ", str(note or "")).strip()[:160] or None,
           "model": str(got["model"])[:80], **reading}
    _db().table("commerce_beach_reports").insert(row).execute()
    back = latest(tenant_id)
    if not back or back["taken_at"] != row["taken_at"]:
        raise ReportError("The report didn't save. Please try again.", 500)
    return back


def latest(tenant_id: str, now: Optional[datetime] = None) -> Optional[Dict[str, Any]]:
    """The newest reading from the last four hours, or None."""
    since = ((now or _now()) - timedelta(hours=FRESH_HOURS)).isoformat()
    try:
        rows = (_db().table("commerce_beach_reports").select("*").eq("tenant_id", tenant_id).gte("taken_at", since)
                .order("taken_at", desc=True).limit(1).execute().data or [])
    except Exception as exc:  # noqa: BLE001 — migration 304 not applied: there is simply no report
        log.debug("beach reports unreadable for %s: %s", tenant_id, exc)
        return None
    return _out(rows[0]) if rows else None


def for_customers(report: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """What a customer sees of a report: when, the first name, the words and the coach's note."""
    if not report or not report.get("text"):
        return None
    return {k: report.get(k) for k in ("time", "day", "by", "text", "coach_note", "surface", "wave_size", "crowd")}


def sharing_points(tenant_id: str, month: str, who: str = "") -> Dict[str, Any]:
    """Who shared what from the beach in a month (South African time), in points: counted here
    from the saved readings, by the fixed rule in POINTS_RULE. Only current team members are
    listed, by first name. `me` is the signed-in person's own row. Points are a count — nothing
    is paid for them."""
    from vula import team_index
    from vula.commerce import lessons
    from vula.db_pages import all_rows
    lo, hi = lessons._month_bounds(month)
    rows = all_rows(lambda: _db().table("commerce_beach_reports").select("id,taken_at,reported_by")
                    .eq("tenant_id", tenant_id).gte("taken_at", lo).lt("taken_at", hi), cap=5000)
    rows.sort(key=lambda r: str(r["taken_at"]))
    team = {str(m.get("id")): (m.get("name") or "").split(" ")[0] or "Team member" for m in team_index.active_members(tenant_id)}
    by: Dict[str, Dict[str, Any]] = {}
    first_seen: set = set()                     # days that already have their first report
    last: Dict[str, datetime] = {}              # a person's last report that counted
    per_day: Dict[tuple, int] = {}
    for r in rows:
        at = datetime.fromisoformat(str(r["taken_at"]).replace("Z", "+00:00")).astimezone(SAST)
        day, pid = at.date(), str(r.get("reported_by") or "")
        first = day not in first_seen
        first_seen.add(day)
        if pid not in team:                     # a report nobody on the team is behind earns nothing
            continue
        c = by.setdefault(pid, {"member_id": pid, "name": team[pid], "reports": 0, "counted": 0, "firsts": 0, "points": 0})
        c["reports"] += 1
        if per_day.get((pid, day), 0) >= POINTS_DAILY or (pid in last and at - last[pid] < timedelta(hours=POINTS_GAP_HOURS)):
            continue
        per_day[(pid, day)] = per_day.get((pid, day), 0) + 1
        last[pid] = at
        c["counted"] += 1
        c["points"] += POINTS_REPORT
        if first:
            c["firsts"] += 1
            c["points"] += POINTS_FIRST
    board = sorted(by.values(), key=lambda c: (-c["points"], c["name"].lower()))
    mine = _reporter(tenant_id, who)[0] if who else None
    return {"month": month, "rule": POINTS_RULE, "board": board,
            "me": next((c for c in board if c["member_id"] == mine), None)}
