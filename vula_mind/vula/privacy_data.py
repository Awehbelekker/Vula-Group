"""Everything Vula holds about one phone number's conversations: erase it, or hand it over.

POPIA gives a person the right to have their data deleted and to get a copy of it. Both used to
cover one table (vula_chat_messages, exact phone only). On a commerce line the conversation lives
in commerce_conversation_messages, so "delete my data" removed almost nothing while the reply
said it had, and promised the rest "within 30 days" with no job behind that promise.

This is the one list of where conversation data lives. A new store that holds what a person said
adds a step here, so delete and export can never fall behind again.

Not erased, on purpose: orders, invoices and payments. A business must keep its financial
records by law, and the reply to the person says so. Deals in the pipeline (vula_deals) are the
business's own record too, and are kept like orders.
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


def _member_thread(tenant_id: str, variants: List[str]) -> List[str]:
    """A team member's dashboard thread with Vula (webadmin:m:{id}), when this number is theirs.
    13 Nov 2026: erase and export covered their WhatsApp thread only."""
    try:
        from vula import team_index
        for v in variants:
            row = team_index.member_for_phone(tenant_id, v)
            if row and row.get("id"):
                return [f"webadmin:m:{row['id']}"]
    except Exception as exc:  # noqa: BLE001 — the number's own threads still count
        logger.debug("data: member thread lookup skipped: %s", exc)
    return []


def _session_ids(tenant_id: str, variants: List[str]) -> List[str]:
    """This number's conversation sessions: as a customer ({phone}) and, for one of the
    business's own people, their threads with Vula (admin:{phone} and their dashboard thread)."""
    keys = variants + [f"admin:{v}" for v in variants] + _member_thread(tenant_id, variants)
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


def _member_key(tenant_id: str, variants: List[str]) -> str:
    """The team member's person key (m:<id>) for this number, or "" when it isn't theirs."""
    thread = _member_thread(tenant_id, variants)
    return thread[0][len("webadmin:"):] if thread else ""


def _erase_research_notes(tenant_id: str, variants: List[str]) -> int:
    """What a team member had Vula research (vula/research_memory.py, migration 269)."""
    from vula import research_memory
    return research_memory.erase_for(tenant_id, _member_key(tenant_id, variants))


def _erase_private_notes(tenant_id: str, variants: List[str]) -> int:
    from vula import personal_memory
    return personal_memory.erase_for_phone(tenant_id, variants)


def same_number(stored: str, variants: List[str]) -> bool:
    """A number typed into a form ("082 111 2222", "+27 82…") against this person's number:
    the last nine digits, as data_export matches orders."""
    got = "".join(c for c in (stored or "") if c.isdigit())
    want = {("".join(c for c in v if c.isdigit()))[-9:] for v in variants}
    return len(got) >= 9 and got[-9:] in want


def _own_enquiries(tenant_id: str, variants: List[str], cols: str = "id,whatsapp") -> List[Dict[str, Any]]:
    """This person's property enquiries. The website stores the number as typed, so it is matched
    here rather than with in_()."""
    rows = (_client().table("vula_property_enquiries").select(cols)
            .eq("tenant_id", tenant_id).execute().data or [])
    return [r for r in rows if isinstance(r, dict) and same_number(r.get("whatsapp") or "", variants)]


def _erase_property_enquiries(tenant_id: str, variants: List[str]) -> int:
    ids = [r["id"] for r in _own_enquiries(tenant_id, variants) if r.get("id")]
    if not ids:
        return 0
    return _count(_client().table("vula_property_enquiries").delete()
                  .eq("tenant_id", tenant_id).in_("id", ids).execute())


def _erase_buyer_wish(tenant_id: str, variants: List[str]) -> int:
    """What they said they're looking for. Left behind, it would keep matching them to new
    listings."""
    return _count(_client().table("vula_buyer_wishes").delete()
                  .eq("tenant_id", tenant_id).in_("phone", variants).execute())


def _erase_listings_sent(tenant_id: str, variants: List[str]) -> int:
    return _count(_client().table("vula_listing_shares").delete()
                  .eq("tenant_id", tenant_id).in_("phone", variants).execute())


def _by_phone(table: str, column: str = "phone") -> Callable[[str, List[str]], int]:
    """A step that deletes this business's rows for the number in `table`.`column`."""
    def step(tenant_id: str, variants: List[str]) -> int:
        return _count(_client().table(table).delete()
                      .eq("tenant_id", tenant_id).in_(column, variants).execute())
    step.__name__ = f"_erase_{table}"
    return step


