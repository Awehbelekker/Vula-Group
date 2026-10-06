"""
vula/commerce/payers.py — whose money a proof of payment came from (migration 196).

A bank's payment notice names the paying account's holder ("MR RICHARD D DOWNING", "AWEH BE
LEKKER (PTY) LTD") and sometimes its number. The business's own legal name is always its own
money; any other payer is asked about once ("a DIGG account or your own money?") and remembered,
so a POP from the owner's name on the business account isn't treated as a personal claim
(2026-10-06, STE Scaffolding: "it went through the DIGG account, just paid by me").
"""
from __future__ import annotations

import logging
import re
from typing import Optional

logger = logging.getLogger(__name__)

_TITLES = {"mr", "mrs", "ms", "miss", "dr", "prof", "mnr", "mev"}


def _client():
    from vula.commerce import service
    return service._client()


def name_key(name: Optional[str]) -> str:
    """'MR RICHARD D DOWNING', 'Richard D Downing', 'MRRICHARDDDOWNING' → 'richardddowning'."""
    s = re.sub(r"[^a-z ]", "", (name or "").lower().replace("*", " "))
    words = [w for w in s.split() if w]
    while words and words[0] in _TITLES:
        words = words[1:]
    squashed = "".join(words)
    for t in sorted(_TITLES, key=len, reverse=True):     # titles glued on: 'mrrichard…'
        if len(words) == 1 and squashed.startswith(t) and len(squashed) > len(t) + 4:
            squashed = squashed[len(t):]
            break
    return squashed


def last4(account: Optional[str]) -> Optional[str]:
    digits = re.sub(r"\D", "", account or "")
    return digits[-4:] if len(digits) >= 4 else None


def classify(tenant_id: str, payer: Optional[str], account: Optional[str] = None) -> Optional[dict]:
    """{'owner': 'business'|'personal', 'person': …, 'why': …} or None when unknown."""
    acct = last4(account)
    key = name_key(payer)
    try:
        from vula.integrations.doc_filing import _is_own, _own_names
        if payer and _is_own(payer.strip("* "), _own_names(tenant_id)):
            return {"owner": "business", "why": "the business's own name"}
    except Exception:
        pass
    if not (key or acct):
        return None
    try:
        rows = (_client().table("commerce_payer_accounts").select("*")
                .eq("tenant_id", tenant_id).execute().data or [])
    except Exception as exc:
        logger.debug("payer accounts lookup skipped (run migration 196?): %s", exc)
        return None
    if acct:
        for r in rows:
            if r.get("account_last4") == acct:
                return {"owner": r["owner"], "person": r.get("person"), "why": f"account …{acct}"}
    for r in rows:
        if key and r.get("name_key") == key and not r.get("account_last4"):
            return {"owner": r["owner"], "person": r.get("person"), "why": "payer name"}
    return None


def remember(tenant_id: str, payer: Optional[str], owner: str, account: Optional[str] = None,
             person: Optional[str] = None) -> bool:
    row = {"tenant_id": tenant_id, "name": payer, "name_key": name_key(payer) or None,
           "account_last4": last4(account), "owner": owner, "person": person}
    try:
        _client().table("commerce_payer_accounts").insert(row).execute()
        return True
    except Exception as exc:
        logger.debug("payer account not stored: %s", exc)
        return False


def pop_question(tenant_id: str, fields: dict, business_label: str = "the business") -> tuple:
    """(line, question) for a POP's payer: a statement when the payer is known, else a question
    as (ref, prompt) for the caller to ask now or queue. ("", None) when the POP names no payer."""
    payer = (fields.get("payer") or "").strip("* ").strip()
    account = fields.get("payer_account")
    if not (payer or account):
        return "", None
    found = classify(tenant_id, payer, account)
    if found and found["owner"] == "business":
        return f"🏦 Paid from {business_label}'s account ({payer or 'account …' + (last4(account) or '')}).", None
    if found:
        who = found.get("person") or payer
        return f"👛 Paid from {who}'s own money — it's owed back to them.", None
    ref = f"payer:{name_key(payer) or last4(account)}|{payer}|{last4(account) or ''}"
    return (f"💳 Paid from *{payer or 'account …' + (last4(account) or '')}* — is that a "
            f"*{business_label}* account or *your own* money? I'll remember it.",
            (ref, f"Whose account: {payer}"))


def pop_note(tenant_id: str, phone: str, fields: dict, business_label: str = "the business") -> str:
    """One line for the POP reply: whose money it was, or one question to find out (recorded as
    an open question so the reply is understood)."""
    line, question = pop_question(tenant_id, fields, business_label)
    if question:
        from vula import open_questions
        open_questions.ask(tenant_id, phone, "payer_account", question[0], question[1])
    return line


def answer(tenant_id: str, ref: str, text: str, business_label: str = "") -> Optional[str]:
    """Reply to the payer question. None when `text` doesn't answer it."""
    low = (text or "").strip().lower().rstrip(".!")
    biz_words = {"business", "company", "the business", "company account", "business account"}
    if business_label:
        biz_words |= {business_label.lower(), f"{business_label.lower()} account"}
    own_words = {"own", "my own", "own money", "personal", "mine", "my money", "me", "my account"}
    if low in biz_words or low.startswith(tuple(w + " " for w in biz_words)):
        owner = "business"
    elif low in own_words:
        owner = "personal"
    else:
        return None
    try:
        _, payer, acct = ref.split("|", 2)
    except ValueError:
        return None
    remember(tenant_id, payer or None, owner, acct or None, person=payer if owner == "personal" else None)
    if owner == "business":
        return f"👍 Noted — *{payer}* pays from the {business_label or 'business'} account. I'll recognise it from now on."
    return f"👍 Noted — *{payer}* is personal money, so payments from it are owed back. I'll remember that."
