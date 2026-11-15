"""
vula/listings/portal_leads.py — property-portal enquiry emails become enquiries (real-estate
add-on, `listings` module; called from vula/email_imap/sync.py for each new inbox email).

Property24 and Private Property send the agent an email when someone enquires about a listing.
Vula already reads the business's connected mailbox, so such an email becomes an enquiry in
Listings › Enquiries and the agent is alerted on WhatsApp straight away (with a wa.me link when
the person left a number) — the same path as a website or WhatsApp enquiry.

Deliberately conservative, because the portals' exact email layouts weren't available when this
was written (2026-11-12):
  - only mail FROM the portals' own domains, and only for businesses with `listings`;
  - only clearly labelled fields are read (Name:, Phone/Cell/Mobile:, Email:, Message:) —
    nothing is guessed; a field that isn't found is left empty;
  - a listing is linked only when its exact title appears in the email; otherwise the enquiry
    is kept as general, with the email's subject;
  - when no contact details can be read (an HTML-only email, an unfamiliar layout), the enquiry
    is still filed under the portal's name and the agent is told to check the email;
  - each email is filed once (its Message-ID is stored as external_ref, migration 259);
  - the person is never messaged automatically: they enquired through the portal, so the agent
    decides how to answer.
"""
from __future__ import annotations

import logging
import re
from typing import Any, Dict, Optional

from vula.listings import service as ls

log = logging.getLogger(__name__)

PORTALS = {"property24.com": "Property24", "privateproperty.co.za": "Private Property"}

_LEADISH = re.compile(r"enquir|inquir|lead|interest|request|viewing|contact|message", re.I)
_FIELD = {
    "name": r"(?:full\s+name|contact\s+name|name)",
    "phone": r"(?:cell(?:phone)?(?:\s+number)?|mobile(?:\s+number)?|phone(?:\s+number)?|contact\s+number|tel(?:ephone)?)",
    "email": r"(?:e-?mail(?:\s+address)?)",
    "message": r"(?:message|comments?|enquiry|inquiry)",
}


def portal_of(sender: str) -> Optional[str]:
    """'Property24 <leads@mail.property24.com>' → 'Property24'; None for anyone else."""
    m = re.search(r"@([A-Za-z0-9.-]+)", sender or "")
    if not m:
        return None
    domain = m.group(1).lower().rstrip(".>")
    for d, name in PORTALS.items():
        if domain == d or domain.endswith("." + d):
            return name
    return None


def _field(body: str, key: str) -> Optional[str]:
    m = re.search(rf"^[ \t*]*{_FIELD[key]}[ \t*]*[:\-][ \t]*(.+)$", body or "", re.I | re.M)
    return re.sub(r"\s+", " ", m.group(1)).strip(" *\t") if m else None


def parse_lead(body: str) -> Dict[str, Optional[str]]:
    """Labelled fields from a lead email's text. Pure; anything not clearly labelled is None."""
    from vula.team_index import digits_za
    name = _field(body, "name")
    phone_raw = _field(body, "phone")
    phone = digits_za(phone_raw) if phone_raw else ""
    email = _field(body, "email")
    if email:
        found = re.search(r"[^@\s<>]+@[^@\s<>]+\.[^@\s<>]+", email)
        email = found.group(0) if found else None
    return {
        "name": name[:80] if name and not re.search(r"@|\d{6,}", name) else None,
        "whatsapp": phone if 9 <= len(phone) <= 15 else None,
        "email": email,
        "message": (_field(body, "message") or "")[:1000] or None,
    }


async def _listing_named_in(tenant_id: str, text: str) -> Optional[Dict[str, Any]]:
    """The one listing whose exact title appears in the email, if exactly one does."""
    low = (text or "").lower()
    rows = await ls.list_properties(tenant_id, statuses=ls.PUBLIC_STATUSES + ls.SOLD_STATUSES, limit=500)
    hits = [p for p in rows if len(p.get("title") or "") >= 8 and p["title"].lower() in low]
    return hits[0] if len(hits) == 1 else None


async def maybe_take(tenant_id: str, em: Dict[str, Any]) -> bool:
    """File this inbox email as an enquiry if it's a portal lead for a listings business.
    True when it was filed now; False otherwise (not a lead, already filed, or not switched on)."""
    portal = portal_of(em.get("from") or "")
    if not portal or em.get("is_sent"):
        return False
    subject = em.get("subject") or ""
    body = em.get("body") or ""
    if not _LEADISH.search(subject) and not _LEADISH.search(body[:500]):
        return False                                     # newsletters, statements, invoices
    try:
        from vula.api.tenants import enabled_modules
        if "listings" not in (enabled_modules(tenant_id) or []):
            return False
    except Exception:
        return False
    ref = (em.get("message_id") or "").strip()[:300] or None
    if ref:
        seen = (ls._client().table(ls.ENQUIRIES).select("id").eq("tenant_id", tenant_id)
                .eq("external_ref", ref).limit(1).execute().data or [])
        if seen:
            return False
    lead = parse_lead(body)
    prop = await _listing_named_in(tenant_id, subject + "\n" + body)
    readable = bool(lead["whatsapp"] or lead["email"])
    note = lead["message"] or subject
    if not readable:
        note = f"{subject} — couldn't read the person's details from the email; please check your inbox."
    await ls.create_enquiry(tenant_id, {
        "name": lead["name"] or f"{portal} enquiry",
        "whatsapp": lead["whatsapp"], "email": lead["email"],
        "message": f"[{portal}] {note}"[:1000],
        "property_slug": prop.get("slug") if prop else None,
        "source": "portal", "external_ref": ref,
    })
    log.info("portal lead filed for %s from %s (contact read: %s, listing linked: %s)",
             tenant_id, portal, readable, bool(prop))
    return True
