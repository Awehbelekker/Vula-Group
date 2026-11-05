"""vula/connections.py — every business's app connections, and what to do when one breaks.

5 Nov 2026 (Ian: "we need to make it simple for Vula to connect to 3rd party apps like ClickUp …
avoid tech support"). Connections broke without anyone hearing about it: a mailbox whose saved
password couldn't be opened for two weeks, a ClickUp link dead since August, a sign-in button
that opened a window that never came back. So, for each business:

  state(tenant)   each connection as working | broken | not_connected | unavailable, with the
                  problem in plain words, since when, and what fixes it
  check_all()     the daily check: remembers each state (migration 245) and tells the owner on
                  WhatsApp once when something breaks (one message, a link to reconnect), again
                  after a week if it's still broken, and once when it works again
  one_tap(p)      whether Vula's own app for a provider is set up, so its Connect button can work

Only names and states are compared; tokens are never read beyond "can it be unlocked".
"""
from __future__ import annotations

import hashlib
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from config import settings

log = logging.getLogger(__name__)

TABLE = "vula_connection_health"
REMIND_AFTER = timedelta(days=7)

LABELS = {"clickup": "ClickUp", "google": "Google Drive", "microsoft": "Microsoft 365",
          "dynamics365": "Dynamics 365", "xero": "Xero", "email": "Mailbox", "whatsapp": "WhatsApp number"}
# Providers that sign in through Vula's own registered app (one tap), and the settings it needs.
_ONE_TAP = {"clickup": ("clickup_client_id", "clickup_client_secret"),
            "google": ("google_client_id", "google_client_secret"),
            "microsoft": ("microsoft_client_id", "microsoft_client_secret"),
            "dynamics365": ("microsoft_client_id", "microsoft_client_secret"),
            "xero": ("xero_client_id", "xero_client_secret")}


def _client():
    from vula.commerce.service import _client as c
    return c()


def _now() -> datetime:
    return datetime.now(timezone.utc)


def one_tap(provider: str) -> bool:
    """Vula's own app for this provider is set up (its Connect button can finish)."""
    need = _ONE_TAP.get(provider)
    return bool(need) and all((getattr(settings, n, "") or "").strip() for n in need)


def unavailable_message(provider: str) -> str:
    return (f"{LABELS.get(provider, provider)} can't be connected with one tap yet — Vula's "
            f"{LABELS.get(provider, provider)} app isn't set up. Vula's team has been told.")


def _locked(value: Optional[str]) -> bool:
    try:
        from vula.email_imap.credentials import cannot_unlock
        return cannot_unlock(value)
    except Exception:  # noqa: BLE001
        return False


def _rows(table: str, cols: str, tenant_id: str) -> List[Dict[str, Any]]:
    try:
        return _client().table(table).select(cols).eq("tenant_id", tenant_id).execute().data or []
    except Exception as exc:  # noqa: BLE001 — a table a deployment hasn't got yet: nothing to show
        log.debug("connections: %s read skipped: %s", table, exc)
        return []


def _item(provider: str, state: str, account: str = "", problem: Optional[str] = None,
          since: Optional[str] = None, fix: Optional[str] = None) -> Dict[str, Any]:
    return {"provider": provider, "label": LABELS.get(provider, provider), "account": account or "",
            "state": state, "problem": problem, "since": since, "fix": fix,
            "one_tap": one_tap(provider) if provider in _ONE_TAP else True}


_RECONNECT = "Settings › Connections: connect it again"


def _oauth_state(provider: str, row: Optional[Dict[str, Any]], token_cols: tuple) -> Dict[str, Any]:
    """Google / Microsoft / Dynamics: an OAuth sign-in Vula keeps (and refreshes)."""
    if not row or (row.get("status") or "") in ("disconnected", "revoked", ""):
        if provider in _ONE_TAP and not one_tap(provider):
            return _item(provider, "unavailable", problem=unavailable_message(provider))
        return _item(provider, "not_connected")
    account = row.get("email") or row.get("org_url") or row.get("org_name") or ""
    if any(_locked(row.get(c)) for c in token_cols):
        return _item(provider, "broken", account, "the saved sign-in can't be unlocked (Vula's encryption key changed)",
                     fix=_RECONNECT)
    status = (row.get("status") or "").lower()
    if status in ("error", "needs_reconnect", "expired"):
        return _item(provider, "broken", account, row.get("last_sync_error") or "the sign-in stopped working",
                     fix=_RECONNECT)
    if (row.get("last_sync_status") or "") == "error":
        return _item(provider, "broken", account, row.get("last_sync_error") or "syncing stopped working",
                     since=row.get("last_synced_at"), fix=_RECONNECT)
    return _item(provider, "working", account)


