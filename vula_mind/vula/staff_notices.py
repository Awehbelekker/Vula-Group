"""A business's notices to its own staff on WhatsApp — announcements, a picture newsletter,
birthdays and public holidays (20 Oct 2026, Ian: "a platform where they could do broadcasting to
the staff, like for birthdays, on special days like public holidays, any notifications that they
need to know about"). Migration 218.

    notice     the owner or a manager writes it (dashboard › Team › Notices, or "tell all staff …"
               on WhatsApp), now or at a set time; "must read" ones get a Got it button, one
               reminder after a day, and the owner is told who hasn't read it yet
    birthday   on the day: a wish to the person, and (unless they'd rather not) a note to the team
    holiday    the day before a South African public holiday (vula/sa_holidays.py)

How it reaches people: an approved UTILITY template (vula/commerce/wa_templates.py
staff_templates) — free text only arrives within 24 hours of the person's last message, and a
notice must reach everyone. Someone who messaged in the last 23 hours gets plain text instead
(Meta doesn't charge for those). One recipient row per person per notice (unique), so a notice
can never reach the same person twice, however many times sending is retried.

Every write is read back by its caller; nothing here is guessed by a model.
"""
from __future__ import annotations

import asyncio
import logging
import re
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

SAST = timezone(timedelta(hours=2))
DEFAULTS = {"birthdays": False, "holidays": False, "send_hour": 8, "holiday_note": ""}
IN_WINDOW = timedelta(hours=23)          # free text is safe this long after their last message
REMIND_AFTER = timedelta(hours=24)
_PACE_S = 0.1                           # 10 a second, like the customer broadcasts
_MEMBER_COLS = ("id,name,whatsapp,role,active,birthday,birthday_share,notices_opt_out,"
                "last_message_at")

# "Got it ✅" (the template's button), or a short typed reply saying the same.
_ACK_WORDS = {"got it", "gotit", "ok", "okay", "noted", "seen", "read", "read it", "received",
              "will do", "sure", "thanks", "thank you", "thanx", "cool", "great", "perfect", "yes",
              "yebo", "ja", "ok thanks", "ok thank you", "noted thanks", "got it thanks", "ok noted",
              "understood", "shot", "sharp"}
_ACK_EMOJI = ("👍", "✅", "🙏", "👌")


def _client():
    from vula.commerce import service
    return service._client()


def _digits(phone: str) -> str:
    d = "".join(ch for ch in (phone or "") if ch.isdigit())
    return "27" + d[1:] if d.startswith("0") and len(d) == 10 else d


def _now() -> datetime:
    return datetime.now(timezone.utc)


def is_ack(text: str) -> bool:
    raw = (text or "").strip()
    words = re.sub(r"[^a-z ]+", " ", raw.lower())
    words = " ".join(words.split())
    if words in _ACK_WORDS:
        return True
    return not words and any(e in raw for e in _ACK_EMOJI)


# ── settings ───────────────────────────────────────────────────────────────────

def settings_for(tenant_id: str) -> Dict[str, Any]:
    try:
        rows = (_client().table("vula_tenant_config").select("staff_notices")
                .eq("tenant_id", tenant_id).limit(1).execute().data or [])
        saved = (rows[0].get("staff_notices") if rows else None) or {}
    except Exception as exc:
        logger.debug("staff notice settings unread (run migration 218?): %s", exc)
        saved = {}
    return {**DEFAULTS, **{k: v for k, v in saved.items() if k in DEFAULTS}}


def save_settings(tenant_id: str, patch: Dict[str, Any]) -> Dict[str, Any]:
    new = settings_for(tenant_id)
    for k, v in (patch or {}).items():
        if k in ("birthdays", "holidays"):
            new[k] = bool(v)
        elif k == "send_hour":
            new[k] = min(20, max(6, int(v)))
        elif k == "holiday_note":
            new[k] = str(v or "").strip()[:200]
    _client().table("vula_tenant_config").update({"staff_notices": new}).eq("tenant_id", tenant_id).execute()
    return settings_for(tenant_id)


def _business(tenant_id: str) -> str:
    try:
        from vula.api.tenants import get_config
        cfg = get_config(tenant_id) or {}
        return (cfg.get("trading_as") or cfg.get("display_name") or tenant_id).strip()
    except Exception:
        return tenant_id


# ── who it goes to ─────────────────────────────────────────────────────────────

def members(tenant_id: str) -> List[Dict[str, Any]]:
    return (_client().table("vula_team_members").select(_MEMBER_COLS)
            .eq("tenant_id", tenant_id).eq("active", True).execute().data or [])


