"""Daily check of real conversations, per tenant (2026-09-30).

Ian: "we can't have this happening on a continuous basis". Until now problems surfaced when a
client like Judy hit them. Every morning this reads yesterday's replies for every tenant and
lists the ones that look wrong, deterministically (no model, no cost), so they're fixed before
the next client sees the same thing:

  repeat        the same reply sent twice in a row in one conversation
  leak          raw JSON / tool text, or the leak / unbacked-action substitution fired
  cant_answer   "I don't know / couldn't find / not able to"
  promise       a promise that someone will follow up (checked against escalations raised)
  caveat        the adversarial checker flagged the answer
  pushback      the person said the answer was wrong ("that's not what I asked", "wrong")

The report goes to TEAM_EMAIL once a day (07:00–10:59 SAST, marker in vula_admin_audit) and
is available in Master › Conversations. Excerpts are short and only go to the Vula team.
"""
from __future__ import annotations

import html
import logging
import re
from datetime import date, datetime, time, timedelta, timezone
from typing import Any, Dict, List, Optional

log = logging.getLogger(__name__)

SAST = timezone(timedelta(hours=2))
_ACTION = "conversation_check_sent"
_EXCERPT = 160

KINDS = {
    "repeat": "Same reply sent twice in a row",
    "leak": "Raw tool output / claimed an action it didn't do",
    "cant_answer": "Couldn't answer",
    "promise": "Promised a follow-up",
    "caveat": "Answer flagged by the checker",
    "pushback": "Person said the answer was wrong",
}

_CANT_RE = re.compile(
    r"\b(i (?:don'?t|do not) (?:know|have (?:that|any|access))|couldn'?t find|could not find|"
    r"i'?m not (?:able|sure)|i am not able|unable to|can'?t (?:help|answer|find|access)|"
    r"couldn'?t (?:work that out|save|add|complete|finish)|"
    r"not in (?:the|our) (?:knowledge base|data sheets|records)|nothing (?:on file|in the knowledge base))",
    re.IGNORECASE)
_PUSHBACK_RE = re.compile(
    r"\b(that'?s (?:wrong|not (?:right|correct|what i (?:asked|meant)))|not what i (?:asked|meant)|"
    r"wrong (?:answer|one|amount|figure|invoice)|you'?re wrong|incorrect|doesn'?t make sense|"
    r"i said|i asked for|still not|again\?|why (?:did|are) you)\b|^\s*\?+\s*$",
    re.IGNORECASE | re.MULTILINE)
# Both wordings the checker's caveat has used.
_CAVEAT_MARKS = ("Please double-check this answer", "Worth double-checking")
# A raw retrieval dump sent as the reply (digg-demo, 29 Sep: "Relevant knowledge:\n[conv_…").
_RAW_CONTEXT_RE = re.compile(r"^\s*Relevant knowledge:|\[conv_[^\]]+\.txt\]:", re.MULTILINE)


def _client():
    from vula.commerce import service as cs
    return cs._client()


def _norm(text: str) -> str:
    return re.sub(r"\W+", " ", (text or "").lower()).strip()


def _excerpt(text: str) -> str:
    t = re.sub(r"\s+", " ", text or "").strip()
    return t if len(t) <= _EXCERPT else t[:_EXCERPT - 1] + "…"


def classify(reply: str, previous_reply: Optional[str], next_user: Optional[str]) -> List[str]:
    """Which checks one assistant reply trips. Pure — the unit the tests pin."""
    from core.skills.base import (LEAKED_CUSTOMER_FALLBACK, LEAKED_OWNER_FALLBACK,
                                  UNBACKED_ACTION_NOTE, leaked_tool_output)
    kinds: List[str] = []
    reply = (reply or "").replace("\u2019", "'")
    next_user = (next_user or "").replace("\u2019", "'") or None
    body = reply.split("⚠️")[0]
    if previous_reply and len(_norm(body)) > 20 and _norm(body) == _norm((previous_reply or "").split("⚠️")[0]):
        kinds.append("repeat")
    if (leaked_tool_output(body, []) or _RAW_CONTEXT_RE.search(body) or UNBACKED_ACTION_NOTE in reply
            or LEAKED_OWNER_FALLBACK in reply or LEAKED_CUSTOMER_FALLBACK in reply):
        kinds.append("leak")
    if _CANT_RE.search(body):
        kinds.append("cant_answer")
    try:
        from vula.escalation import _NO_ANSWER
        if _NO_ANSWER.search(body) and "cant_answer" not in kinds:
            kinds.append("promise")
    except Exception:
        pass
    if any(m in reply for m in _CAVEAT_MARKS):
        kinds.append("caveat")
    if next_user and _PUSHBACK_RE.search(next_user):
        kinds.append("pushback")
    return kinds


def _day_bounds(day: date) -> tuple[str, str]:
    start = datetime.combine(day, time(0, 0), tzinfo=SAST)
    return start.astimezone(timezone.utc).isoformat(), (start + timedelta(days=1)).astimezone(timezone.utc).isoformat()


def _messages(day: date) -> List[Dict[str, Any]]:
    lo, hi = _day_bounds(day)
    out: List[Dict[str, Any]] = []
    page = 0
    while True:
        rows = (_client().table("commerce_conversation_messages")
                .select("tenant_id,session_id,role,content,created_at")
                .gte("created_at", lo).lt("created_at", hi).order("created_at")
                .range(page * 1000, page * 1000 + 999).execute().data or [])
        out += rows
        if len(rows) < 1000 or page >= 19:
            return out
        page += 1


