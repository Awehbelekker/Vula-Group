"""
vula/commerce/mail_router.py — one send point for tenant-originated system email (call sheet,
expense sheet, and any future automated send), picking whichever mailbox a tenant actually has
connected.

Real motivating case (2026-08-26): a tenant's corporate mailbox is Microsoft 365 with basic/
legacy IMAP+SMTP auth disabled — Microsoft's default now for most tenants. No password (app
password or otherwise) can authenticate there; only OAuth works. Confirmed live: an IMAP connect
attempt against a real M365 mailbox returned "AUTHENTICATE failed. Provided authentication
mechanism is not supported." — not a wrong-credentials error, a hard auth-method mismatch.

Tries the tenant's connected IMAP/SMTP mailbox first (the common case — most tenants aren't on
a hardened M365 tenant), then a connected Microsoft Graph (Outlook) account with Mail.Send
granted. Shared here so call_sheet.py/expense_sheet.py don't each duplicate the fallback order.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

log = logging.getLogger(__name__)


async def send_tenant_email(tenant_id: str, to: str, subject: str, body: str,
                            attachments: Optional[List[Dict[str, Any]]] = None) -> bool:
    """Best-effort — never raises. Returns True only once a real send actually succeeded via
    one of the two paths; False when neither is connected, or both failed."""
    from vula.email_imap.credentials import get_email_creds
    from vula.email_imap.service import send as imap_send
    creds = get_email_creds(tenant_id)
    if creds:
        try:
            result = await imap_send(creds, to, subject, body, attachments=attachments)
            if result.get("sent"):
                return True
            log.warning("IMAP send failed for %s: %s", tenant_id, result.get("error"))
        except Exception as exc:
            log.warning("IMAP send raised for %s: %s", tenant_id, exc)

    try:
        from vula.microsoft.credentials import get_access_token
        token = await get_access_token(tenant_id)
        if token:
            from vula.microsoft import service as ms_service
            result = await ms_service.send_mail(tenant_id, to, subject, body, attachments=attachments)
            if result.get("sent"):
                return True
            log.warning("Microsoft Graph send failed for %s: %s", tenant_id, result.get("error"))
    except Exception as exc:
        log.warning("Microsoft Graph send raised for %s: %s", tenant_id, exc)

    return False


async def create_tenant_draft(tenant_id: str, to: str, subject: str, body: str) -> Optional[Dict[str, Any]]:
    """The draft-side twin of send_tenant_email — same IMAP-then-Microsoft fallback order,
    but for a REVIEWABLE draft, never an actual send.

    2026-09-15: core/skills/commerce_admin.py's draft_followup_email tool (a meeting follow-up
    to a real customer — exactly the kind of message that should always be reviewed before
    sending, never auto-sent) was hardcoded to Gmail only. A tenant with a working IMAP or
    Microsoft mailbox connected — confirmed the more common case, see this module's own
    docstring — got a dead-end "connect Google" error despite already having mail connected.
    Both backend capabilities already existed independently (vula/email_imap/service.py's
    save_draft, used by core/skills/email_admin.py; vula/microsoft/service.py's
    mail_create_draft, used by core/skills/microsoft_admin.py) — this is the first time either
    is reached from a tenant-agnostic "whichever mailbox they actually have" caller, the same
    role send_tenant_email already plays for sending.

    Returns {"via": "imap"|"microsoft", ...} on success, None if neither is connected or both
    failed — the caller decides what "nothing worked" means for it (this module's send path
    stays a plain bool since nothing downstream needed the richer detail; drafting benefits
    from telling the user which mailbox actually got it)."""
    from vula.email_imap.credentials import get_email_creds
    from vula.email_imap.service import save_draft as imap_save_draft
    creds = get_email_creds(tenant_id)
    if creds:
        try:
            result = await imap_save_draft(creds, to, subject, body)
            if result.get("saved_to"):
                return {"via": "imap", **result}
            log.warning("IMAP draft failed for %s: %s", tenant_id, result.get("error"))
        except Exception as exc:
            log.warning("IMAP draft raised for %s: %s", tenant_id, exc)

    try:
        from vula.microsoft.credentials import get_access_token
        token = await get_access_token(tenant_id)
        if token:
            from vula.microsoft import service as ms_service
            result = await ms_service.mail_create_draft(tenant_id, to, subject, body)
            if result.get("draft_id"):
                return {"via": "microsoft", **result}
            log.warning("Microsoft Graph draft failed for %s: %s", tenant_id, result.get("error"))
    except Exception as exc:
        log.warning("Microsoft Graph draft raised for %s: %s", tenant_id, exc)

    return None
