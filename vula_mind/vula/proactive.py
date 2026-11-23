"""
vula/proactive.py — a message someone asked for, delivered even outside WhatsApp's 24-hour window.

12 Nov 2026. Meta only lets a business send free text to someone who messaged it in the last 24
hours. A morning brief at 07:00, a reminder set last week, a meeting reminder: often outside it.
Before, those failed and at most a generic "you have a new update" template went out. Now:

  1. send the full text as usual (inside the window it simply arrives);
  2. if WhatsApp refused it because the window is closed, send this message's own approved
     template (wa_templates.PROACTIVE_TEMPLATES — named <business>_<suffix>) with the key facts
     in its variables, e.g. "⏰ Meeting today at 10:00 with Acme Builders. Where: 12 Main Rd.";
  3. if that template isn't approved yet (or fails), the generic notification template as before.

Replying to the template opens the window, so the full text can follow ("Reply *brief*").
Only ever used for messages the person asked for — the rule that nothing goes out unasked
is decided by the callers, not here.
"""
from __future__ import annotations

import logging
import re
from typing import List, Optional

log = logging.getLogger(__name__)


def one_line(value: object, limit: int = 180) -> str:
    """A template variable: one line, no tabs or runs of spaces, not empty (Meta refuses those)."""
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    text = text[:limit].rstrip()
    return text or "—"


async def send(tenant_id: str, phone: str, text: str, *, suffix: str, params: List[object],
               idem_key: Optional[str] = None) -> Optional[str]:
    """'text' when the full message went, 'template' when its template did, 'notify' for the
    generic nudge, None when nothing could be sent."""
    from vula.api import whatsapp as wa
    from vula.commerce.job_config import template_name
    fb, wc = wa.NOTIFY_FALLBACK.set(False), wa.WINDOW_CLOSED.set(False)
    try:
        if await wa._send_reply(phone, text, tenant_id, idem_key=idem_key):
            return "text"
        closed = wa.WINDOW_CLOSED.get()
    finally:
        wa.NOTIFY_FALLBACK.reset(fb)
        wa.WINDOW_CLOSED.reset(wc)
    if not closed:
        return None                              # a different failure: the caller retries as before
    if idem_key:
        import asyncio
        if not await asyncio.to_thread(wa._claim_outbound, tenant_id, f"{idem_key}:template"):
            return "template"                    # already sent by another worker
    name = template_name(tenant_id, suffix)
    if await wa.send_template(tenant_id, phone, name, [one_line(p) for p in params]):
        return "template"
    log.info("template %s not usable for %s — generic notification instead", name, tenant_id)
    creds = await wa._send_creds(tenant_id, to=phone)
    if creds and await wa._send_notify_template(creds, wa._wa_number(phone), tenant_id):
        return "notify"
    return None