def _clickup(tenant_id: str) -> Dict[str, Any]:
    rows = _rows("vula_clickup_accounts", "status,api_token,last_error,last_synced_at,last_sync_status,last_sync_error",
                 tenant_id)
    row = rows[0] if rows else None
    if not row or (row.get("status") or "") in ("disconnected", ""):
        return _item("clickup", "not_connected")
    if _locked(row.get("api_token")):
        return _item("clickup", "broken", problem="the saved ClickUp sign-in can't be unlocked (Vula's encryption key changed)",
                     fix="Settings › Connections › ClickUp: connect again")
    if row.get("status") in ("needs_reconnect", "error") or row.get("last_sync_status") == "error":
        return _item("clickup", "broken", problem=row.get("last_sync_error") or row.get("last_error") or "ClickUp stopped accepting Vula's sign-in",
                     since=row.get("last_synced_at"), fix="Settings › Connections › ClickUp: connect again")
    return _item("clickup", "working")


def _mailboxes(tenant_id: str) -> List[Dict[str, Any]]:
    from vula.readiness import mailbox_problem
    out = []
    for r in _rows("vula_email_accounts", "email,status,secret,sync_fail_count,last_error,last_sync_at", tenant_id):
        if r.get("status") == "disconnected":
            continue
        if _locked(r.get("secret")):
            out.append(_item("email", "broken", r.get("email"), mailbox_problem("can't be unlocked"),
                             since=r.get("last_sync_at"), fix="Settings › Connections › Email: Connect again, with its current password"))
        elif r.get("status") == "error" or int(r.get("sync_fail_count") or 0) >= 3:
            out.append(_item("email", "broken", r.get("email"), mailbox_problem(r.get("last_error")),
                             since=r.get("last_sync_at"), fix="Settings › Connections › Email: Connect again, with its current password"))
        else:
            out.append(_item("email", "working", r.get("email")))
    return out


def _whatsapp(tenant_id: str) -> List[Dict[str, Any]]:
    out = []
    now = _now().isoformat()
    for r in _rows("vula_whatsapp_accounts", "phone_number,status,access_token,token_expires_at,last_error", tenant_id):
        num = r.get("phone_number") or ""
        if r.get("status") in ("disconnected", "pending"):
            continue
        if _locked(r.get("access_token")):
            out.append(_item("whatsapp", "broken", num, "the saved WhatsApp access can't be unlocked (Vula's encryption key changed)",
                             fix="Settings › WhatsApp: connect the number again"))
        elif r.get("token_expires_at") and str(r["token_expires_at"]) < now:
            out.append(_item("whatsapp", "broken", num, "Meta's access for this number has expired",
                             since=r.get("token_expires_at"), fix="Settings › WhatsApp: connect the number again"))
        elif r.get("status") == "error":
            out.append(_item("whatsapp", "broken", num, r.get("last_error") or "Meta refused this number's access",
                             fix="Settings › WhatsApp: connect the number again"))
        else:
            out.append(_item("whatsapp", "working", num))
    return out


def state(tenant_id: str) -> List[Dict[str, Any]]:
    """Every connection this business has (or could have), with its state."""
    one = lambda t, cols: (_rows(t, cols, tenant_id) or [None])[0]  # noqa: E731
    items = [_clickup(tenant_id),
             _oauth_state("google", one("vula_google_accounts", "email,status,refresh_token"), ("refresh_token",)),
             _oauth_state("microsoft", one("vula_microsoft_accounts",
                                           "email,status,refresh_token,last_synced_at,last_sync_status,last_sync_error"),
                          ("refresh_token",)),
             _oauth_state("dynamics365", one("vula_dynamics365_accounts", "org_url,email,status,refresh_token"),
                          ("refresh_token",)),
             _oauth_state("xero", one("vula_xero_accounts", "org_name,status,refresh_token,last_error"),
                          ("refresh_token",))]
    return _mailboxes(tenant_id) + _whatsapp(tenant_id) + items


# ── the daily check ────────────────────────────────────────────────────────────────────────

def _key(c: Dict[str, Any]) -> tuple:
    return (c["provider"], c.get("account") or "")


def _short_date(iso: Optional[str]) -> str:
    try:
        return datetime.fromisoformat(str(iso).replace("Z", "+00:00")).strftime("%-d %b")
    except (TypeError, ValueError):
        return ""


def _line(c: Dict[str, Any], since: Optional[str]) -> str:
    what = c["label"] + (f" {c['account']}" if c.get("account") else "")
    when = _short_date(since)
    return f"• {what} — {c.get('problem') or 'stopped working'}" + (f" (since {when})" if when else "")


def _owner_phones(tenant_id: str) -> List[str]:
    """Who hears about a broken connection: the owners, else the admins / managers."""
    from vula.integrations.notify import _members, _digits
    members = [m for m in _members(tenant_id) if m.get("whatsapp")]
    for roles in (("owner",), ("admin", "manager")):
        phones = [_digits(m["whatsapp"]) for m in members if (m.get("role") or "").lower() in roles]
        if phones:
            return list(dict.fromkeys(phones))
    return []


def alert_text(tenant_id: str, broke: List[tuple], fixed: List[Dict[str, Any]]) -> Optional[str]:
    """One WhatsApp message for this check: what broke (with the reconnect link) and what works
    again. `broke` is [(connection, since)]."""
    if not broke and not fixed:
        return None
    from vula.api.tenants import dashboard_link
    parts = []
    if broke:
        n = len(broke)
        parts.append(f"⚠️ {n} of your Vula connection{'s' if n != 1 else ''} stopped working:\n"
                     + "\n".join(_line(c, s) for c, s in broke)
                     + "\n\nNothing new comes in from it until it's connected again. It takes a minute: "
                     + dashboard_link(tenant_id, "settings"))
    if fixed:
        parts.append("✅ Working again: " + ", ".join(
            c["label"] + (f" {c['account']}" if c.get("account") else "") for c in fixed) + ".")
    return "\n\n".join(parts)


