"""What Vula remembers about a client between conversations.

The model reads only the last 24 hours of a conversation (commerce/service.py — older lines were
being echoed back as if fresh). So a client who came back after a month was a stranger: nothing
of what they asked, were told, or were promised. The messages were all still stored; the model
just never saw them.

This keeps one short, dated summary per client (commerce_client_summaries, migration 258). It is
written when a conversation has gone quiet — one model call per conversation, never per message —
and folded into the next one: the previous summary plus the new messages become the new summary.
On the client's next message it is shown to the assistant, labelled as earlier and possibly out
of date. Staff see it in the client brief (vula/clients.py).

What keeps it honest:
  - It records what was SAID. Orders, invoices and amounts are always read from the database at
    the time they are needed; the summary is never their source, and any rand amount in a
    summary that isn't in the messages it was written from is removed before it is stored.
  - It is keyed on the WhatsApp number the messages came from, for this business only.
  - "Delete my data" removes it (vula/privacy_data.py) and it expires with the conversation
    (vula/retention.py).
  - A staff member's own thread with Vula is never summarised here.

Off unless settings.client_memory_enabled.
"""
from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

TABLE = "commerce_client_summaries"
QUIET_HOURS = 2            # a conversation is "over for now" after this long with no message
LOOKBACK_DAYS = 14         # only sessions active this recently are considered each sweep
MAX_MESSAGES = 80          # per summary pass
MAX_SUMMARY_CHARS = 1200
_RAND = re.compile(r"R\s?\d[\d  ,]*(?:\.\d{1,2})?")

_PROMPT = (
    "You keep a short running note about one client of a small business, so that the next "
    "conversation with them can pick up where the last one left off.\n\n"
    "Below are the note so far (may be empty) and the newest messages between the client and the "
    "business. Write the updated note.\n\n"
    "Rules:\n"
    "- Only what the messages actually say. Never add, infer or tidy up a fact.\n"
    "- Short dated lines, newest last, e.g. '2026-10-03: asked about delivery to Paarl; told "
    "Tuesdays only.' Use the dates shown on the messages.\n"
    "- Keep: what they wanted, what they were told, anything promised to them or by them, "
    "preferences they stated, complaints, and anything left unresolved.\n"
    "- Leave out greetings, small talk, and anything about another person.\n"
    "- Do not record card numbers, ID numbers, passwords or health details.\n"
    "- Amounts and order details are kept elsewhere: mention that an order or invoice was "
    "discussed, not its figures.\n"
    "- At most 12 lines. When the note is full, merge the oldest lines into one.\n"
    "- Plain text only. If there is nothing worth keeping, reply with exactly: NOTHING\n"
)


def enabled(tenant_id: Optional[str] = None) -> bool:
    """On for everyone (CLIENT_MEMORY_ENABLED), or for this business (vula/features.py).
    With no business given: on anywhere — whether the sweep has anything to do."""
    try:
        from vula import features
        return features.on(tenant_id, "client_memory") if tenant_id else features.on_anywhere("client_memory")
    except Exception:  # noqa: BLE001
        return False


def _client():
    from vula.commerce import service
    return service._client()


def client_key(phone: Any) -> str:
    """A client's key: their number in 27… form ("" when it isn't a phone number)."""
    d = "".join(c for c in str(phone or "") if c.isdigit())
    if len(d) < 9:
        return ""
    return "27" + d[1:] if d.startswith("0") else d


def get(tenant_id: str, phone: Any) -> Optional[Dict[str, Any]]:
    key = client_key(phone)
    if not tenant_id or not key:
        return None
    try:
        rows = (_client().table(TABLE).select("summary,covers_until,updated_at")
                .eq("tenant_id", tenant_id).eq("client_key", key).limit(1).execute().data or [])
    except Exception as exc:  # noqa: BLE001 — a missing note must never cost a reply
        logger.debug("client summary read skipped (run migration 258?): %s", exc)
        return None
    return rows[0] if rows and isinstance(rows[0], dict) and rows[0].get("summary") else None


def summary_for(tenant_id: str, phone: Any) -> str:
    row = get(tenant_id, phone)
    return (row or {}).get("summary") or ""


def block_for_prompt(tenant_id: str, phone: Any) -> str:
    """The note, framed for the assistant that is about to talk to this client. "" when the
    feature is off or there is nothing on file."""
    if not enabled(tenant_id):
        return ""
    row = get(tenant_id, phone)
    if not row:
        return ""
    upto = str(row.get("covers_until") or "")[:10]
    return (
        "\n\nEARLIER CONVERSATIONS WITH THIS CUSTOMER — a note of what was said before"
        + (f" (up to {upto})" if upto else "") + ":\n"
        f"{row['summary']}\n"
        "- This is background so they don't have to repeat themselves. It may be out of date: "
        "treat it as what was said then, not as what is true now.\n"
        "- Never take a price, an amount, stock, an order's status or a delivery date from this "
        "note. Use your tools for those, every time.\n"
        "- Don't recite the note back at them. Use it the way a person who remembers them would."
    )


# ── writing the note ─────────────────────────────────────────────────────────────────────────

def strip_unsupported_amounts(summary: str, source: str) -> str:
    """Drop any line of the note that states a rand amount which isn't in the text it was
    written from. A wrong figure in a remembered note would be repeated for months."""
    def norm(m: str) -> str:
        return re.sub(r"[^\d.]", "", m).rstrip(".")
    known = {norm(m) for m in _RAND.findall(source or "")}
    kept = []
    for line in (summary or "").splitlines():
        amounts = {norm(m) for m in _RAND.findall(line)}
        if amounts and not amounts <= known:
            continue
        kept.append(line)
    return "\n".join(kept).strip()


