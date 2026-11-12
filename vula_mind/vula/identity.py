"""One answer to "who is this?" for a business's own people, on WhatsApp and in the dashboard.

A staff member reaches Vula two ways: from their WhatsApp number, and from a dashboard login.
Until now each path worked the person out for itself (phone → vula_team_members on WhatsApp;
nothing at all in the dashboard chat, which ran anonymous and with every tool). Anything that is
personal to a member — their style, their private notes, their own thread with Vula — has to
follow the PERSON across both, so both paths resolve to the same `Person`, keyed on the team row
(`vula_team_members.id`), which survives a change of phone number or email. An owner login with
no team row is keyed on the Supabase user id instead.

Also here: the rule for which conversation threads are a staff member's own thread with Vula
(`admin:{phone}` on WhatsApp, `webadmin:{…}` in the dashboard). Those are private to that member:
not listed in the shared inbox, not readable by colleagues, the owner, or the platform operator.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set

logger = logging.getLogger(__name__)

STAFF_SESSION_PREFIXES = ("admin:", "webadmin:")
_FULL_ROLES = {"owner", "manager", "admin"}


@dataclass(frozen=True)
class Person:
    tenant_id: str
    member_id: Optional[str] = None      # vula_team_members.id
    user_id: Optional[str] = None        # Supabase auth user (dashboard login)
    name: str = ""
    role: str = ""
    access: Optional[List[str]] = None   # None = full access; a list = only these sections
    phone: str = ""                      # the member's WhatsApp number, digits
    email: str = ""
    prefs: Dict[str, Any] = field(default_factory=dict)

    @property
    def key(self) -> str:
        """Stable id for anything stored per person. "" when the person can't be pinned down."""
        if self.member_id:
            return f"m:{self.member_id}"
        return f"u:{self.user_id}" if self.user_id else ""

    @property
    def web_session_key(self) -> str:
        """This person's own dashboard thread with Vula."""
        return f"webadmin:{self.key}" if self.key else ""


def _digits(phone: Any) -> str:
    from vula.team_index import digits_za
    return digits_za(str(phone or ""))


def _access_of(row: Dict[str, Any]) -> Optional[List[str]]:
    if (row.get("role") or "").lower() in _FULL_ROLES:
        return None
    return list(row.get("access") or []) or None


def _from_member(tenant_id: str, row: Dict[str, Any], user_id: Optional[str] = None,
                 access: Any = "unset") -> Person:
    return Person(
        tenant_id=tenant_id, member_id=str(row["id"]) if row.get("id") else None, user_id=user_id,
        name=(row.get("name") or "").strip(), role=(row.get("role") or "").strip(),
        access=_access_of(row) if access == "unset" else access,
        phone=_digits(row.get("whatsapp")), email=(row.get("email") or "").strip().lower(),
        prefs=dict(row.get("prefs") or {}) if isinstance(row.get("prefs"), dict) else {},
    )


def person_for_phone(tenant_id: str, phone: Optional[str]) -> Optional[Person]:
    """The team member writing from this WhatsApp number, or None (a customer, an unknown
    sender, or a failed lookup — callers treat all three as "not one of ours")."""
    if not tenant_id or not phone:
        return None
    try:
        from vula import team_index
        row = team_index.member_for_phone(tenant_id, phone)
    except Exception as exc:  # noqa: BLE001 — never block a reply on this
        logger.debug("person_for_phone lookup skipped: %s", exc)
        return None
    return _from_member(tenant_id, row) if row else None


async def person_for_login(authorization: str, tenant_id: str) -> Optional[Person]:
    """The signed-in dashboard user as a Person, or None when there is no valid login for this
    business (no bearer token, an API-key caller, not a member)."""
    token = (authorization or "").removeprefix("Bearer ").strip()
    if not token or not tenant_id:
        return None
    try:
        from vula.api.master_auth import _verify_jwt
        from vula.api.tenant_auth import member_scope
        scope = await member_scope(authorization, tenant_id)
        if not scope:
            return None
        user = await _verify_jwt(token) or {}
    except Exception as exc:  # noqa: BLE001
        logger.debug("person_for_login lookup skipped: %s", exc)
        return None
    user_id = user.get("id")
    # full → None (everything); otherwise exactly the granted sections. An inactive member has
    # full=False and no sections: an empty list, which callers must treat as "nothing".
    access = None if scope.get("full") else list(scope.get("access") or [])
    member = scope.get("member")
    if member:
        return _from_member(tenant_id, member, user_id=user_id, access=access)
    if not user_id:
        return None
    return Person(tenant_id=tenant_id, user_id=str(user_id), role=scope.get("role") or "",
                  access=access, email=(scope.get("email") or "").strip().lower())


# ── a staff member's own thread with Vula ────────────────────────────────────────────────────

def is_staff_session_key(session_key: Optional[str]) -> bool:
    return str(session_key or "").startswith(STAFF_SESSION_PREFIXES)


def owns_session(person: Optional[Person], session_key: Optional[str]) -> bool:
    """True when this staff thread is the caller's own."""
    key = str(session_key or "")
    if not person or not is_staff_session_key(key):
        return False
    if key.startswith("webadmin:"):
        return bool(person.web_session_key) and key == person.web_session_key
    theirs = _digits(key.split(":", 1)[1].split(":", 1)[0])
    return bool(theirs) and bool(person.phone) and theirs == person.phone


_PHONE_IN_KEY = re.compile(r"\d{7,15}")


def staff_phone_digits(tenant_id: str) -> Set[str]:
    """Every WhatsApp number that belongs to this business's own people — team rows (active or
    not) and registered admin/staff phones. Used to keep their threads out of operator views.
    Raises if the team can't be read, so a caller can fail closed rather than show everything."""
    from vula.commerce import service
    db = service._client()
    out: Set[str] = set()
    rows = db.table("vula_team_members").select("whatsapp").eq("tenant_id", tenant_id).execute().data or []
    out |= {_digits(r.get("whatsapp")) for r in rows if isinstance(r, dict)}
    try:
        rows = (db.table("vula_tenant_phones").select("phone,role")
                .eq("tenant_id", tenant_id).execute().data or [])
        out |= {_digits(r.get("phone")) for r in rows
                if isinstance(r, dict) and (r.get("role") or "").lower() in ("admin", "staff")}
    except Exception as exc:  # noqa: BLE001 — the team table is the source of truth
        logger.debug("staff_phone_digits: tenant_phones skipped: %s", exc)
    out.discard("")
    return out


def is_staff_thread(thread_key: Optional[str], staff_digits: Set[str]) -> bool:
    """A vula_chat_messages thread key ("{phone}" or "{phone}:{project}") that is a staff
    member's, given staff_phone_digits()."""
    m = _PHONE_IN_KEY.search(str(thread_key or "").split(":", 1)[0])
    return bool(m) and _digits(m.group(0)) in staff_digits
