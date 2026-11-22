"""Paused WhatsApp chats nobody is answering (gap audit 1.4).

When an owner or staff member takes over a chat, the bot is paused. Two steps keep the customer
from being left waiting (run by server.py::_stale_handoff_scheduler_loop):

- after 30 minutes, if the customer's message is the last one, the person handling the chat
  is reminded once, with the time Vula will take back over;
- after 2 hours of silence, that person is told and the bot resumes. If the customer was the
  one waiting, they get a short apology too. Before 27 Oct the bot just resumed and said
  nothing, so the customer never heard back unless they wrote again.

The resume never depends on any message being delivered: a send outside WhatsApp's 24-hour
window fails, and the customer must still be answered next time they write.

22 Nov 2026 (conversation intelligence, step 1 — docs/plans/conversation-intelligence.md):
- each business sets both times in the Scheduling tab (job_config "stale_handoff_resume",
  settings remind_after_minutes / resume_after_minutes, migration 294; 30 and 120 by default);
- the reminder quotes what the customer asked and carries three buttons — Send holding reply,
  Give back to Vula, Pass to someone (a tap list of the team). Only an active team member of that
  business can use them, each press is recorded in vula_merchant_audit, and a press on a chat
  that is no longer paused (or no longer waiting) says so instead of acting twice.
"""
from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Dict, Optional

logger = logging.getLogger(__name__)

REMIND_AFTER = timedelta(minutes=30)
RESUME_AFTER = timedelta(hours=2)
SAST = timezone(timedelta(hours=2))

HOLDING_REPLY = ("Thanks for waiting. Someone from the team has your message and will reply "
                 "here shortly.")

CUSTOMER_APOLOGY = ("Sorry for the wait. The team couldn't get back to you yet, so I'm here to "
                    "help again. Send your question and I'll answer it, or ask for a person and "
                    "I'll pass it on.")

# (session id, last_at) already reminded in this process. A restart can repeat one reminder;
# there is no column for it and one extra nudge is cheaper than a migration.
_reminded: set[tuple[str, str]] = set()


def _when(ts: Optional[str]) -> Optional[datetime]:
    try:
        d = datetime.fromisoformat((ts or "").replace("Z", "+00:00"))
        return d if d.tzinfo else d.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def _quote(text: Optional[str], limit: int = 140) -> str:
    t = re.sub(r"\s+", " ", (text or "")).strip()
    return (t[:limit - 1] + "…") if len(t) > limit else t


def reminder_text(who: str, idle_min: int, back_at: str, last_message: Optional[str]) -> str:
    """The reminder to the person handling the chat (fixed wording; tests snapshot it)."""
    q = _quote(last_message)
    return (f"⏰ {who} has waited {idle_min} min for a reply in the chat you took over."
            + (f"\n\"{q}\"" if q else "")
            + f"\n\nIf nobody replies, Vula answers them again at {back_at}.")


def reminder_buttons(session_id: str) -> list:
    return [{"id": f"handoff:hold:{session_id}", "title": "Send holding reply"},
            {"id": f"handoff:back:{session_id}", "title": "Give back to Vula"},
            {"id": f"handoff:pass:{session_id}", "title": "Pass to someone"}]


async def _remind(tenant_id: str, to: str, text: str, session_id: str) -> bool:
    """The reminder with its buttons; plain text when buttons can't be sent."""
    from vula.api import whatsapp as wa
    try:
        creds = await wa._get_tenant_wa_creds(tenant_id)
        if creds and await wa._send_wa_buttons(creds, wa._wa_number(to), text,
                                               reminder_buttons(session_id)):
            return True
    except Exception as exc:  # noqa: BLE001 — the plain text still goes
        logger.debug("handoff reminder buttons skipped: %s", exc)
    return await wa._send_reply(to, text + "\nReply to them in the inbox.", tenant_id=tenant_id)


