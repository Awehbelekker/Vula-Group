"""
vula/commerce/call_sheet.py — weekly per-rep "call sheet" digest.

A sales rep logs meetings over WhatsApp (log_meeting); each logged meeting lands as an entry on
that rep's own standing, OPEN call-sheet document (one open row per rep at a time, migration
138). The rep can review it (view_call_sheet) and correct/add to it (update_call_sheet) before
it goes out. A weekly job checks whether each rep's configured day/time has arrived and, if so,
compiles the open document's entries into one digest and sends it — email always carries the
full text; WhatsApp (if configured) sends only a short pre-approved TEMPLATE notice pointing to
the email, since a proactive WhatsApp send outside the customer-initiated session window
requires Meta template approval (same constraint already accepted for the supplier-PO WhatsApp
leg, vula/commerce/purchase_orders.py) — free text would fail outright. Once sent, the row is
marked 'sent' and the next entry lazily opens a fresh one.

Plain text only, not a PDF attachment — vula/email_imap/service.py's send() has no attachment
support today; a PDF upgrade is a natural future extension if that changes, not built here.
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from vula.commerce import service

log = logging.getLogger(__name__)

_ACTIONS = {"add", "edit", "remove"}


def _client():
    return service._client()


def get_or_create_open_call_sheet(tenant_id: str, rep_whatsapp: str) -> Dict[str, Any]:
    """Return the rep's current status='open' vula_call_sheets row, creating one if none
    exists. Tolerates the rare race on the unique-open-row index by re-fetching on conflict."""
    rows = (_client().table("vula_call_sheets").select("*")
            .eq("tenant_id", tenant_id).eq("rep_whatsapp", rep_whatsapp)
            .eq("status", "open").limit(1).execute().data or [])
    if rows:
        return rows[0]
    try:
        res = (_client().table("vula_call_sheets")
               .insert({"tenant_id": tenant_id, "rep_whatsapp": rep_whatsapp,
                        "status": "open", "entries": []}).execute())
        if res.data:
            return res.data[0]
    except Exception as exc:
        log.debug("call sheet create raced or failed, re-fetching: %s", exc)
    rows = (_client().table("vula_call_sheets").select("*")
            .eq("tenant_id", tenant_id).eq("rep_whatsapp", rep_whatsapp)
            .eq("status", "open").limit(1).execute().data or [])
    if rows:
        return rows[0]
    raise RuntimeError(f"could not get or create an open call sheet for {tenant_id}/{rep_whatsapp}")


def append_entry(tenant_id: str, rep_whatsapp: str, source: str, text: str,
                  meeting_note_id: Optional[str] = None) -> Dict[str, Any]:
    """Append one entry (source='log_meeting'|'manual') to the rep's open call sheet."""
    row = get_or_create_open_call_sheet(tenant_id, rep_whatsapp)
    entries: List[Dict[str, Any]] = list(row.get("entries") or [])
    entries.append({
        "id": uuid.uuid4().hex[:8], "source": source, "text": text,
        "meeting_note_id": meeting_note_id,
        "created_at": datetime.now(timezone.utc).isoformat(),
    })
    res = (_client().table("vula_call_sheets").update({"entries": entries})
           .eq("id", row["id"]).execute())
    return res.data[0] if res.data else {**row, "entries": entries}