def audience(tenant_id: str, spec: Optional[Dict[str, Any]], kind: str = "notice",
             team: Optional[List[Dict[str, Any]]] = None) -> List[Dict[str, Any]]:
    """Active members with a WhatsApp number that the spec picks: {"all": true}, {"roles": [...]}
    or {"ids": [...]}. Birthday and holiday messages skip anyone who opted out; work notices
    reach everyone."""
    spec = spec or {"all": True}
    rows = [m for m in (team if team is not None else members(tenant_id)) if m.get("whatsapp")]
    if spec.get("ids"):
        ids = {str(i) for i in spec["ids"]}
        rows = [m for m in rows if str(m.get("id")) in ids]
    elif spec.get("roles"):
        roles = {str(r).lower() for r in spec["roles"]}
        rows = [m for m in rows if (m.get("role") or "staff").lower() in roles]
    if kind in ("birthday", "holiday"):
        rows = [m for m in rows if not m.get("notices_opt_out")]
    return rows


# ── creating and sending ───────────────────────────────────────────────────────

def create(tenant_id: str, title: str, body: str, *, kind: str = "notice",
           image_url: Optional[str] = None, must_read: bool = False,
           audience_spec: Optional[Dict[str, Any]] = None, send_at: Optional[str] = None,
           created_by: Optional[str] = None) -> Dict[str, Any]:
    title, body = (title or "").strip()[:120], (body or "").strip()[:900]
    if not title or not body:
        raise ValueError("A notice needs a title and a message.")
    row = {"tenant_id": tenant_id, "kind": kind, "title": title, "body": body,
           "image_url": (image_url or None), "must_read": bool(must_read),
           "audience": audience_spec or {"all": True}, "created_by": created_by,
           "status": "scheduled" if send_at else "draft", "send_at": send_at}
    data = _client().table("vula_staff_notices").insert(row).execute().data or []
    if not data:
        raise RuntimeError("The notice wasn't saved.")
    return data[0]


def get(tenant_id: str, notice_id: str) -> Optional[Dict[str, Any]]:
    rows = (_client().table("vula_staff_notices").select("*").eq("tenant_id", tenant_id)
            .eq("id", notice_id).limit(1).execute().data or [])
    return rows[0] if rows else None


def _one_line(text: str) -> str:
    # Meta rejects template parameters with new lines, tabs or 4+ spaces in a row.
    return re.sub(r"\s+", " ", text or "").strip()


def _first_name(m: Dict[str, Any]) -> str:
    return ((m.get("name") or "").split() or ["there"])[0]


def message_text(notice: Dict[str, Any], member: Dict[str, Any], business: str) -> str:
    """The plain-text version (for someone inside the 24-hour window, and the preview)."""
    if notice.get("kind") in ("birthday", "holiday"):
        return f"Hi {_first_name(member)}, {notice['body']} From all of us at {business}."
    text = f"📢 *{notice['title']}*\n\n{notice['body']}"
    if notice.get("must_read"):
        text += "\n\nReply *got it* once you've read it."
    return text


def _template_call(tenant_id: str, notice: Dict[str, Any], member: Dict[str, Any],
                   business: str) -> tuple:
    from vula.commerce.job_config import template_name
    from vula.commerce import wa_templates as wt
    if notice.get("kind") in ("birthday", "holiday"):
        return (template_name(tenant_id, wt.STAFF_DAY_SUFFIX),
                [_first_name(member), _one_line(notice["body"]), business], None)
    suffix = wt.STAFF_NOTICE_IMAGE_SUFFIX if notice.get("image_url") else wt.STAFF_NOTICE_SUFFIX
    return (template_name(tenant_id, suffix),
            [business, _one_line(f"{notice['title']} — {notice['body']}")], notice.get("image_url"))


def _in_window(member: Dict[str, Any]) -> bool:
    last = member.get("last_message_at")
    if not last:
        return False
    try:
        t = datetime.fromisoformat(str(last).replace("Z", "+00:00"))
        return _now() - t < IN_WINDOW
    except Exception:
        return False


async def _deliver(tenant_id: str, notice: Dict[str, Any], member: Dict[str, Any],
                   business: str) -> tuple:
    """(wamid or "", error or None)."""
    from vula.api import whatsapp as wa
    phone = member["whatsapp"]
    if _in_window(member) and not notice.get("image_url"):
        ok = await wa._send_reply(phone, message_text(notice, member, business), tenant_id,
                                  idem_key=f"notice:{notice['id']}:{member['id']}")
        return ("", None) if ok else ("", "not delivered")
    name, params, image = _template_call(tenant_id, notice, member, business)
    wamid = await wa.send_template_message(tenant_id, phone, name, params, header_image_url=image)
    return (wamid, None) if wamid else ("", f"template {name} not sent (is it approved?)")


