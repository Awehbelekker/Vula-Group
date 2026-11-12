"""Everything Vula holds about one phone number's conversations: erase it, or hand it over.

POPIA gives a person the right to have their data deleted and to get a copy of it. Both used to
cover one table (vula_chat_messages, exact phone only). On a commerce line the conversation lives
in commerce_conversation_messages, so "delete my data" removed almost nothing while the reply
said it had, and promised the rest "within 30 days" with no job behind that promise.

This is the one list of where conversation data lives. A new store that holds what a person said
adds a step here, so delete and export can never fall behind again.

Not erased, on purpose: orders, invoices and payments. A business must keep its financial
records by law, and the reply to the person says so.
"""
from __future__ import annotations

import logging
from types import SimpleNamespace
from typing import Any, Callable, Dict, List, Tuple

logger = logging.getLogger(__name__)


def _client():
    from vula.commerce import service
    return service._client()


def phone_variants(phone: str) -> List[str]:
    """The spellings one number is stored under (27821234567, 0821234567, +27821234567)."""
    raw = (phone or "").strip()
    digits = "".join(c for c in raw if c.isdigit())
    if not digits:
        return []
    intl = "27" + digits[1:] if digits.startswith("0") else digits
    out = [raw, digits, intl, "+" + intl]
    if intl.startswith("27") and len(intl) > 2:
        out.append("0" + intl[2:])
    seen: List[str] = []
    for v in out:
        if v and v not in seen:
            seen.append(v)
    return seen


def _session_ids(tenant_id: str, variants: List[str]) -> List[str]:
    """This number's conversation sessions: as a customer ({phone}) and, for one of the
    business's own people, their thread with Vula (admin:{phone})."""
    keys = variants + [f"admin:{v}" for v in variants]
    rows = (_client().table("commerce_conversation_sessions").select("id")
            .eq("tenant_id", tenant_id).in_("session_key", keys).execute().data or [])
    return [r["id"] for r in rows if isinstance(r, dict) and r.get("id")]


def _count(res: Any) -> int:
    return len(getattr(res, "data", None) or [])


# ── erase ────────────────────────────────────────────────────────────────────────────────────

def _erase_chat_messages(tenant_id: str, variants: List[str]) -> int:
    db = _client()
    n = _count(db.table("vula_chat_messages").delete()
               .eq("tenant_id", tenant_id).in_("phone", variants).execute())
    for v in variants:      # project threads are stored as "{phone}:{project_id}"
        n += _count(db.table("vula_chat_messages").delete()
                    .eq("tenant_id", tenant_id).like("phone", f"{v}:%").execute())
    return n


def _erase_sessions(tenant_id: str, variants: List[str]) -> int:
    db = _client()
    ids = _session_ids(tenant_id, variants)
    if not ids:
        return 0
    n = _count(db.table("commerce_conversation_messages").delete()
               .eq("tenant_id", tenant_id).in_("session_id", ids).execute())
    db.table("vula_reply_feedback").delete().eq("tenant_id", tenant_id).in_("session_id", ids).execute()
    db.table("commerce_conversation_sessions").delete().eq("tenant_id", tenant_id).in_("id", ids).execute()
    return n


def _erase_turns(tenant_id: str, variants: List[str]) -> int:
    return _count(_client().table("vula_turns").delete()
                  .eq("tenant_id", tenant_id).in_("phone", variants).execute())


def _erase_kb_turns(tenant_id: str, variants: List[str]) -> int:
    from vula.kb_learning import asker_key
    keys = list({asker_key(tenant_id, v) for v in variants})
    return _count(_client().table("vula_kb_turns").delete()
                  .eq("tenant_id", tenant_id).in_("asker", keys).execute())


def _erase_open_questions(tenant_id: str, variants: List[str]) -> int:
    return _count(_client().table("vula_open_questions").delete()
                  .eq("tenant_id", tenant_id).in_("phone", variants).execute())


def _erase_escalations(tenant_id: str, variants: List[str]) -> int:
    return _count(_client().table("vula_escalations").delete()
                  .eq("tenant_id", tenant_id).in_("customer_phone", variants).execute())


def _erase_client_note(tenant_id: str, variants: List[str]) -> int:
    from vula import client_memory
    return client_memory.erase(tenant_id, variants)


def _erase_private_notes(tenant_id: str, variants: List[str]) -> int:
    from vula import personal_memory
    return personal_memory.erase_for_phone(tenant_id, variants)


# (label, step). Order matters only for readability; each step stands alone.
ERASE_STEPS: List[Tuple[str, Callable[[str, List[str]], int]]] = [
    ("chat_messages", _erase_chat_messages),
    ("conversations", _erase_sessions),
    ("turn_records", _erase_turns),
    ("question_log", _erase_kb_turns),
    ("open_questions", _erase_open_questions),
    ("escalations", _erase_escalations),
    ("client_note", _erase_client_note),
    ("private_notes", _erase_private_notes),
]


def erase(tenant_id: str, phone: str) -> Dict[str, Any]:
    """Delete every conversation record this number has with this business, now.

    Returns {"removed": {label: rows}, "failed": [labels]}. A step that fails is reported, never
    hidden: the caller tells the person what was and wasn't removed.
    """
    variants = phone_variants(phone)
    removed: Dict[str, int] = {}
    failed: List[str] = []
    if not tenant_id or not variants:
        return {"removed": removed, "failed": failed}
    for label, step in ERASE_STEPS:
        try:
            removed[label] = int(step(tenant_id, variants) or 0)
        except Exception as exc:  # noqa: BLE001 — one store failing must not stop the others
            logger.error("data erase: %s failed (tenant=%s): %s", label, tenant_id, exc)
            failed.append(label)
    return {"removed": removed, "failed": failed}


# ── export ───────────────────────────────────────────────────────────────────────────────────

def conversation_messages(tenant_id: str, phone: str, limit: int = 500) -> List[SimpleNamespace]:
    """This number's messages on the commerce path (customer thread, and their own thread with
    Vula if they are staff), oldest first, as objects with .role/.text/.created_at — the same
    shape vula/chat/history.py returns, so an export can merge the two."""
    variants = phone_variants(phone)
    if not tenant_id or not variants:
        return []
    try:
        ids = _session_ids(tenant_id, variants)
        if not ids:
            return []
        rows = (_client().table("commerce_conversation_messages")
                .select("role,content,created_at").eq("tenant_id", tenant_id)
                .in_("session_id", ids).order("created_at", desc=True).limit(limit).execute().data or [])
    except Exception as exc:  # noqa: BLE001
        logger.warning("data export: conversation lookup failed (tenant=%s): %s", tenant_id, exc)
        return []
    out = [SimpleNamespace(role="user" if r.get("role") == "user" else "assistant",
                           text=r.get("content") or "", created_at=r.get("created_at") or "")
           for r in rows if isinstance(r, dict) and r.get("content")]
    return list(reversed(out))


def merge_messages(*groups: List[Any]) -> List[Any]:
    """Several message lists as one, oldest first, without the copies a staff turn has in both
    stores (the knowledge path mirrors it into the commerce tables)."""
    seen = set()
    merged: List[Any] = []
    for m in sorted((m for g in groups for m in (g or [])), key=lambda m: str(m.created_at or "")):
        key = (m.role, (m.text or "").strip(), str(m.created_at or "")[:16])
        if key in seen:
            continue
        seen.add(key)
        merged.append(m)
    return merged
