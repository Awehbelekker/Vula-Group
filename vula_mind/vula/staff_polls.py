"""Polls to a business's own staff on WhatsApp, optionally anonymous (20 Oct 2026, Ian: "Even
voting? Option for anonymous?"). Migration 219.

    create     a question with 2–10 choices, anonymous or not, an optional close time, and who
               it's for — sent through the staff notices' claim-once path (kind 'poll')
    deliver    someone who messaged in the last day gets reply buttons (up to 3 short choices) or
               a tap list; anyone else gets the staff_poll_invite template, whose Vote button
               reopens the chat and brings up the choices
    vote       a tap ("poll:<id>:<i>") or a typed "2" / "Friday"; voting again changes it
    results    counts per choice and "9 of 12 voted" — and, only when the poll isn't anonymous,
               who chose what
    run_due    one reminder to people who haven't voted (halfway to the close, or after a day),
               and at the close the owner gets the result

Anonymous, honestly: who has voted (vula_staff_poll_voters) is kept apart from what was voted
(vula_staff_poll_votes). An anonymous vote row carries no member_id, only an HMAC of the poll and
person (so a vote can be changed), and nothing in Vula's API or dashboard joins it back. Staff are
told their answer "isn't shown to anyone at <business>", not that it's untraceable.
"""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import logging
import re
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from vula import staff_notices as sn

logger = logging.getLogger(__name__)

MAX_OPTIONS = 10
OPTION_LEN = 24          # WhatsApp list row title limit
BUTTON_LEN = 20          # reply button title limit
REMIND_AFTER = timedelta(hours=24)


def _client():
    return sn._client()


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _voter_key(poll_id: str, member_id: str) -> str:
    from config import settings
    seed = (settings.supabase_service_role_key or settings.supabase_service_key or "vula-polls")
    key = hashlib.sha256(("staff-poll:" + seed).encode()).digest()
    return hmac.new(key, f"{poll_id}|{member_id}".encode(), hashlib.sha256).hexdigest()


def _clean_options(options: List[Any]) -> List[str]:
    out: List[str] = []
    for o in options or []:
        t = re.sub(r"\s+", " ", str(o or "")).strip()[:OPTION_LEN]
        if t and t.lower() not in {x.lower() for x in out}:
            out.append(t)
    return out[:MAX_OPTIONS]


# ── creating ──────────────────────────────────────────────────────────────────

def create(tenant_id: str, question: str, options: List[Any], *, anonymous: bool = False,
           closes_at: Optional[str] = None, audience_spec: Optional[Dict[str, Any]] = None,
           send_at: Optional[str] = None, created_by: Optional[str] = None) -> Dict[str, Any]:
    question = re.sub(r"\s+", " ", question or "").strip()[:300]
    opts = _clean_options(options)
    if not question:
        raise ValueError("A poll needs a question.")
    if len(opts) < 2:
        raise ValueError("A poll needs at least 2 different choices.")
    notice = sn.create(tenant_id, question[:120], question, kind="poll", audience_spec=audience_spec,
                       send_at=send_at, created_by=created_by)
    row = {"tenant_id": tenant_id, "notice_id": notice["id"], "question": question, "options": opts,
           "anonymous": bool(anonymous), "closes_at": closes_at, "created_by": created_by}
    data = _client().table("vula_staff_polls").insert(row).execute().data or []
    if not data:
        raise RuntimeError("The poll wasn't saved.")
    return {**data[0], "notice": notice}


def get(tenant_id: str, poll_id: str) -> Optional[Dict[str, Any]]:
    rows = (_client().table("vula_staff_polls").select("*").eq("tenant_id", tenant_id)
            .eq("id", poll_id).limit(1).execute().data or [])
    return rows[0] if rows else None


def for_notice(tenant_id: str, notice_id: str) -> Optional[Dict[str, Any]]:
    rows = (_client().table("vula_staff_polls").select("*").eq("tenant_id", tenant_id)
            .eq("notice_id", notice_id).limit(1).execute().data or [])
    return rows[0] if rows else None


# ── delivering (called from staff_notices._deliver for kind 'poll') ───────────

def _choices_text(poll: Dict[str, Any], business: str) -> str:
    lines = [f"🗳️ *{poll['question']}*", ""]
    lines += [f"{i + 1}. {o}" for i, o in enumerate(poll["options"])]
    lines += ["", "Tap your choice, or reply with its number."]
    if poll.get("anonymous"):
        lines.append(f"Your answer isn't shown to anyone at {business}.")
    return "\n".join(lines)


async def send_choices(tenant_id: str, phone: str, poll: Dict[str, Any], business: str) -> bool:
    """The buttons or tap list — only inside the person's 24-hour window."""
    from vula.api import whatsapp as wa
    creds = await wa._get_tenant_wa_creds(tenant_id)
    body = _choices_text(poll, business)
    number = wa._wa_number(phone)
    opts = poll["options"]
    ok = False
    if creds:
        if len(opts) <= 3 and all(len(o) <= BUTTON_LEN for o in opts):
            ok = await wa._send_wa_buttons(creds, number, body, [
                {"id": f"poll:{poll['id']}:{i}", "title": o} for i, o in enumerate(opts)])
        else:
            ok = await wa._send_wa_list(creds, number, "", body, "", "Choose", [
                {"title": "Choices", "rows": [{"id": f"poll:{poll['id']}:{i}", "title": o}
                                              for i, o in enumerate(opts)]}])
    if not ok:                           # no interactive message — the numbered text still works
        ok = await wa._send_reply(phone, body, tenant_id)
    return ok


