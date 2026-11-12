"""What a staff member has asked Vula to remember about THEM — private to that person.

Staff use Vula to think things through as well as to run the shop, and some of that is personal:
how they like to work, what they're planning, things about themselves. Until now anything a
person asked Vula to "remember" became a company-wide rule every colleague's assistant read.

The rules here (the owner's decisions, 12 Nov 2026):

  private     A note belongs to one person (vula/identity.py::Person.key). It is shown to the
              assistant only when THAT person is the one talking, on WhatsApp or the dashboard.
              No colleague, no owner and no operator screen can read it: there is no route or
              tool that takes someone else's id, the table has RLS on with no policies, and the
              text is encrypted at rest.
  consent     Nothing is saved unless the person asks ("remember that I…"). Vula never files
              something away on its own.
  theirs      They can see everything kept about them and delete any of it, in words on
              WhatsApp or under "How Vula talks to you" in the dashboard. It is deleted with
              their data and when they leave the team.
  not facts   A private note is how one person works or what they said about themselves. It is
              never a fact about the business, and the prompt says so: "we close at 4" as
              someone's private note must not answer a customer.

Everything here is decided in code — recognising the request, saving, listing, deleting — so a
note is never "saved" by a model saying so. A save is read back before it is confirmed.

Off unless settings.personal_memory_enabled.
"""
from __future__ import annotations

import logging
import re
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

TABLE = "vula_personal_memory"
MAX_NOTES = 30
MAX_NOTE_CHARS = 300
MAX_BLOCK_CHARS = 900

# "remember that I…", "remember my…", "note that I'm…", "please remember: I…", and the
# explicit "just for me". Afrikaans: "onthou dat ek…", "onthou my…".
_FIRST_PERSON = r"(?:i\b|i'm\b|i’m\b|im\b|i've\b|i’ve\b|my\b|ek\b)"
_REMEMBER = re.compile(
    r"^\s*(?:please\s+|pls\s+|asseblief\s+)?"
    r"(?:remember|note|keep in mind|don'?t forget|onthou)\s+"
    r"(?:(?:for me|just for me|privately|only for me|net vir my)\s*[:,\-]?\s*)?"
    r"(?:that\s+|dat\s+|this\s*[:,\-]\s*)?"
    r"(?P<note>" + _FIRST_PERSON + r".{2,})$",
    re.IGNORECASE | re.DOTALL)
_PRIVATE = re.compile(
    r"^\s*(?:please\s+|pls\s+)?(?:remember|note|keep in mind|onthou)\s+"
    r"(?:for me|just for me|privately|only for me|net vir my)\s*[:,\-]?\s*(?:that\s+|dat\s+)?(?P<note>.{3,})$",
    re.IGNORECASE | re.DOTALL)
_LIST = re.compile(
    r"^\s*(?:what do you (?:remember|know) about me|what have you (?:remembered|saved|noted) about me|"
    r"(?:show|list)(?: me)? my (?:private )?notes|my (?:private )?notes|wat onthou jy (?:van|oor) my)\s*\??\s*$",
    re.IGNORECASE)
_FORGET_ONE = re.compile(
    r"^\s*(?:please\s+)?(?:forget|delete|remove|vergeet)\s+(?:my\s+)?(?:private\s+)?(?:note|nota)\s*#?\s*(?P<n>\d{1,2})\s*\.?\s*$",
    re.IGNORECASE)
_FORGET_ALL = re.compile(
    r"^\s*(?:please\s+)?(?:forget|delete|remove|vergeet)\s+(?:everything|all|alles)\s*"
    r"(?:you (?:remember|know) about me|about me|(?:of\s+)?my (?:private\s+)?notes|van my)\s*\.?\s*$",
    re.IGNORECASE)


def enabled(tenant_id: Optional[str] = None) -> bool:
    """On for everyone (PERSONAL_MEMORY_ENABLED), or for this business (vula/features.py)."""
    try:
        from vula import features
        return features.on(tenant_id, "personal_memory")
    except Exception:  # noqa: BLE001
        return False


def _client():
    from vula.commerce import service
    return service._client()


def _owner_filter(q, person):
    """Rows that belong to this person, in this business — and nobody else's."""
    q = q.eq("tenant_id", person.tenant_id)
    return q.eq("member_id", person.member_id) if person.member_id else q.eq("user_id", person.user_id)