# (label, step). Order matters only for readability; each step stands alone.
# 13 Nov 2026 audit: six stores that hold what was sent to or about the person were missing.
# Left out on purpose: bookings (the business's own appointment record) and vula_wa_prompt_state
# (no tenant_id, so deleting by number would reach the person's state with other businesses).
ERASE_STEPS: List[Tuple[str, Callable[[str, List[str]], int]]] = [
    ("chat_messages", _erase_chat_messages),
    ("conversations", _erase_sessions),
    ("turn_records", _erase_turns),
    ("question_log", _erase_kb_turns),
    ("open_questions", _erase_open_questions),
    ("escalations", _erase_escalations),
    ("client_note", _erase_client_note),
    ("private_notes", _erase_private_notes),
    ("research_notes", _erase_research_notes),
    ("property_enquiries", _erase_property_enquiries),
    ("buyer_wish", _erase_buyer_wish),
    ("listings_sent", _erase_listings_sent),
    ("messages_sent", _by_phone("vula_wa_outbound", "to_phone")),
    ("contact_record", _by_phone("commerce_contacts")),
    ("broadcasts_received", _by_phone("commerce_broadcast_recipients")),
    ("delivery_failures", _by_phone("vula_webhook_failures")),
    ("flow_progress", _by_phone("commerce_flow_sessions")),
    ("pending_confirmations", _by_phone("commerce_pending_confirmations")),
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


def research_notes(tenant_id: str, phone: str) -> List[Dict[str, Any]]:
    """What this team member had Vula research: each question and when (for an export)."""
    variants = phone_variants(phone)
    if not tenant_id or not variants:
        return []
    try:
        from vula import research_memory
        return research_memory.export_for(tenant_id, _member_key(tenant_id, variants))
    except Exception as exc:  # noqa: BLE001
        logger.debug("data export: research notes skipped: %s", exc)
        return []


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


def property_records(tenant_id: str, phone: str) -> Dict[str, Any]:
    """Their property enquiries, what they said they're looking for, and the listings sent to
    them (vula/listings). Each part reads on its own: one that fails or whose table isn't there
    is empty, never the whole export."""
    out: Dict[str, Any] = {"enquiries": [], "wish": "", "listings_sent": []}
    variants = phone_variants(phone)
    if not tenant_id or not variants:
        return out
    titles: Dict[str, str] = {}

    def title(pid: Any) -> str:
        if not pid:
            return ""
        if pid not in titles:
            try:
                rows = (_client().table("vula_properties").select("title").eq("tenant_id", tenant_id)
                        .eq("id", pid).limit(1).execute().data or [])
                titles[pid] = (rows[0].get("title") or "") if rows else ""
            except Exception:  # noqa: BLE001
                titles[pid] = ""
        return titles[pid]

    try:
        rows = _own_enquiries(tenant_id, variants,
                              "id,whatsapp,property_id,message,preferred_times,created_at")
        out["enquiries"] = [{"date": str(r.get("created_at") or "")[:10], "property": title(r.get("property_id")),
                             "message": r.get("message") or "", "preferred_times": r.get("preferred_times") or ""}
                            for r in sorted(rows, key=lambda r: str(r.get("created_at") or ""))]
    except Exception as exc:  # noqa: BLE001
        logger.debug("data export: enquiries skipped: %s", exc)
    try:
        rows = (_client().table("vula_buyer_wishes").select("*").eq("tenant_id", tenant_id)
                .in_("phone", variants).limit(1).execute().data or [])
        if rows:
            from vula.listings.sharing import describe_wish
            out["wish"] = describe_wish(rows[0])
    except Exception as exc:  # noqa: BLE001
        logger.debug("data export: buyer wish skipped: %s", exc)
    try:
        rows = (_client().table("vula_listing_shares").select("property_id,created_at")
                .eq("tenant_id", tenant_id).in_("phone", variants).execute().data or [])
        out["listings_sent"] = [{"date": str(r.get("created_at") or "")[:10], "property": title(r.get("property_id"))}
                                for r in sorted(rows, key=lambda r: str(r.get("created_at") or ""))]
    except Exception as exc:  # noqa: BLE001
        logger.debug("data export: listings sent skipped: %s", exc)
    return out