def _claim(tenant_id: str, notice_id: str) -> Optional[Dict[str, Any]]:
    """Move a draft/scheduled notice to sending — only one sender ever wins."""
    data = (_client().table("vula_staff_notices").update({"status": "sending"})
            .eq("tenant_id", tenant_id).eq("id", notice_id).in_("status", ["draft", "scheduled"])
            .execute().data or [])
    return data[0] if data else None


async def send(tenant_id: str, notice_id: str) -> Dict[str, Any]:
    """Send a notice to its audience now. Returns {sent, failed, total, names_failed}."""
    notice = _claim(tenant_id, notice_id)
    if not notice:
        current = get(tenant_id, notice_id)
        return {"error": "That notice was already sent or cancelled.",
                "status": (current or {}).get("status")}
    db = _client()
    business = _business(tenant_id)
    people = audience(tenant_id, notice.get("audience"), notice.get("kind") or "notice")
    sent, failed = 0, []
    for m in people:
        try:
            db.table("vula_staff_notice_recipients").insert({
                "notice_id": notice["id"], "tenant_id": tenant_id, "member_id": m["id"],
                "name": m.get("name"), "phone": _digits(m["whatsapp"])}).execute()
        except Exception:
            continue                      # already has a row — reached on an earlier attempt
        wamid, err = await _deliver(tenant_id, notice, m, business)
        patch = {"sent_at": _now().isoformat(), "wamid": wamid or None} if not err else {"failed": err}
        db.table("vula_staff_notice_recipients").update(patch).eq("notice_id", notice["id"]) \
            .eq("member_id", m["id"]).execute()
        if err:
            failed.append(m.get("name") or m["whatsapp"])
            continue
        sent += 1
        if notice.get("must_read"):
            from vula import open_questions
            open_questions.ask(tenant_id, m["whatsapp"], "staff_notice", notice["id"], notice["title"])
        await asyncio.sleep(_PACE_S)
    db.table("vula_staff_notices").update({"status": "sent", "sent_at": _now().isoformat()}) \
        .eq("id", notice["id"]).execute()
    return {"sent": sent, "failed": len(failed), "total": len(people), "names_failed": failed}


def cancel(tenant_id: str, notice_id: str) -> bool:
    data = (_client().table("vula_staff_notices").update({"status": "cancelled"})
            .eq("tenant_id", tenant_id).eq("id", notice_id).in_("status", ["draft", "scheduled"])
            .execute().data or [])
    return bool(data)


def set_opt_out(tenant_id: str, phone: str, opted_out: bool) -> Optional[str]:
    """STOP / START from a team member: birthday and holiday messages off / on (work notices
    still go). Returns their name, or None when the phone isn't on the team."""
    try:
        from vula import team_index
        m = team_index.member_for_phone(tenant_id, phone)
        if not m:
            return None
        _client().table("vula_team_members").update({"notices_opt_out": bool(opted_out)}) \
            .eq("tenant_id", tenant_id).eq("id", m["id"]).execute()
        return (m.get("name") or "").split()[0] if m.get("name") else ""
    except Exception as exc:
        logger.debug("staff notice opt-out skipped (run migration 218?): %s", exc)
        return None


# ── read ticks and "Got it" ────────────────────────────────────────────────────

def record_status(wamid: str, status: str) -> None:
    """Meta's delivered / read callback for a notice sent as a template."""
    col = {"delivered": "delivered_at", "read": "read_at"}.get(status)
    if not wamid or not col:
        return
    try:
        patch = {col: _now().isoformat()}
        if status == "read":
            patch["delivered_at"] = patch["read_at"]
        (_client().table("vula_staff_notice_recipients").update(patch)
         .eq("wamid", wamid).is_(col, "null").execute())
    except Exception as exc:
        logger.debug("notice status skipped: %s", exc)


def acknowledge(tenant_id: str, phone: str, notice_id: str) -> bool:
    now = _now().isoformat()
    data = (_client().table("vula_staff_notice_recipients")
            .update({"acknowledged_at": now})
            .eq("tenant_id", tenant_id).eq("notice_id", notice_id).eq("phone", _digits(phone))
            .is_("acknowledged_at", "null").execute().data or [])
    return bool(data)