def _usable(person) -> bool:
    return bool(person and person.tenant_id and (person.member_id or person.user_id))


def _seal(text: str) -> str:
    from vula import secret_keys
    return secret_keys.encrypt(text)


def _open(stored: Any) -> Optional[str]:
    from vula import secret_keys
    if not isinstance(stored, str) or not stored.startswith(secret_keys.PREFIX):
        return None
    return secret_keys.decrypt(stored)


def clean(text: str) -> str:
    """One line, trimmed and bounded. A note is a sentence, not a document."""
    t = re.sub(r"\s+", " ", (text or "")).strip().strip("\"'“”").strip()
    return t[:MAX_NOTE_CHARS].rstrip()


# ── the store ────────────────────────────────────────────────────────────────────────────────

def notes(person) -> List[Dict[str, Any]]:
    """This person's notes, oldest first: [{"id", "text", "created_at"}]. A note no key can
    unlock any more is left out (and logged by secret_keys)."""
    if not _usable(person):
        return []
    rows = (_owner_filter(_client().table(TABLE).select("id,content,created_at"), person)
            .order("created_at").limit(MAX_NOTES * 2).execute().data or [])
    out = []
    for r in rows:
        if not isinstance(r, dict):
            continue
        text = _open(r.get("content"))
        if text:
            out.append({"id": r.get("id"), "text": text, "created_at": r.get("created_at")})
    return out


def remember(person, text: str, source: str = "whatsapp") -> Dict[str, Any]:
    """Save one note for this person. {"saved": text} only once it has been read back;
    otherwise {"error": reason}."""
    note = clean(text)
    if not _usable(person):
        return {"error": "unknown_person"}
    if len(note) < 3:
        return {"error": "empty"}
    existing = notes(person)
    if any(n["text"].lower() == note.lower() for n in existing):
        return {"saved": note, "already": True}
    if len(existing) >= MAX_NOTES:
        return {"error": "full"}
    row = {"tenant_id": person.tenant_id, "member_id": person.member_id,
           "user_id": None if person.member_id else person.user_id,
           "content": _seal(note), "source": source}
    _client().table(TABLE).insert(row).execute()
    # Verified, not reported: it is "saved" when it can be read back.
    if not any(n["text"] == note for n in notes(person)):
        return {"error": "not_saved"}
    return {"saved": note}


def forget(person, note_id: Any) -> bool:
    if not _usable(person) or not note_id:
        return False
    res = _owner_filter(_client().table(TABLE).delete(), person).eq("id", note_id).execute()
    return bool(getattr(res, "data", None))


def forget_all(person) -> int:
    if not _usable(person):
        return 0
    res = _owner_filter(_client().table(TABLE).delete(), person).execute()
    return len(getattr(res, "data", None) or [])


def erase_member(tenant_id: str, member_id: Any) -> int:
    """Everything kept for a team member — when they are removed from the team, or ask for
    their data to be deleted."""
    if not tenant_id or not member_id:
        return 0
    res = (_client().table(TABLE).delete().eq("tenant_id", tenant_id)
           .eq("member_id", str(member_id)).execute())
    return len(getattr(res, "data", None) or [])


def erase_for_phone(tenant_id: str, variants: List[str]) -> int:
    """Called by vula/privacy_data.py: the notes of whichever team member has this number."""
    from vula import team_index
    total = 0
    seen = set()
    for v in variants:
        try:
            row = team_index.member_for_phone(tenant_id, v)
        except Exception:  # noqa: BLE001
            row = None
        if row and row.get("id") and row["id"] not in seen:
            seen.add(row["id"])
            total += erase_member(tenant_id, row["id"])
    return total


# ── the request, recognised in code ──────────────────────────────────────────────────────────

def wants_to_remember(text: str) -> Optional[str]:
    """The note in "remember that I…" / "remember for me: …", or None. Only a first-person or
    explicitly private request — "remember we close at 4" is a business rule and is left for
    the existing company-wide tool."""
    t = (text or "").strip()
    if not t or len(t) > 600 or "\n\n" in t:
        return None
    m = _PRIVATE.match(t) or _REMEMBER.match(t)
    return clean(m.group("note")) if m else None


