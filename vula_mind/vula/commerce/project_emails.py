"""vula/commerce/project_emails.py — email the project's client from the business's own mailbox, on record.

16 Nov 2026 (Ian, DIGG): "send him approval so that we can prove he has access". Mail sent from a
personal inbox isn't the business's record, and copy-pasting a link leaves nothing in Vula. Now the
owner writes (or adjusts) the email on the project's Money tab, confirms, and Vula:

  send(tenant, project, to, subject, body, by)   sends it from the business's connected mailbox
                                                 (vula/commerce/mail_router.py), then a copy to that
                                                 mailbox itself, and records both — read back
  sent(tenant, project)                           every email sent to the project's client, newest first

Each email is a row in vula_project_client_emails (migration 286): who sent it, to whom, when, the
subject and the text, and whether it went. Owners and managers only (server.py section rule).
Nothing is sent without the owner's own words in front of them and their confirmation in the dashboard.
"""
from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

log = logging.getLogger(__name__)

TABLE = "vula_project_client_emails"
MAX_TO = 5
MAX_SUBJECT = 200
MAX_BODY = 20_000
_EMAIL = re.compile(r"^[^@\s,;<>]+@[^@\s,;<>]+\.[A-Za-z]{2,}$")


def _client():
    from vula.commerce.service import _client as c
    return c()


def recipients(to: Any) -> List[str]:
    """Clean addresses from a list or a "a@x, b@y" string. ValueError naming any that aren't one."""
    items = to if isinstance(to, (list, tuple)) else re.split(r"[,;\s]+", str(to or ""))
    out, bad = [], []
    for a in (str(x).strip() for x in items):
        if not a:
            continue
        (out if _EMAIL.match(a) else bad).append(a.lower())
    if bad:
        raise ValueError(f"Not an email address: {', '.join(bad)}")
    out = list(dict.fromkeys(out))
    if not out:
        raise ValueError("Say who to send it to.")
    if len(out) > MAX_TO:
        raise ValueError(f"At most {MAX_TO} people per email.")
    return out


def _mailbox(tenant_id: str) -> Optional[str]:
    """The business's own sending address, for the record copy — or None."""
    try:
        from vula.email_imap.credentials import get_email_creds
        creds = get_email_creds(tenant_id) or {}
        addr = str(creds.get("email") or "").strip()
        return addr if _EMAIL.match(addr) else None
    except Exception as exc:  # noqa: BLE001
        log.debug("mailbox address read skipped: %s", exc)
        return None


async def send(tenant_id: str, project: str, to: Any, subject: str, body: str, by: Optional[str] = None) -> Dict[str, Any]:
    """Send the email from the business's mailbox and record it. ValueError for bad input;
    RuntimeError when it didn't go (the failed attempt is recorded too)."""
    from vula.commerce.mail_router import send_tenant_email
    from vula.commerce.service import canonical_project
    to_list = recipients(to)
    subject = (subject or "").strip()
    body = (body or "").strip()
    if not subject:
        raise ValueError("Give the email a subject.")
    if not body:
        raise ValueError("Write the email first.")
    if len(subject) > MAX_SUBJECT or len(body) > MAX_BODY:
        raise ValueError("That email is too long to send from here.")
    name = canonical_project(tenant_id, project) or project
    db = _client()
    row = db.table(TABLE).insert({"tenant_id": tenant_id, "project": name, "recipients": to_list,
                                  "subject": subject, "body": body, "sent_by": by, "status": "sending"}).execute().data[0]
    ok = False
    err = None
    try:
        ok = await send_tenant_email(tenant_id, ", ".join(to_list), subject, body)
        if not ok:
            err = "No mailbox is connected, or it refused the email."
    except Exception as exc:  # noqa: BLE001
        err = str(exc)[:300]
    copy_to = _mailbox(tenant_id) if ok else None
    copied = False
    if copy_to and copy_to not in to_list:
        try:
            copied = await send_tenant_email(
                tenant_id, copy_to, f"Copy: {subject}",
                f"Sent from Vula to {', '.join(to_list)} on "
                f"{datetime.now(timezone.utc).strftime('%d %b %Y %H:%M')} UTC.\n\n{body}")
        except Exception as exc:  # noqa: BLE001 — the client's email went; the copy is a convenience
            log.warning("record copy of a client email not sent: %s", exc)
    patch = {"status": "sent" if ok else "failed", "error": err, "copy_to": copy_to if copied else None,
             "sent_at": datetime.now(timezone.utc).isoformat()}
    db.table(TABLE).update(patch).eq("tenant_id", tenant_id).eq("id", row["id"]).execute()
    back = (db.table(TABLE).select("*").eq("tenant_id", tenant_id).eq("id", row["id"]).limit(1).execute().data or [{}])[0]
    if back.get("status") != patch["status"]:
        raise RuntimeError("The email's record didn't save — check Sent in your mailbox before sending again.")
    if not ok:
        raise RuntimeError(f"The email wasn't sent: {err}")
    return back


def sent(tenant_id: str, project: str) -> List[Dict[str, Any]]:
    """Every email sent to the project's client, newest first."""
    from vula.commerce.service import project_key
    try:
        rows = (_client().table(TABLE).select("*").eq("tenant_id", tenant_id).limit(500).execute().data or [])
    except Exception as exc:  # noqa: BLE001 — before migration 286
        log.debug("client emails unavailable: %s", exc)
        return []
    rows = [r for r in rows if project_key(r.get("project")) == project_key(project)]
    return sorted(rows, key=lambda r: str(r.get("sent_at") or r.get("created_at") or ""), reverse=True)