def acknowledge_member(tenant_id: str, member_id: str, notice_id: str) -> bool:
    """The dashboard's Got it (the noticeboard)."""
    data = (_client().table("vula_staff_notice_recipients")
            .update({"acknowledged_at": _now().isoformat()})
            .eq("tenant_id", tenant_id).eq("notice_id", notice_id).eq("member_id", member_id)
            .is_("acknowledged_at", "null").execute().data or [])
    return bool(data)


def recipients(tenant_id: str, notice_id: str) -> List[Dict[str, Any]]:
    return (_client().table("vula_staff_notice_recipients")
            .select("member_id,name,phone,sent_at,failed,delivered_at,read_at,acknowledged_at")
            .eq("tenant_id", tenant_id).eq("notice_id", notice_id).execute().data or [])


def tally(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    sent = [r for r in rows if r.get("sent_at")]
    return {"total": len(rows), "sent": len(sent),
            "failed": sum(1 for r in rows if r.get("failed")),
            "delivered": sum(1 for r in sent if r.get("delivered_at") or r.get("read_at")),
            "read": sum(1 for r in sent if r.get("read_at") or r.get("acknowledged_at")),
            "acknowledged": sum(1 for r in sent if r.get("acknowledged_at")),
            "not_acknowledged": [r.get("name") or r.get("phone") for r in sent
                                 if not r.get("acknowledged_at")]}


def list_notices(tenant_id: str, limit: int = 50) -> List[Dict[str, Any]]:
    rows = (_client().table("vula_staff_notices").select("*").eq("tenant_id", tenant_id)
            .order("created_at", desc=True).limit(limit).execute().data or [])
    if not rows:
        return []
    ids = [r["id"] for r in rows]
    rcpts = (_client().table("vula_staff_notice_recipients")
             .select("notice_id,member_id,name,phone,sent_at,failed,delivered_at,read_at,acknowledged_at")
             .eq("tenant_id", tenant_id).in_("notice_id", ids).execute().data or [])
    by: Dict[str, list] = {}
    for r in rcpts:
        by.setdefault(r["notice_id"], []).append(r)
    for n in rows:
        n["counts"] = tally(by.get(n["id"], []))
        n["recipients"] = by.get(n["id"], [])
    return rows


# ── the day's birthdays and tomorrow's public holiday ─────────────────────────

def _birthday_today(m: Dict[str, Any], today: date) -> bool:
    b = (m.get("birthday") or "").strip()
    if not b:
        return False
    if b == "02-29" and today.month == 2 and today.day == 28:
        from calendar import isleap
        return not isleap(today.year)
    return b == today.strftime("%m-%d")


def _exists(tenant_id: str, title: str) -> bool:
    rows = (_client().table("vula_staff_notices").select("id").eq("tenant_id", tenant_id)
            .eq("title", title).limit(1).execute().data or [])
    return bool(rows)


async def daily_pass(tenant_id: str, today: date) -> List[str]:
    """Birthdays today and tomorrow's public holiday — each made into a notice once (by title)."""
    cfg = settings_for(tenant_id)
    made: List[str] = []
    team = None
    if cfg["birthdays"]:
        team = members(tenant_id)
        for m in team:
            if not _birthday_today(m, today) or m.get("notices_opt_out") or not m.get("whatsapp"):
                continue
            name = (m.get("name") or "").strip() or "a colleague"
            title = f"Birthday: {name} {today.isoformat()}"
            if _exists(tenant_id, title):
                continue
            wish = create(tenant_id, title, "happy birthday! 🎉 Wishing you a wonderful day.",
                          kind="birthday", audience_spec={"ids": [m["id"]]}, created_by="vula")
            await send(tenant_id, wish["id"])
            made.append(title)
            if m.get("birthday_share", True):
                others = [o["id"] for o in team if o["id"] != m["id"]]
                if others:
                    note = create(tenant_id, f"Team: {title}",
                                  f"it's {name.split()[0]}'s birthday today 🎂 Send them a message!",
                                  kind="birthday", audience_spec={"ids": others}, created_by="vula")
                    await send(tenant_id, note["id"])
    if cfg["holidays"]:
        from vula.sa_holidays import holiday_name
        tomorrow = today + timedelta(days=1)
        name = holiday_name(tomorrow)
        if name and tomorrow.weekday() < 5:
            title = f"Public holiday {tomorrow.isoformat()}"
            if not _exists(tenant_id, title):
                line = (f"a reminder that tomorrow ({tomorrow.strftime('%a')} {tomorrow.day} {tomorrow.strftime('%b')}) is "
                        f"{name.replace(' (observed)', '')}, a public holiday.")
                if cfg["holiday_note"]:
                    line += " " + cfg["holiday_note"].rstrip(".") + "."
                h = create(tenant_id, title, line, kind="holiday", created_by="vula")
                await send(tenant_id, h["id"])
                made.append(title)
    return made


# ── a day later: one reminder, and the owner hears who hasn't read it ─────────

async def remind_and_summarise(tenant_id: str, notice: Dict[str, Any]) -> Dict[str, Any]:
    from vula.api import whatsapp as wa
    db = _client()
    claimed = (db.table("vula_staff_notices").update({"reminded_at": _now().isoformat()})
               .eq("id", notice["id"]).is_("reminded_at", "null").execute().data or [])
    if not claimed:
        return {"skipped": True}
    rows = recipients(tenant_id, notice["id"])
    business = _business(tenant_id)
    team = {str(m["id"]): m for m in members(tenant_id)}
    reminded = 0
    for r in rows:
        if not r.get("sent_at") or r.get("acknowledged_at"):
            continue
        m = team.get(str(r["member_id"]))
        if not m:
            continue
        reminder = {**notice, "title": f"Reminder: {notice['title']}"}
        _, err = await _deliver(tenant_id, {**reminder, "id": f"{notice['id']}:r"}, m, business)
        if not err:
            reminded += 1
            db.table("vula_staff_notice_recipients").update({"reminded_at": _now().isoformat()}) \
                .eq("notice_id", notice["id"]).eq("member_id", r["member_id"]).execute()
    t = tally(rows)
    summary = (f"📢 *{notice['title']}*: {t['acknowledged']} of {t['sent']} have confirmed they've "
               "read it.")
    if t["not_acknowledged"]:
        summary += " Not yet: " + ", ".join(t["not_acknowledged"][:15]) + \
                   (f" and {len(t['not_acknowledged']) - 15} more" if len(t["not_acknowledged"]) > 15 else "") + \
                   ". I've sent them one reminder."
    for owner in [m for m in team.values() if (m.get("role") or "") == "owner" and m.get("whatsapp")]:
        await wa._send_reply(owner["whatsapp"], summary, tenant_id,
                             idem_key=f"notice_summary:{notice['id']}:{owner['id']}")
    return {"reminded": reminded, **t}


# ── the scheduler's one entry point (server.py _staff_notices_loop) ───────────

async def run_due(now: Optional[datetime] = None) -> Dict[str, int]:
    now = now or _now()
    out = {"sent": 0, "daily": 0, "reminded": 0}
    db = _client()
    try:
        due = (db.table("vula_staff_notices").select("id,tenant_id").eq("status", "scheduled")
               .lte("send_at", now.isoformat()).limit(50).execute().data or [])
    except Exception as exc:
        logger.debug("staff notices skipped (run migration 218?): %s", exc)
        return out
    for n in due:
        try:
            res = await send(n["tenant_id"], n["id"])
            out["sent"] += int(res.get("sent") or 0)
        except Exception as exc:
            logger.warning("staff notice %s failed: %s", n["id"], exc)
    local = now.astimezone(SAST)
    try:
        cfgs = (db.table("vula_tenant_config").select("tenant_id,staff_notices")
                .not_.is_("staff_notices", "null").execute().data or [])
    except Exception:
        cfgs = []
    for c in cfgs:
        cfg = {**DEFAULTS, **(c.get("staff_notices") or {})}
        if not (cfg.get("birthdays") or cfg.get("holidays")) or local.hour != int(cfg["send_hour"]):
            continue
        try:
            out["daily"] += len(await daily_pass(c["tenant_id"], local.date()))
        except Exception as exc:
            logger.warning("staff notices daily pass for %s failed: %s", c["tenant_id"], exc)
    try:
        cutoff = (now - REMIND_AFTER).isoformat()
        stale = (db.table("vula_staff_notices").select("*").eq("status", "sent")
                 .eq("must_read", True).is_("reminded_at", "null").lte("sent_at", cutoff)
                 .limit(20).execute().data or [])
    except Exception:
        stale = []
    for n in stale:
        try:
            res = await remind_and_summarise(n["tenant_id"], n)
            out["reminded"] += int(res.get("reminded") or 0)
        except Exception as exc:
            logger.warning("staff notice reminder %s failed: %s", n["id"], exc)
    return out