async def check_tenant(tenant_id: str) -> Dict[str, Any]:
    """Work out this business's connections, remember them, and tell the owner what changed."""
    db = _client()
    now = _now()
    current = state(tenant_id)
    stored = {(r["provider"], r.get("account_key") or ""): r
              for r in (db.table(TABLE).select("*").eq("tenant_id", tenant_id).execute().data or [])}
    broke, fixed, remind_ids = [], [], []
    for c in current:
        prev = stored.get(_key(c))
        changed = not prev or prev.get("state") != c["state"]
        since = (prev.get("since") if prev and not changed else None) or c.get("since") or now.isoformat()
        row = {"tenant_id": tenant_id, "provider": c["provider"], "account_key": c.get("account") or "",
               "state": c["state"], "problem": (c.get("problem") or "")[:300] or None,
               "since": since, "last_checked_at": now.isoformat()}
        if changed:
            row["alerted_at"] = None
        db.table(TABLE).upsert(row, on_conflict="tenant_id,provider,account_key").execute()
        if c["state"] == "broken":
            last = (prev or {}).get("alerted_at") if not changed else None
            if not last:
                broke.append((c, since))
            elif datetime.fromisoformat(str(last).replace("Z", "+00:00")) < now - REMIND_AFTER:
                broke.append((c, since))
                remind_ids.append(_key(c))
        elif c["state"] == "working" and prev and prev.get("state") == "broken":
            fixed.append(c)
    text = alert_text(tenant_id, broke, fixed)
    sent = 0
    if text:
        from vula.api.whatsapp import _send_reply
        digest = hashlib.sha256(text.encode()).hexdigest()[:12]
        for phone in _owner_phones(tenant_id):
            try:
                if await _send_reply(phone, text, tenant_id=tenant_id,
                                     idem_key=f"conn:{tenant_id}:{now.date()}:{digest}:{phone}"):
                    sent += 1
            except Exception as exc:  # noqa: BLE001
                log.warning("connection alert to %s failed: %s", tenant_id, exc)
        if sent:
            for c, _since in broke:
                db.table(TABLE).update({"alerted_at": now.isoformat()}).eq("tenant_id", tenant_id) \
                    .eq("provider", c["provider"]).eq("account_key", c.get("account") or "").execute()
    return {"tenant_id": tenant_id, "broken": [c for c in current if c["state"] == "broken"],
            "told": sent, "newly_broken": len(broke) - len(remind_ids), "fixed": len(fixed)}


async def check_all() -> Dict[str, Any]:
    """The daily check across every active business."""
    from vula.api import tenants as _t
    try:
        tids = sorted({r["tenant_id"] for r in (_client().table("vula_tenant_config").select("tenant_id")
                                                .execute().data or []) if r.get("tenant_id")})
    except Exception as exc:  # noqa: BLE001
        log.warning("connection check: tenant list failed: %s", exc)
        return {"tenants": 0}
    out = {"tenants": 0, "broken": 0, "told": 0}
    for tid in tids:
        if not _t.is_active(tid):
            continue
        try:
            res = await check_tenant(tid)
        except Exception as exc:  # noqa: BLE001 — one business's failure never stops the rest
            log.warning("connection check for %s failed: %s", tid, exc)
            continue
        out["tenants"] += 1
        out["broken"] += len(res["broken"])
        out["told"] += res["told"]
    return out


def overview() -> List[Dict[str, Any]]:
    """Master panel: every business's connections as last checked (no tokens, no secrets)."""
    rows = (_client().table(TABLE).select("tenant_id,provider,account_key,state,problem,since,last_checked_at,alerted_at")
            .order("tenant_id").execute().data or [])
    for r in rows:
        r["label"] = LABELS.get(r["provider"], r["provider"])
    return rows


_TOLD: set = set()


async def not_available(provider: str) -> dict:
    """The answer a Connect button gets when Vula's own app for this provider isn't set up: a
    plain message for the business (shown instead of opening a window that goes nowhere), and one
    operator alert per provider per process so it gets set up."""
    msg = unavailable_message(provider)
    if provider not in _TOLD:
        _TOLD.add(provider)
        need = ", ".join(n.upper() for n in _ONE_TAP.get(provider, ()))
        try:
            from vula.integrations.platform_support import alert_operator
            await alert_operator(f"A business tried to connect {LABELS.get(provider, provider)} but Vula's "
                                 f"{LABELS.get(provider, provider)} app isn't set up: set {need} in Railway "
                                 f"(docs/connections-setup.md), then railway up.")
        except Exception as exc:  # noqa: BLE001
            log.debug("operator alert for %s skipped: %s", provider, exc)
    return {"available": False, "error": msg, "message": msg}
