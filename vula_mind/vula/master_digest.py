"""vula/master_digest.py — Monday morning email to the Vula team: how every tenant is doing.

The master Tenants page already computes a health light per tenant (WhatsApp connected,
unanswered escalations, last activity → dormant). Nobody opens it on a quiet week, so the same
signals — plus documents waiting for a project and migrations missing in production — go out
once a week to settings.team_email, the address that already receives signup alerts.

Email, not WhatsApp: Meta's 24-hour rule would turn a proactive weekly WhatsApp into a template
nudge that can't carry the report.

Sent once per ISO week. The marker is a vula_admin_audit row (action 'master_digest_sent'), so a
restart or a second worker inside the send window never sends it twice.
"""
from __future__ import annotations

import html
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional

log = logging.getLogger(__name__)

SAST = timezone(timedelta(hours=2))
_ACTION = "master_digest_sent"
_ORDER = {"red": 0, "amber": 1, "green": 2, "off": 3}


def _client():
    from vula.commerce import service as cs
    return cs._client()


def _stuck_documents(db, tenant_ids: list) -> Dict[str, int]:
    """Documents filed but still waiting for someone to say which project they belong to."""
    out: Dict[str, int] = {}
    try:
        rows = (db.table("vula_filed_documents").select("tenant_id").eq("status", "pending_project")
                .limit(5000).execute().data or [])
        for r in rows:
            if r.get("tenant_id") in tenant_ids:
                out[r["tenant_id"]] = out.get(r["tenant_id"], 0) + 1
    except Exception as exc:
        log.debug("digest: stuck documents skipped: %s", exc)
    return out


def build() -> Dict[str, Any]:
    """Everything the digest says, as data (the master panel previews the same thing)."""
    from vula.api.master import tenant_overview
    tenants = tenant_overview().get("tenants") or []
    db = _client()
    stuck = _stuck_documents(db, [t["tenant_id"] for t in tenants])
    try:
        from vula.startup_checks import check_schema
        missing = check_schema()
    except Exception as exc:
        log.debug("digest: schema check skipped: %s", exc)
        missing = []
    rows = []
    for t in tenants:
        reasons = list(t.get("health_reasons") or [])
        if stuck.get(t["tenant_id"]):
            reasons.append(f"{stuck[t['tenant_id']]} document(s) waiting for a project")
        rows.append({"tenant_id": t["tenant_id"], "name": t.get("display_name") or t["tenant_id"],
                     "health": t.get("health") or "green", "reasons": reasons,
                     "dormant": bool(t.get("dormant")), "last_activity": t.get("last_activity"),
                     "open_escalations": t.get("open_escalations") or 0,
                     "stuck_documents": stuck.get(t["tenant_id"], 0)})
    rows.sort(key=lambda r: (_ORDER.get(r["health"], 9), r["name"].lower()))
    active = [r for r in rows if r["health"] != "off"]
    return {
        "generated_at": datetime.now(SAST).isoformat(timespec="minutes"),
        "tenants": rows,
        "counts": {h: sum(1 for r in rows if r["health"] == h) for h in ("red", "amber", "green", "off")},
        "dormant": [r["name"] for r in active if r["dormant"]],
        "missing_migrations": missing,
    }


def _headline(d: Dict[str, Any]) -> str:
    c = d["counts"]
    return (f"{c['red']} need attention, {c['amber']} to watch, {c['green']} fine"
            + (f", {c['off']} suspended" if c["off"] else ""))


def render_text(d: Dict[str, Any]) -> str:
    lines = [f"Vula weekly — {_headline(d)}", ""]
    for r in d["tenants"]:
        mark = {"red": "🔴", "amber": "🟠", "green": "🟢", "off": "⚪"}.get(r["health"], "•")
        lines.append(f"{mark} {r['name']}" + (f" — {'; '.join(r['reasons'])}" if r["reasons"] else ""))
    if d["dormant"]:
        lines += ["", "No messages in 30 days: " + ", ".join(d["dormant"])]
    if d["missing_migrations"]:
        lines += ["", "Missing in production (Master › Health › Copy SQL):"]
        lines += [f"  - {m}" for m in d["missing_migrations"]]
    return "\n".join(lines)


def render_html(d: Dict[str, Any]) -> str:
    colour = {"red": "#B42318", "amber": "#B54708", "green": "#067647", "off": "#667085"}
    rows = "".join(
        '<tr><td style="padding:6px 8px;vertical-align:top;">'
        f'<span style="display:inline-block;width:10px;height:10px;border-radius:50%;'
        f'background:{colour.get(r["health"], "#667085")};"></span></td>'
        f'<td style="padding:6px 8px;font-weight:600;vertical-align:top;">{html.escape(r["name"])}</td>'
        f'<td style="padding:6px 8px;color:#475467;">{html.escape("; ".join(r["reasons"]) or "All good")}</td></tr>'
        for r in d["tenants"])
    extra = ""
    if d["missing_migrations"]:
        items = "".join(f"<li>{html.escape(m)}</li>" for m in d["missing_migrations"])
        extra = ('<p style="margin:20px 0 6px;font-weight:600;">Missing in production '
                 '(Master › Health › Copy SQL)</p><ul style="margin:0;">' + items + "</ul>")
    return ('<div style="font-family:-apple-system,Segoe UI,sans-serif;font-size:14px;color:#101828;">'
            f'<p style="font-size:16px;font-weight:700;margin:0 0 12px;">{html.escape(_headline(d))}</p>'
            f'<table cellpadding="0" cellspacing="0">{rows}</table>{extra}</div>')


def _week(now: datetime) -> str:
    y, w, _ = now.isocalendar()
    return f"{y}-W{w:02d}"


def already_sent(week: str) -> bool:
    try:
        rows = (_client().table("vula_admin_audit").select("id").eq("action", _ACTION)
                .contains("detail", {"week": week}).limit(1).execute().data or [])
        return bool(rows)
    except Exception as exc:
        # Can't tell → don't send: a missed digest is better than a daily flood.
        log.warning("digest: sent-marker check failed (%s) — skipping this round", exc)
        return True


async def send(now: Optional[datetime] = None, force: bool = False) -> Dict[str, Any]:
    """Send this week's digest to settings.team_email. Returns what happened."""
    from config import settings
    now = now or datetime.now(SAST)
    week = _week(now)
    to = (settings.team_email or "").strip()
    if not to:
        return {"sent": False, "reason": "TEAM_EMAIL is not set"}
    if not force and already_sent(week):
        return {"sent": False, "reason": f"already sent for {week}"}
    d = build()
    from vula.api.email import _send
    ok = await _send(to, f"Vula weekly — {_headline(d)}", render_html(d), render_text(d))
    if ok:
        try:
            _client().table("vula_admin_audit").insert({
                "actor_email": "scheduler", "action": _ACTION, "detail": {"week": week, "to": to},
            }).execute()
        except Exception as exc:
            log.warning("digest: sent-marker write failed: %s", exc)
    return {"sent": bool(ok), "week": week, "reason": None if ok else "email send failed"}


def due(now: datetime) -> bool:
    """Monday, 07:00–10:59 SAST."""
    return now.weekday() == 0 and 7 <= now.hour < 11