async def sweep(tenant_id: str, *, helper_for: Callable[[dict], Optional[dict]],
                now: Optional[datetime] = None, remind_after: Optional[timedelta] = None,
                resume_after: Optional[timedelta] = None) -> dict:
    """One pass over a tenant's paused chats. Returns counts {reminded, resumed, apologised}.
    The times are the business's own (job settings), else 30 minutes and 2 hours."""
    from vula.api.whatsapp import _send_reply
    from vula.commerce import service

    now = now or datetime.now(timezone.utc)
    remind_after = remind_after or REMIND_AFTER
    resume_after = resume_after if resume_after and resume_after > remind_after else max(RESUME_AFTER, remind_after * 2)
    done = {"reminded": 0, "resumed": 0, "apologised": 0}
    sessions = await service.find_stale_paused_sessions(tenant_id, hours=remind_after.total_seconds() / 3600)
    for s in sessions:
        last = _when(s.get("last_at"))
        if last is None:
            continue
        idle = now - last
        waiting = s.get("last_role") == "user"      # the customer wrote last
        who = s.get("customer_name") or s.get("customer_phone") or "a customer"
        helper = helper_for(s)
        to = (helper or {}).get("whatsapp")

        if idle < resume_after:
            key = (s["id"], s.get("last_at") or "")
            if not waiting or key in _reminded:
                continue
            _reminded.add(key)
            if to:
                back_at = (last + resume_after).astimezone(SAST).strftime("%H:%M")
                msg = reminder_text(who, int(idle.total_seconds() // 60), back_at, s.get("last_message"))
                if await _remind(tenant_id, to, msg, s["id"]):
                    done["reminded"] += 1
                else:
                    logger.warning("handoff reminder undelivered for %s session %s", tenant_id, s["id"])
            continue

        if to:
            msg = (f"⏰ Heads up — you paused Vula's replies to {who} a while ago and nothing's "
                   f"happened since. I've resumed answering them so they're not left waiting; "
                   f"jump back in any time by replying in the inbox.")
            if not await _send_reply(to, msg, tenant_id=tenant_id):
                logger.warning("stale-handoff ping undelivered for %s session %s "
                               "(helper outside their 24h WhatsApp window?)", tenant_id, s["id"])
        else:
            logger.warning("stale-handoff: no helper configured to notify for %s session %s",
                           tenant_id, s["id"])
        await service.set_session_paused(tenant_id, s["id"], False)
        done["resumed"] += 1
        logger.info("auto-resumed stale-paused session %s for %s (idle since %s)",
                    s["id"], tenant_id, s.get("last_at"))
        if waiting and s.get("customer_phone"):
            if await _send_reply(s["customer_phone"], CUSTOMER_APOLOGY, tenant_id=tenant_id):
                done["apologised"] += 1
                try:
                    await service.append_message(tenant_id, s["id"], "assistant", CUSTOMER_APOLOGY)
                except Exception as exc:  # noqa: BLE001
                    logger.debug("apology not added to the thread: %s", exc)
            else:
                logger.info("handoff apology undelivered for %s session %s (outside 24h?)",
                            tenant_id, s["id"])
        try:
            from core.mass_mind import health as _mm_health
            _mm_health.record_event(tenant_id, "stale_handoff_auto_resumed", {"session_id": s["id"]})
        except Exception:  # noqa: BLE001
            pass
    return done


# ── the reminder's buttons (whatsapp.py routes "handoff:…" replies here) ─────────────────────────

_ID = re.compile(r"^[0-9a-fA-F-]{8,64}$")


def _session(tenant_id: str, session_id: str) -> Optional[Dict[str, Any]]:
    from vula.commerce import service
    rows = (service._client().table("commerce_conversation_sessions").select("*")
            .eq("tenant_id", tenant_id).eq("id", session_id).limit(1).execute().data or [])
    return rows[0] if rows else None


def _audit(tenant_id: str, member: Dict[str, Any], action: str, **detail: Any) -> None:
    from vula.api import merchant_audit
    merchant_audit.audit(tenant_id, {"email": member.get("email"), "user_id": member.get("id")},
                         f"handoff.{action}", **detail)


async def handle_button(tenant_id: str, phone: str, reply_id: str) -> Optional[str]:
    """A tap on a reminder's button (or the Pass to someone list). Returns what to tell the person
    who tapped, or None when a list was sent instead."""
    from vula import team_index
    from vula.api import whatsapp as wa
    from vula.commerce import service

    parts = (reply_id or "").split(":")
    if len(parts) < 3 or parts[0] != "handoff" or not _ID.match(parts[2]):
        return None
    action, session_id = parts[1], parts[2]
    try:
        member = team_index.member_for_phone(tenant_id, phone)
    except Exception as exc:  # noqa: BLE001
        logger.warning("handoff button: team lookup failed for %s: %s", tenant_id, exc)
        member = None
    if not member:
        return "Only someone on the team can do that."
    s = _session(tenant_id, session_id)
    if not s:
        return "That chat isn't there any more."
    who = s.get("customer_name") or s.get("customer_phone") or "the customer"
    if not s.get("paused"):
        return f"Vula is already answering {who}, so there's nothing to do."
    waiting = s.get("last_role") == "user"

    if action == "hold":
        if not waiting:
            return f"{who} isn't waiting: the last message in that chat isn't theirs."
        if not s.get("customer_phone"):
            return f"There's no WhatsApp number for {who}. Reply from the inbox."
        if not await wa._send_reply(s["customer_phone"], HOLDING_REPLY, tenant_id=tenant_id):
            return (f"That didn't go: {who} last wrote more than 24 hours ago, so WhatsApp only "
                    f"allows an approved template. Reply from the inbox.")
        await service.append_message(tenant_id, session_id, "assistant", HOLDING_REPLY)
        _audit(tenant_id, member, "holding_reply", session_id=session_id)
        return f"Sent {who} a holding reply. The chat stays with you."

    if action == "back":
        await service.set_session_paused(tenant_id, session_id, False)
        _audit(tenant_id, member, "give_back", session_id=session_id)
        if waiting and s.get("customer_phone"):
            if await wa._send_reply(s["customer_phone"], CUSTOMER_APOLOGY, tenant_id=tenant_id):
                await service.append_message(tenant_id, session_id, "assistant", CUSTOMER_APOLOGY)
        return f"Vula is answering {who} again."

    if action == "pass":
        others = [m for m in team_index.active_members(tenant_id)
                  if m.get("whatsapp") and m.get("id") != member.get("id")][:10]
        if not others:
            return "There's nobody else on the team with a WhatsApp number to pass it to."
        creds = await wa._get_tenant_wa_creds(tenant_id)
        rows = [{"id": f"handoff:to:{session_id}:{m['id']}", "title": (m.get("name") or m["whatsapp"])[:24]}
                for m in others]
        if creds and await wa._send_wa_list(creds, wa._wa_number(phone), "", f"Pass the chat with {who} to:",
                                            "", "Choose", [{"title": "Team", "rows": rows}]):
            return None
        return "Couldn't show the team list just now. Assign the chat in the inbox instead."

    if action == "to" and len(parts) == 4 and _ID.match(parts[3]):
        target = next((m for m in team_index.active_members(tenant_id) if str(m.get("id")) == parts[3]), None)
        if not target or not target.get("whatsapp"):
            return "That person isn't on the team any more."
        name = target.get("name") or target["whatsapp"]
        (service._client().table("commerce_conversation_sessions")
         .update({"assigned_to": name, "updated_at": datetime.now(timezone.utc).isoformat()})
         .eq("tenant_id", tenant_id).eq("id", session_id).execute())
        _audit(tenant_id, member, "pass", session_id=session_id, to_member=target.get("id"))
        q = _quote(s.get("last_message"))
        await wa._send_reply(target["whatsapp"],
                             f"👋 {member.get('name') or 'A colleague'} passed you the chat with {who}."
                             + (f"\n\"{q}\"" if q and waiting else "")
                             + "\nReply to them in the inbox.", tenant_id=tenant_id)
        return f"Passed to {name}."
    return None