def _list_reply(items: List[Dict[str, Any]]) -> str:
    if not items:
        return ("I'm not keeping anything about you. If you'd like me to, say "
                "\"remember that I…\" and I'll keep it just for you.")
    lines = [f"{i}. {n['text']}" for i, n in enumerate(items, 1)]
    return ("Here's what I'm keeping about you. Only you can see this:\n" + "\n".join(lines)
            + "\n\nTo remove one, say \"forget note 2\". To remove them all, say "
              "\"forget everything about me\".")


def handle(person, text: str, channel: str = "whatsapp") -> Optional[str]:
    """If this message is about the person's own private notes — save one, show them, delete
    one or all — do it and return the reply. None for anything else (the message goes on to
    the assistant as usual). Never raises."""
    if not _usable(person) or not enabled(person.tenant_id):
        return None
    t = (text or "").strip()
    try:
        if _LIST.match(t):
            return _list_reply(notes(person))
        m = _FORGET_ONE.match(t)
        if m:
            items = notes(person)
            n = int(m.group("n"))
            if not 1 <= n <= len(items):
                return (f"I only have {len(items)} note{'s' if len(items) != 1 else ''} about you. "
                        "Say \"what do you remember about me\" to see them.")
            gone = items[n - 1]
            if forget(person, gone["id"]) and not any(x["id"] == gone["id"] for x in notes(person)):
                return f"Done, I've forgotten: \"{gone['text']}\""
            return "I couldn't remove that just now. Please try again in a moment."
        if _FORGET_ALL.match(t):
            n = forget_all(person)
            if notes(person):
                return "I couldn't remove everything just now. Please try again in a moment."
            return ("I wasn't keeping anything about you." if not n else
                    f"Done, I've forgotten all {n} thing{'s' if n != 1 else ''} I was keeping about you.")
        note = wants_to_remember(t)
        if note:
            out = remember(person, note, source=channel)
            if out.get("saved"):
                return (f"Got it. I'll remember that, just for you: \"{out['saved']}\"\n"
                        "Nobody else at the business can see it. Say \"what do you remember "
                        "about me\" to see or remove what I keep.")
            if out.get("error") == "full":
                return (f"I'm already keeping {MAX_NOTES} things about you, which is the limit. "
                        "Say \"what do you remember about me\" and remove one first.")
            return "I couldn't save that just now, so I haven't. Please try again in a moment."
    except Exception as exc:  # noqa: BLE001
        logger.warning("private note request failed (tenant=%s): %s", getattr(person, "tenant_id", "?"), exc)
        if _LIST.match(t) or _FORGET_ONE.match(t) or _FORGET_ALL.match(t) or wants_to_remember(t):
            return "I couldn't get to your private notes just now. Please try again in a moment."
    return None


# ── showing them to the assistant ────────────────────────────────────────────────────────────

_CURRENT: ContextVar[str] = ContextVar("vula_private_notes", default="")


def block_for(person) -> str:
    """This person's notes as a prompt block, newest kept when there are too many to fit."""
    if not _usable(person) or not enabled(person.tenant_id):
        return ""
    try:
        items = notes(person)
    except Exception as exc:  # noqa: BLE001 — a note must never cost a reply
        logger.debug("private notes skipped: %s", exc)
        return ""
    if not items:
        return ""
    picked: List[str] = []
    size = 0
    for n in reversed(items):
        line = f"- {n['text']}"
        if size + len(line) > MAX_BLOCK_CHARS:
            break
        picked.append(line)
        size += len(line)
    return (
        "PRIVATE NOTES — things the person you are talking to asked you to remember about "
        "themselves:\n" + "\n".join(reversed(picked)) + "\n"
        "These are theirs alone. Use them to work the way they like and to pick up where they "
        "left off. They are not facts about the business: never use one to answer a question "
        "about the business's records, prices, hours or customers, never put one in anything "
        "sent to someone else, and never mention them to anyone but this person.\n"
    )


async def load(person) -> str:
    """block_for, off the event loop (it reads the database)."""
    import asyncio
    if not _usable(person) or not enabled(person.tenant_id):
        return ""
    return await asyncio.to_thread(block_for, person)


@contextmanager
def use(block: str):
    """Make one person's notes (from `load`) available to the skills that answer this turn.
    Set only by an entry point that knows the caller IS that person."""
    token = _CURRENT.set(block or "")
    try:
        yield
    finally:
        _CURRENT.reset(token)


def current_block() -> str:
    return _CURRENT.get()
