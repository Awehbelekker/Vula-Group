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
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Callable, Optional

logger = logging.getLogger(__name__)

REMIND_AFTER = timedelta(minutes=30)
RESUME_AFTER = timedelta(hours=2)
SAST = timezone(timedelta(hours=2))

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


async def sweep(tenant_id: str, *, helper_for: Callable[[dict], Optional[dict]],
                now: Optional[datetime] = None) -> dict:
    """One pass over a tenant's paused chats. Returns counts {reminded, resumed, apologised}."""
    from vula.api.whatsapp import _send_reply
    from vula.commerce import service

    now = now or datetime.now(timezone.utc)
    done = {"reminded": 0, "resumed": 0, "apologised": 0}
    sessions = await service.find_stale_paused_sessions(tenant_id, hours=REMIND_AFTER.total_seconds() / 3600)
    for s in sessions:
        last = _when(s.get("last_at"))
        if last is None:
            continue
        idle = now - last
        waiting = s.get("last_role") == "user"      # the customer wrote last
        who = s.get("customer_name") or s.get("customer_phone") or "a customer"
        helper = helper_for(s)
        to = (helper or {}).get("whatsapp")

        if idle < RESUME_AFTER:
            key = (s["id"], s.get("last_at") or "")
            if not waiting or key in _reminded:
                continue
            _reminded.add(key)
            if to:
                back_at = (last + RESUME_AFTER).astimezone(SAST).strftime("%H:%M")
                msg = (f"⏰ {who} has been waiting {int(idle.total_seconds() // 60)} min for a reply "
                       f"in the chat you took over. If nobody answers, Vula takes the chat back "
                       f"at {back_at}.")
                if await _send_reply(to, msg, tenant_id=tenant_id):
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