async def deliver(tenant_id: str, notice: Dict[str, Any], member: Dict[str, Any],
                  business: str) -> tuple:
    """(wamid or "", error or None) — the poll's version of staff_notices._deliver."""
    from vula import open_questions
    from vula.api import whatsapp as wa
    poll = for_notice(tenant_id, str(notice["id"]).split(":")[0])
    if not poll:
        return "", "poll not found"
    wamid = ""
    if sn._in_window(member):
        if not await send_choices(tenant_id, member["whatsapp"], poll, business):
            return "", "not delivered"
    else:
        from vula.commerce.job_config import template_name
        from vula.commerce import wa_templates as wt
        name = template_name(tenant_id, wt.STAFF_POLL_SUFFIX)
        wamid = await wa.send_template_message(
            tenant_id, member["whatsapp"], name,
            [sn._first_name(member), business, sn._one_line(poll["question"])])
        if not wamid:
            return "", f"template {name} not sent (is it approved?)"
    open_questions.ask(tenant_id, member["whatsapp"], "staff_poll", poll["id"], poll["question"][:120])
    return wamid, None


# ── voting ────────────────────────────────────────────────────────────────────

def _member(tenant_id: str, phone: str) -> Optional[Dict[str, Any]]:
    me = sn._digits(phone)[-9:]
    return next((m for m in sn.members(tenant_id) if sn._digits(m.get("whatsapp") or "")[-9:] == me), None)


def _closed(poll: Dict[str, Any]) -> bool:
    if poll.get("status") == "closed":
        return True
    c = poll.get("closes_at")
    if not c:
        return False
    try:
        return datetime.fromisoformat(str(c).replace("Z", "+00:00")) <= _now()
    except Exception:
        return False


def record_vote(tenant_id: str, poll: Dict[str, Any], member: Dict[str, Any], index: int) -> str:
    db = _client()
    key = _voter_key(poll["id"], member["id"])
    row = {"poll_id": poll["id"], "tenant_id": tenant_id, "voter_key": key, "option_index": index,
           "member_id": None if poll.get("anonymous") else member["id"],
           "updated_at": _now().isoformat()}
    have = (db.table("vula_staff_poll_votes").select("id").eq("poll_id", poll["id"])
            .eq("voter_key", key).limit(1).execute().data or [])
    if have:
        db.table("vula_staff_poll_votes").update({"option_index": index, "updated_at": row["updated_at"]}) \
            .eq("id", have[0]["id"]).execute()
    else:
        db.table("vula_staff_poll_votes").insert(row).execute()
        try:
            db.table("vula_staff_poll_voters").insert({
                "poll_id": poll["id"], "tenant_id": tenant_id, "member_id": member["id"]}).execute()
        except Exception:
            pass                                  # already counted as having voted
    reply = f"🗳️ Got your vote: *{poll['options'][index]}*. Reply with another number to change it."
    if poll.get("anonymous"):
        reply += f" Your answer isn't shown to anyone at {sn._business(tenant_id)}."
    return reply


async def vote_tap(tenant_id: str, phone: str, reply_id: str) -> Optional[str]:
    m = re.fullmatch(r"poll:([0-9a-f-]{36}):(\d)", reply_id or "")
    if not m:
        return None
    poll, member = get(tenant_id, m.group(1)), _member(tenant_id, phone)
    if not poll or not member:
        return None
    if _closed(poll):
        return "That poll has closed — thanks anyway!"
    i = int(m.group(2))
    if i >= len(poll["options"]):
        return None
    return record_vote(tenant_id, poll, member, i)


async def vote_text(tenant_id: str, phone: str, poll_id: str, text: str) -> Optional[str]:
    """A typed answer ("2", "Friday"), or the invite's Vote button. None when it isn't one."""
    poll, member = get(tenant_id, poll_id), _member(tenant_id, phone)
    if not poll or not member:
        return None
    t = (text or "").strip()
    if _closed(poll):
        return "That poll has closed — thanks anyway!" if re.fullmatch(r"\d{1,2}|vote", t, re.I) else None
    if t.lower().strip("!. ") in ("vote", "vote now"):
        await send_choices(tenant_id, phone, poll, sn._business(tenant_id))
        return "👆 Tap your choice above."
    if re.fullmatch(r"\d{1,2}", t):
        i = int(t) - 1
        return record_vote(tenant_id, poll, member, i) if 0 <= i < len(poll["options"]) else \
            f"Please reply with a number from 1 to {len(poll['options'])}."
    low = t.lower()
    hits = [i for i, o in enumerate(poll["options"]) if low == o.lower()]
    if not hits and len(low) >= 3:
        hits = [i for i, o in enumerate(poll["options"]) if low in o.lower()]
    return record_vote(tenant_id, poll, member, hits[0]) if len(hits) == 1 else None


