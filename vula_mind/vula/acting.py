"""
vula/acting.py — who is doing this, for "who made it" on quotes, invoices and orders.

12 Nov 2026: so sales can be counted per person (vula/team_activity.py), every quote, invoice
and order records the team member who made it (created_by, migration 260). Rather than threading
a parameter through every caller, the request says once who is acting:

  WhatsApp   commerce_admin._dispatch_tool sets the sender's number for the tool call
  dashboard  the create endpoints set the signed-in member's WhatsApp number (or their email)

and the create functions in vula/commerce/service.py read it. A customer's own checkout has no
actor, so it stays empty — that's the business, not a person.
"""
from __future__ import annotations

import re
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Iterator, Optional

_ACTOR: ContextVar[Optional[str]] = ContextVar("vula_actor", default=None)


def _clean(who: Optional[str]) -> Optional[str]:
    who = (who or "").strip()
    if not who:
        return None
    if "@" in who:
        return who.lower()[:200]
    d = re.sub(r"\D", "", who)
    return d or None


def current() -> Optional[str]:
    return _ACTOR.get()


@contextmanager
def acting_as(who: Optional[str]) -> Iterator[None]:
    token = _ACTOR.set(_clean(who))
    try:
        yield
    finally:
        _ACTOR.reset(token)


async def from_dashboard(authorization: str, tenant_id: str) -> Optional[str]:
    """The signed-in member's WhatsApp number, else their login email; None if unknown."""
    try:
        from vula.api.tenant_auth import member_scope
        scope = await member_scope(authorization or "", tenant_id) or {}
    except Exception:
        return None
    member = scope.get("member") or {}
    return _clean(member.get("whatsapp")) or _clean(scope.get("email"))
