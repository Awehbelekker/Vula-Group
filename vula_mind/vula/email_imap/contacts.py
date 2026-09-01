"""
vula/email_imap/contacts.py — resolve a person's NAME to a real email address.

email_draft takes `to` as a raw string and there was no way to look anyone up, even though
vula/email_imap/sync.py has been building vula_email_contacts from every real message all along
(333 contacts across digg-demo and off-the-hook as of 2026-09-01, with names, domains and
message counts). So "email Jack about the invoice" — the natural thing to say in a WhatsApp
voice note — left the model to invent an address.

Two real hazards visible in that live data, both of which this module is shaped around:

  Staci Brits   -> messaging-service@post.xero.com     a PERSON's name on a robot's address
  Judy Downing  -> judy@digg-ct.co.za    (1376 msgs)   the same name on two addresses
  Judy Downing  -> judydowning0@gmail.com  (67 msgs)

So this never auto-picks a winner. It returns ranked candidates and lets the caller confirm with
a human, because "most messages" is not the same as "the one they meant".
"""
from __future__ import annotations

import logging
import re
from typing import Any, Dict, List

log = logging.getLogger(__name__)

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

# Addresses that belong to a system, not a person, even when a person's name is attached —
# emailing one of these instead of the human is the specific failure this guards against.
_ROBOT_HINTS = re.compile(
    r"(no[-_.]?reply|do[-_.]?not[-_.]?reply|notification|notifications|mailer|postmaster|"
    r"messaging-service|automated|bounce|support@|billing@|invoice@|@post\.|@mail\.|"
    r"@email\.|@notify\.)",
    re.IGNORECASE,
)


def looks_like_email(value: str) -> bool:
    return bool(_EMAIL_RE.match((value or "").strip()))


def is_probably_automated(email: str) -> bool:
    return bool(_ROBOT_HINTS.search((email or "").lower()))


def _client():
    from vula.commerce import service
    return service._client()


def _score(row: Dict[str, Any], q: str) -> int:
    """Higher is a better match. Deliberately simple and explainable — the caller confirms
    with a human anyway, so cleverness here would only hide ambiguity."""
    name = (row.get("name") or "").lower()
    email = (row.get("email") or "").lower()
    domain = (row.get("domain") or "").lower()
    if email == q:
        return 100
    score = 0
    if name == q:
        score = 90
    elif name.startswith(q) or any(p.startswith(q) for p in name.split()):
        score = 70
    elif q in name:
        score = 55
    elif email.startswith(q):
        score = 50
    elif q in email:
        score = 35
    elif q in domain:
        score = 25
    if not score:
        return 0
    # A busy correspondent is a likelier match than a one-off, but never enough to override a
    # better textual match — hence the small weight.
    score += min(int(row.get("message_count") or 0), 200) // 40
    if is_probably_automated(email):
        score -= 30   # a robot address should rarely be the answer to "email <person>"
    return max(score, 1)


def search_contacts(tenant_id: str, query: str, limit: int = 5) -> List[Dict[str, Any]]:
    """Ranked candidate contacts for a name/partial email/company. Never guesses a winner."""
    q = (query or "").strip().lower()
    if not q or not tenant_id:
        return []
    try:
        rows = (_client().table("vula_email_contacts")
                .select("name,email,domain,kind,message_count,last_seen")
                .eq("tenant_id", tenant_id).limit(2000).execute().data or [])
    except Exception as exc:
        log.debug("contact search skipped (run migration 024?): %s", exc)
        return []
    scored = []
    for r in rows:
        s = _score(r, q)
        if s > 0:
            scored.append((s, r))
    scored.sort(key=lambda x: (-x[0], -(x[1].get("message_count") or 0)))
    out = []
    for s, r in scored[:limit]:
        out.append({
            "name": r.get("name"),
            "email": r.get("email"),
            "company": r.get("domain"),
            "kind": r.get("kind"),
            "emails_exchanged": r.get("message_count"),
            "last_seen": r.get("last_seen"),
            "looks_automated": is_probably_automated(r.get("email") or ""),
            "match_score": s,
        })
    return out