def _escalations(day: date) -> Dict[str, int]:
    lo, hi = _day_bounds(day)
    try:
        rows = (_client().table("vula_escalations").select("tenant_id")
                .gte("created_at", lo).lt("created_at", hi).limit(5000).execute().data or [])
    except Exception as exc:
        log.debug("conversation check: escalations skipped: %s", exc)
        return {}
    out: Dict[str, int] = {}
    for r in rows:
        out[r["tenant_id"]] = out.get(r["tenant_id"], 0) + 1
    return out


def build(day: Optional[date] = None) -> Dict[str, Any]:
    """Yesterday (SAST) by default. {day, tenants: [{tenant_id, replies, flagged, counts,
    examples[kind] -> [{question, reply, next}]}], totals}."""
    day = day or (datetime.now(SAST).date() - timedelta(days=1))
    msgs = _messages(day)
    by_session: Dict[tuple, List[Dict[str, Any]]] = {}
    for m in msgs:
        by_session.setdefault((m.get("tenant_id"), m.get("session_id")), []).append(m)
    tenants: Dict[str, Dict[str, Any]] = {}
    for (tid, _sid), rows in by_session.items():
        t = tenants.setdefault(tid, {"tenant_id": tid, "replies": 0, "flagged": 0,
                                     "counts": {k: 0 for k in KINDS}, "examples": {}})
        last_user, prev_reply = None, None
        for i, m in enumerate(rows):
            if m.get("role") == "user":
                last_user = m.get("content")
                continue
            if m.get("role") != "assistant":
                continue
            nxt = next((r.get("content") for r in rows[i + 1:] if r.get("role") == "user"), None)
            kinds = classify(m.get("content") or "", prev_reply, nxt)
            t["replies"] += 1
            if kinds:
                t["flagged"] += 1
            for k in kinds:
                t["counts"][k] += 1
                ex = t["examples"].setdefault(k, [])
                if len(ex) < 3:
                    ex.append({"question": _excerpt(last_user or ""), "reply": _excerpt(m.get("content") or ""),
                               "next": _excerpt(nxt or "") if k == "pushback" else None,
                               "at": m.get("created_at")})
            prev_reply = m.get("content")
    esc = _escalations(day)
    for tid, t in tenants.items():
        t["escalations_raised"] = esc.get(tid, 0)
        t["unfollowed_promises"] = max(0, t["counts"]["promise"] - esc.get(tid, 0))
    ordered = sorted(tenants.values(), key=lambda t: (-t["flagged"], t["tenant_id"] or ""))
    return {"day": day.isoformat(), "tenants": ordered,
            "totals": {"replies": sum(t["replies"] for t in ordered),
                       "flagged": sum(t["flagged"] for t in ordered)}}


def render_text(d: Dict[str, Any]) -> str:
    lines = [f"Vula conversation check — {d['day']}: {d['totals']['flagged']} of "
             f"{d['totals']['replies']} replies need a look."]
    for t in d["tenants"]:
        if not t["flagged"]:
            continue
        counts = ", ".join(f"{KINDS[k].lower()}: {n}" for k, n in t["counts"].items() if n)
        lines.append(f"\n{t['tenant_id']} — {t['flagged']}/{t['replies']} ({counts})")
        if t["unfollowed_promises"]:
            lines.append(f"  ⚠ {t['unfollowed_promises']} follow-up promise(s) with no escalation raised")
        for k, ex in t["examples"].items():
            for e in ex:
                lines.append(f"  [{KINDS[k]}] Q: {e['question']}\n     A: {e['reply']}"
                             + (f"\n     then: {e['next']}" if e.get("next") else ""))
    return "\n".join(lines)


def render_html(d: Dict[str, Any]) -> str:
    return ('<pre style="font-family:ui-monospace,Menlo,monospace;font-size:13px;white-space:pre-wrap;">'
            + html.escape(render_text(d)) + "</pre>")


def already_sent(day: str) -> bool:
    try:
        rows = (_client().table("vula_admin_audit").select("id").eq("action", _ACTION)
                .contains("detail", {"day": day}).limit(1).execute().data or [])
        return bool(rows)
    except Exception as exc:
        log.warning("conversation check: marker check failed (%s) — skipping", exc)
        return True


def due(now: datetime) -> bool:
    return 7 <= now.hour < 11


async def send(now: Optional[datetime] = None, force: bool = False) -> Dict[str, Any]:
    from config import settings
    now = now or datetime.now(SAST)
    day = (now.date() - timedelta(days=1)).isoformat()
    to = (settings.team_email or "").strip()
    if not to:
        return {"sent": False, "reason": "TEAM_EMAIL is not set"}
    if not force and already_sent(day):
        return {"sent": False, "reason": f"already sent for {day}"}
    d = build(now.date() - timedelta(days=1))
    if not d["totals"]["replies"]:
        return {"sent": False, "reason": "no conversations"}
    from vula.api.email import _send
    subject = f"Vula conversations {day} — {d['totals']['flagged']} to check"
    ok = await _send(to, subject, render_html(d), render_text(d))
    if ok:
        try:
            _client().table("vula_admin_audit").insert({
                "actor_email": "scheduler", "action": _ACTION,
                "detail": {"day": day, "to": to, "flagged": d["totals"]["flagged"]}}).execute()
        except Exception as exc:
            log.warning("conversation check: marker write failed: %s", exc)
    return {"sent": bool(ok), "day": day}