# ── results ───────────────────────────────────────────────────────────────────

def results(tenant_id: str, poll_id: str) -> Dict[str, Any]:
    poll = get(tenant_id, poll_id)
    if not poll:
        return {"error": "Poll not found."}
    db = _client()
    votes = (db.table("vula_staff_poll_votes").select("option_index,member_id")
             .eq("poll_id", poll_id).execute().data or [])
    voters = (db.table("vula_staff_poll_voters").select("member_id")
              .eq("poll_id", poll_id).execute().data or [])
    reached = [r for r in sn.recipients(tenant_id, poll["notice_id"]) if r.get("sent_at")]
    counts = [0] * len(poll["options"])
    for v in votes:
        if 0 <= int(v["option_index"]) < len(counts):
            counts[int(v["option_index"])] += 1
    voted_ids = {str(v["member_id"]) for v in voters}
    out = {"poll": {k: poll.get(k) for k in ("id", "question", "options", "anonymous", "closes_at",
                                             "status", "notice_id")},
           "counts": [{"option": o, "votes": counts[i]} for i, o in enumerate(poll["options"])],
           "voted": len(voted_ids), "reached": len(reached),
           "not_voted": [r.get("name") or r.get("phone") for r in reached
                         if str(r.get("member_id")) not in voted_ids]}
    if not poll.get("anonymous"):
        names = {str(r.get("member_id")): r.get("name") or r.get("phone") for r in reached}
        out["by_person"] = [{"name": names.get(str(v["member_id"]), "?"),
                             "option": poll["options"][int(v["option_index"])]}
                            for v in votes if v.get("member_id")]
    return out


def summary_text(res: Dict[str, Any]) -> str:
    p = res["poll"]
    parts = " · ".join(f"{c['option']} {c['votes']}" for c in res["counts"])
    text = f"🗳️ *{p['question']}*: {parts}. {res['voted']} of {res['reached']} voted."
    if p.get("anonymous"):
        text += " (Anonymous — who chose what isn't recorded.)"
    return text


def close(tenant_id: str, poll_id: str) -> bool:
    data = (_client().table("vula_staff_polls").update({"status": "closed"})
            .eq("tenant_id", tenant_id).eq("id", poll_id).eq("status", "open").execute().data or [])
    if data:
        from vula import open_questions
        open_questions.close_for(tenant_id, poll_id, status="expired")
    return bool(data)


# ── the scheduler (from staff_notices.run_due) ────────────────────────────────

async def run_due(now: Optional[datetime] = None) -> Dict[str, int]:
    from vula.api import whatsapp as wa
    now = now or _now()
    out = {"poll_reminded": 0, "poll_closed": 0}
    try:
        polls = (_client().table("vula_staff_polls").select("*").eq("status", "open")
                 .limit(50).execute().data or [])
    except Exception as exc:
        logger.debug("staff polls skipped (run migration 219?): %s", exc)
        return out
    for poll in polls:
        tid = poll["tenant_id"]
        notice = sn.get(tid, poll["notice_id"]) if poll.get("notice_id") else None
        if not notice or notice.get("status") != "sent" or not notice.get("sent_at"):
            continue
        sent = datetime.fromisoformat(str(notice["sent_at"]).replace("Z", "+00:00"))
        closes = (datetime.fromisoformat(str(poll["closes_at"]).replace("Z", "+00:00"))
                  if poll.get("closes_at") else None)
        try:
            if closes and closes <= now:
                if close(tid, poll["id"]):
                    out["poll_closed"] += 1
                    res = results(tid, poll["id"])
                    for owner in [m for m in sn.members(tid) if (m.get("role") or "") == "owner"
                                  and m.get("whatsapp")]:
                        await wa._send_reply(owner["whatsapp"], summary_text(res), tid,
                                             idem_key=f"poll_summary:{poll['id']}:{owner['id']}")
                continue
            due = sent + ((closes - sent) / 2 if closes else REMIND_AFTER)
            if poll.get("reminded_at") or due > now:
                continue
            claimed = (_client().table("vula_staff_polls").update({"reminded_at": now.isoformat()})
                       .eq("id", poll["id"]).is_("reminded_at", "null").execute().data or [])
            if not claimed:
                continue
            business = sn._business(tid)
            team = {str(m["id"]): m for m in sn.members(tid)}
            voted = set()
            for v in (_client().table("vula_staff_poll_voters").select("member_id")
                      .eq("poll_id", poll["id"]).execute().data or []):
                voted.add(str(v["member_id"]))
            for r in sn.recipients(tid, poll["notice_id"]):
                m = team.get(str(r.get("member_id")))
                if not m or not r.get("sent_at") or str(m["id"]) in voted:
                    continue
                _, err = await deliver(tid, {**notice, "id": f"{notice['id']}:r"}, m, business)
                out["poll_reminded"] += 0 if err else 1
                await asyncio.sleep(sn._PACE_S)
        except Exception as exc:
            logger.warning("staff poll %s: %s", poll.get("id"), exc)
    return out