async def parse_update_instruction(entries: List[Dict[str, Any]], instruction: str) -> Dict[str, Any]:
    """Interpret a plain-language instruction against the current entries — constrained to
    add/edit/remove, never open generation, mirroring page_copy.py's refine_page_copy framing
    ('apply only what this specific instruction implies, leave everything else untouched').
    Returns {"action": "add"|"edit"|"remove", "entry_id": str|None, "text": str|None} or
    {"error": "..."}. The raw LLM output is never trusted as-is — whitelist-validated below."""
    import json as _json
    import litellm
    from core.llm_router import resolve_generation_route

    litellm.drop_params = True
    listing = "\n".join(f'- id={e["id"]}: {e["text"][:200]}' for e in entries) or "(no entries yet)"
    system = (
        "A sales rep is editing their weekly call sheet (a list of meeting-note entries). Apply "
        "ONLY what their instruction specifically asks for — never touch or rewrite anything else. "
        "Return STRICT JSON only, exactly one of:\n"
        '{"action": "add", "text": "<the new entry text, written from the instruction>"}\n'
        '{"action": "edit", "entry_id": "<id of the ONE existing entry this refers to>", '
        '"text": "<its corrected/updated text>"}\n'
        '{"action": "remove", "entry_id": "<id of the ONE existing entry to remove>"}\n'
        'If the instruction doesn\'t clearly match one existing entry for edit/remove, or is too '
        'vague to act on, return {"error": "<short plain-English reason>"} instead.\n\n'
        f"Current entries:\n{listing}"
    )
    try:
        model, api_key, api_base = await resolve_generation_route(task_type="call_sheet_update")
        resp = await litellm.acompletion(
            model=model, temperature=0.1, max_tokens=300, api_key=api_key, api_base=api_base,
            messages=[{"role": "system", "content": system}, {"role": "user", "content": instruction}],
        )
        raw = (resp.choices[0].message.content or "").strip()
        raw = raw.replace("```json", "").replace("```", "").strip()
        i, j = raw.find("{"), raw.rfind("}")
        if i < 0 or j <= i:
            return {"error": "Couldn't understand that — try describing the change more simply."}
        obj = _json.loads(raw[i:j + 1])
    except Exception as exc:
        log.warning("call sheet update parse failed: %s", exc)
        return {"error": "Couldn't understand that right now — please try again."}

    if obj.get("error"):
        return {"error": str(obj["error"])[:200]}
    action = obj.get("action")
    if action not in _ACTIONS:
        return {"error": "Couldn't map that to a clear change — try again."}
    entry_id = obj.get("entry_id")
    if action in ("edit", "remove"):
        if not entry_id or not any(e["id"] == entry_id for e in entries):
            return {"error": "Couldn't tell which entry you meant — try naming the contact or date."}
    if action in ("add", "edit") and not (obj.get("text") or "").strip():
        return {"error": "Need the text for that entry."}
    return {"action": action, "entry_id": entry_id, "text": (obj.get("text") or "").strip() or None}


def apply_edit(tenant_id: str, rep_whatsapp: str, action: str, entry_id: Optional[str],
               text: Optional[str]) -> Dict[str, Any]:
    """Apply an already-parsed/validated add|edit|remove action to the rep's open call sheet."""
    row = get_or_create_open_call_sheet(tenant_id, rep_whatsapp)
    entries: List[Dict[str, Any]] = list(row.get("entries") or [])
    if action == "add":
        entries.append({"id": uuid.uuid4().hex[:8], "source": "manual", "text": text,
                        "meeting_note_id": None,
                        "created_at": datetime.now(timezone.utc).isoformat()})
    elif action == "edit":
        for e in entries:
            if e["id"] == entry_id:
                e["text"] = text
                break
    elif action == "remove":
        entries = [e for e in entries if e["id"] != entry_id]
    res = (_client().table("vula_call_sheets").update({"entries": entries})
           .eq("id", row["id"]).execute())
    return res.data[0] if res.data else {**row, "entries": entries}


def format_call_sheet(rep_name: str, entries: List[Dict[str, Any]]) -> str:
    if not entries:
        return f"Weekly Call Sheet — {rep_name}\n\nNo entries logged."
    lines = [f"Weekly Call Sheet — {rep_name}", ""]
    for idx, e in enumerate(entries, start=1):
        when = (e.get("created_at") or "")[:10]
        lines.append(f"{idx}. ({when}) {e.get('text', '')}")
    lines.append("")
    lines.append(f"{len(entries)} entr{'y' if len(entries) == 1 else 'ies'} this period.")
    return "\n".join(lines)


def is_due(rep: Dict[str, Any], now: datetime) -> bool:
    """A rep's call sheet is due once a week, on their configured day/hour, and not already sent
    in the last 24h — mirrors job_config.py's day-of-week + not-already-fired-today check
    (friday_catch_reminder/claim_run), adapted from tenant-scoped to per-rep."""
    if now.weekday() != (rep.get("call_sheet_day_of_week") if rep.get("call_sheet_day_of_week") is not None else 4):
        return False
    hour = rep.get("call_sheet_hour") if rep.get("call_sheet_hour") is not None else 17
    if now.hour < hour:
        return False
    last = rep.get("call_sheet_last_sent_at")
    if last:
        try:
            last_dt = datetime.fromisoformat(str(last).replace("Z", "+00:00"))
            if (now - last_dt).total_seconds() < 24 * 3600:
                return False
        except Exception:
            pass
    return True