def _render(messages: List[Dict[str, Any]]) -> str:
    who = {"user": "Client", "assistant": "Business (assistant)", "agent": "Business (staff)"}
    return "\n".join(f"{str(m.get('created_at') or '')[:10]} {who.get(m.get('role'), 'Business')}: "
                     f"{(m.get('content') or '').strip()[:600]}"
                     for m in messages if m.get("content"))


async def _write_note(previous: str, messages: List[Dict[str, Any]]) -> Optional[str]:
    """The updated note, "" for "nothing worth keeping", or None when the model call failed."""
    import litellm
    from core.llm_router import resolve_generation_route
    from core.prompt_safety import UNTRUSTED_CONTENT_RULE, fence
    transcript = _render(messages)
    litellm.drop_params = True
    try:
        model, api_key, api_base = await resolve_generation_route(task_type="client_summary")
        resp = await litellm.acompletion(
            model=model, temperature=0.1, max_tokens=700, api_key=api_key, api_base=api_base,
            messages=[
                {"role": "system", "content": _PROMPT + "\n" + UNTRUSTED_CONTENT_RULE},
                {"role": "user", "content": fence("NOTE_SO_FAR", previous or "(none)") + "\n\n"
                                            + fence("NEW_MESSAGES", transcript)},
            ])
        text = (resp.choices[0].message.content or "").strip()
    except Exception as exc:  # noqa: BLE001
        logger.warning("client summary: model call failed: %s", exc)
        return None
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.S).strip()
    if not text or text.upper().startswith("NOTHING"):
        return ""
    return strip_unsupported_amounts(text, (previous or "") + "\n" + transcript)[:MAX_SUMMARY_CHARS]


async def summarise_session(tenant_id: str, session: Dict[str, Any]) -> str:
    """Fold a quiet conversation's new messages into the client's note.
    Returns "written", "nothing_new", "nothing_worth_keeping" or "failed"."""
    from vula.identity import is_staff_session_key
    key = client_key(session.get("customer_phone") or session.get("session_key"))
    if not key or is_staff_session_key(session.get("session_key")):
        return "nothing_new"
    existing = get(tenant_id, key) or {}
    covers = existing.get("covers_until") or ""
    q = (_client().table("commerce_conversation_messages").select("role,content,created_at")
         .eq("tenant_id", tenant_id).eq("session_id", session["id"]))
    if covers:
        q = q.gt("created_at", covers)
    messages = q.order("created_at").limit(MAX_MESSAGES).execute().data or []
    messages = [m for m in messages if isinstance(m, dict) and m.get("content")]
    if not any(m.get("role") == "user" for m in messages):
        return "nothing_new"
    note = await _write_note(existing.get("summary") or "", messages)
    if note is None:
        return "failed"
    upto = str(messages[-1].get("created_at") or "")
    row = {"tenant_id": tenant_id, "client_key": key, "covers_until": upto,
           "updated_at": datetime.now(timezone.utc).isoformat(),
           # nothing new worth keeping: keep the old note, but don't re-read these messages
           "summary": note or (existing.get("summary") or "")}
    _client().table(TABLE).upsert(row, on_conflict="tenant_id,client_key").execute()
    return "written" if note else "nothing_worth_keeping"


async def sweep(limit: int = 25, now: Optional[datetime] = None) -> Dict[str, int]:
    """Summarise conversations that have gone quiet since their note was last written. Counts
    only — nothing a client said is logged."""
    counts = {"written": 0, "nothing_new": 0, "nothing_worth_keeping": 0, "failed": 0}
    if not enabled():
        return counts
    from vula.identity import is_staff_session_key
    now = now or datetime.now(timezone.utc)
    quiet_before = (now - timedelta(hours=QUIET_HOURS)).isoformat()
    since = (now - timedelta(days=LOOKBACK_DAYS)).isoformat()
    sessions = (_client().table("commerce_conversation_sessions")
                .select("id,tenant_id,session_key,customer_phone,updated_at")
                .gte("updated_at", since).lt("updated_at", quiet_before)
                .order("updated_at", desc=True).limit(400).execute().data or [])
    done = 0
    for s in sessions:
        if done >= limit:
            break
        if not isinstance(s, dict) or is_staff_session_key(s.get("session_key")):
            continue
        if not enabled(s.get("tenant_id")):            # on for this business?
            continue
        key = client_key(s.get("customer_phone") or s.get("session_key"))
        if not key:
            continue
        note = get(s["tenant_id"], key)
        if note and str(note.get("covers_until") or "") >= str(s.get("updated_at") or ""):
            continue                                     # already up to date
        try:
            outcome = await summarise_session(s["tenant_id"], s)
        except Exception as exc:  # noqa: BLE001 — one client never stops the sweep
            logger.warning("client summary failed for a session (tenant=%s): %s", s.get("tenant_id"), exc)
            outcome = "failed"
        counts[outcome] += 1
        if outcome != "nothing_new":
            done += 1
    return counts


# ── lifecycle ────────────────────────────────────────────────────────────────────────────────

def erase(tenant_id: str, variants: List[str]) -> int:
    """Delete this number's note (called by vula/privacy_data.py)."""
    keys = list({k for k in (client_key(v) for v in variants) if k})
    if not keys:
        return 0
    res = _client().table(TABLE).delete().eq("tenant_id", tenant_id).in_("client_key", keys).execute()
    return len(getattr(res, "data", None) or [])
