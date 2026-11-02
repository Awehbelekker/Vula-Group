"""
vula/api/whatsapp_account_events.py — Meta's account-level webhook fields (31 Oct 2026).

These came in on the webhook and were dropped, so a rejected or paused template, a quality
downgrade or a banned account went unnoticed until sends started failing:

  message_template_status_update   REJECTED / PAUSED / DISABLED (and FLAGGED) → operator alert
  phone_number_quality_update      DOWNGRADE / FLAGGED, or a RED / YELLOW rating → operator alert
  account_update                   banned, restricted, disabled or a policy violation → alert
  template_category_update         Meta re-categorised a template → logged

Every event gets one structured log line; the bad ones also go to the operator through
vula/integrations/platform_support.py::alert_operator (docs/operator-alerts.md). Only Meta's own
fields are used (template name, event, reason, the business's number and limits): these payloads
carry no customer content, and none is logged. A redelivered event alerts once. Never raises.
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Dict, Optional

log = logging.getLogger(__name__)

ACCOUNT_FIELDS = ("account_update", "message_template_status_update",
                  "phone_number_quality_update", "template_category_update")

_BAD_TEMPLATE = {"REJECTED", "PAUSED", "DISABLED", "FLAGGED"}
_BAD_QUALITY_EVENT = {"DOWNGRADE", "FLAGGED"}
_BAD_RATING = {"RED", "YELLOW"}
# account_update events / ban states that stop or limit sending.
_BAD_ACCOUNT_WORDS = ("BAN", "RESTRICT", "DISABLE", "VIOLATION")
_GOOD_ACCOUNT_WORDS = ("REINSTATE", "UNBAN", "UNRESTRICT", "ENABLE")

_SEEN: Dict[str, float] = {}      # event key → when it was alerted (Meta redelivers)
_SEEN_TTL_S = 6 * 3600


def _s(v: Any, limit: int = 120) -> str:
    return str(v if v is not None else "").strip()[:limit]


def _tenant_for_waba(waba_id: str) -> Optional[str]:
    """The business a WABA belongs to, or None. Never raises."""
    if not waba_id:
        return None
    try:
        from vula.commerce import service
        rows = (service._client().table("vula_whatsapp_accounts").select("tenant_id")
                .eq("waba_id", waba_id).limit(1).execute().data or [])
        return rows[0].get("tenant_id") if rows else None
    except Exception as exc:  # noqa: BLE001
        log.info("WABA lookup failed: %s", type(exc).__name__)
        return None


def _assess(field: str, value: Dict[str, Any]) -> tuple[bool, Dict[str, str], str]:
    """(alert?, the safe fields to log, a one-line description)."""
    event = _s(value.get("event")).upper()
    if field in ("message_template_status_update", "template_category_update"):
        info = {"event": event, "template": _s(value.get("message_template_name")),
                "template_id": _s(value.get("message_template_id")),
                "language": _s(value.get("message_template_language"), 20),
                "reason": _s(value.get("reason"))}
        if field == "template_category_update":
            info.update(previous=_s(value.get("previous_category")), new=_s(value.get("new_category")))
            return False, info, (f"template '{info['template']}' re-categorised "
                                 f"{info['previous'] or '?'} → {info['new'] or '?'}")
        line = f"template '{info['template']}' ({info['language'] or '?'}) is {event or 'updated'}"
        if info["reason"] and info["reason"].upper() != "NONE":
            line += f" — reason: {info['reason']}"
        return event in _BAD_TEMPLATE, info, line

    if field == "phone_number_quality_update":
        rating = _s(value.get("quality_rating") or value.get("current_quality")
                    or value.get("quality_score"), 20).upper()
        info = {"event": event, "number": _s(value.get("display_phone_number"), 30),
                "limit": _s(value.get("current_limit"), 40), "rating": rating}
        line = f"number {info['number'] or '?'} quality {event or 'update'}"
        if rating:
            line += f", rating {rating}"
        if info["limit"]:
            line += f", daily limit {info['limit']}"
        return (event in _BAD_QUALITY_EVENT or rating in _BAD_RATING), info, line

    # account_update
    ban = value.get("ban_info") if isinstance(value.get("ban_info"), dict) else {}
    ban_state = _s(ban.get("waba_ban_state"), 40).upper()
    restrictions = value.get("restriction_info")
    kinds = []
    if isinstance(restrictions, list):
        kinds = [_s(r.get("restriction_type"), 60) for r in restrictions if isinstance(r, dict)]
    violation = value.get("violation_info") if isinstance(value.get("violation_info"), dict) else {}
    info = {"event": event, "ban_state": ban_state, "restrictions": ",".join(k for k in kinds if k),
            "violation": _s(violation.get("violation_type"), 60),
            "number": _s(value.get("phone_number"), 30)}
    line = f"account {event or 'update'}"
    for label, key in (("ban state", "ban_state"), ("restrictions", "restrictions"), ("violation", "violation")):
        if info[key]:
            line += f", {label} {info[key]}"
    words = f"{event} {ban_state}"
    bad = (any(w in words for w in _BAD_ACCOUNT_WORDS) and not any(w in words for w in _GOOD_ACCOUNT_WORDS)) \
        or bool(kinds) or bool(info["violation"])
    return bad, info, line


def _first_time(key: str) -> bool:
    now = time.time()
    for k, at in list(_SEEN.items()):
        if now - at > _SEEN_TTL_S:
            _SEEN.pop(k, None)
    if key in _SEEN:
        return False
    _SEEN[key] = now
    return True


async def handle(field: str, waba_id: Optional[str], value: Any) -> str:
    """Log one account-level event and alert the operator when it's bad. Returns "alerted",
    "logged", "duplicate" or "error"; never raises."""
    try:
        value = value if isinstance(value, dict) else {}
        bad, info, line = _assess(field, value)
        tenant = await asyncio.to_thread(_tenant_for_waba, waba_id or "")
        log.log(logging.WARNING if bad else logging.INFO,
                "WA account event field=%s waba=%s tenant=%s %s", field, waba_id or "?", tenant or "?",
                " ".join(f"{k}={v}" for k, v in info.items() if v))
        if not bad:
            return "logged"
        if not _first_time("|".join([field, waba_id or "", *(f"{k}={v}" for k, v in sorted(info.items()))])):
            return "duplicate"
        from vula.integrations import platform_support
        who = tenant or (f"WhatsApp account {waba_id}" if waba_id else "an unknown WhatsApp account")
        await platform_support.alert_operator(f"WhatsApp on {who}: {line}.")
        return "alerted"
    except Exception as exc:  # noqa: BLE001
        log.warning("WA account event %s not handled: %s", field, type(exc).__name__)
        return "error"