async def send_call_sheet(tenant_id: str, rep: Dict[str, Any]) -> Dict[str, Any]:
    """Compile the rep's open call sheet and send it per their configured channel(s). Each
    channel is independently best-effort (one failing never blocks the other — mirrors
    purchase_orders.py::send_purchase_order's per-channel-independent-failure precedent).
    Returns {"email": bool|None, "whatsapp": bool|None, "meeting_count": int, "sheet_id": str|None}.
    None for a channel means it wasn't configured/attempted, not that it failed."""
    result: Dict[str, Any] = {"email": None, "whatsapp": None, "meeting_count": 0, "sheet_id": None}
    row = get_or_create_open_call_sheet(tenant_id, rep["whatsapp"])
    entries = row.get("entries") or []
    result["meeting_count"] = len(entries)
    result["sheet_id"] = row["id"]

    rep_name = rep.get("name") or "Rep"
    body = format_call_sheet(rep_name, entries)
    subject = f"Weekly Call Sheet — {rep_name}"
    channel = rep.get("call_sheet_channel") or "email"

    if channel in ("email", "both") and rep.get("call_sheet_recipient_email"):
        from vula.email_imap.credentials import get_email_creds
        from vula.email_imap.service import send as email_send
        creds = get_email_creds(tenant_id)
        if not creds:
            log.info("call sheet email skipped for %s/%s — no connected email account", tenant_id, rep["whatsapp"])
            result["email"] = False
        else:
            try:
                sent = await email_send(creds, rep["call_sheet_recipient_email"], subject, body)
                result["email"] = bool(sent.get("sent"))
                if not result["email"]:
                    log.warning("call sheet email failed for %s/%s: %s", tenant_id, rep["whatsapp"], sent.get("error"))
            except Exception as exc:
                log.warning("call sheet email raised for %s/%s: %s", tenant_id, rep["whatsapp"], exc)
                result["email"] = False

    if channel in ("whatsapp", "both") and rep.get("call_sheet_recipient_phone"):
        from vula.api.whatsapp import _send_wa_template
        try:
            ok = await _send_wa_template(tenant_id, rep["call_sheet_recipient_phone"], "call_sheet_ready")
            result["whatsapp"] = ok
            if not ok:
                log.info("call sheet WhatsApp notice skipped/failed for %s/%s — needs an approved "
                          "'call_sheet_ready' template", tenant_id, rep["whatsapp"])
        except Exception as exc:
            log.warning("call sheet WhatsApp send raised for %s/%s: %s", tenant_id, rep["whatsapp"], exc)
            result["whatsapp"] = False

    return result


async def run_weekly_call_sheets() -> int:
    """For every sales_rep, across every tenant, whose configured day/time is due: compile and
    send their call sheet. Never lets one rep's failure (bad config, missing SMTP, composer
    error) stop the rest — same per-row defensive discipline as _daily_commerce_jobs_loop."""
    try:
        reps = (_client().table("vula_team_members").select(
                    "id,tenant_id,whatsapp,name,call_sheet_recipient_email,"
                    "call_sheet_recipient_phone,call_sheet_channel,call_sheet_day_of_week,"
                    "call_sheet_hour,call_sheet_minute,call_sheet_last_sent_at")
                .eq("role", "sales_rep").eq("active", True).execute().data or [])
    except Exception as exc:
        log.warning("call sheet rep query failed: %s", exc)
        return 0

    now = datetime.now(timezone.utc)
    sent_count = 0
    for rep in reps:
        if not (rep.get("call_sheet_recipient_email") or rep.get("call_sheet_recipient_phone")):
            continue
        try:
            if not is_due(rep, now):
                continue
            result = await send_call_sheet(rep["tenant_id"], rep)
            # A due-and-handled cycle (even an empty one) counts as "sent for this cycle" — the
            # explicit day/time is a calendar slot, not a rolling "n days since last content."
            _client().table("vula_call_sheets").update(
                {"status": "sent", "sent_at": now.isoformat()}
            ).eq("id", result["sheet_id"]).execute()
            _client().table("vula_team_members").update(
                {"call_sheet_last_sent_at": now.isoformat()}
            ).eq("id", rep["id"]).execute()
            if result.get("email") or result.get("whatsapp"):
                sent_count += 1
        except Exception as exc:
            log.warning("call sheet failed for rep %s/%s: %s", rep.get("tenant_id"), rep.get("whatsapp"), exc)
    return sent_count
