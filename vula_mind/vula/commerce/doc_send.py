"""vula/commerce/doc_send.py — send one of the business's filed documents to someone, when asked.

20 Nov 2026 (Ian, DIGG): "it would be nice if Vula can send this [the bank letters] to a client when
asked." Clients and suppliers ask for the business's bank confirmation letter, tax clearance, CIPC
certificate, B-BBEE certificate or a proof of payment; staff found the file and forwarded it by hand.

  find(tenant, query)                 the live documents the words name (one per bank account for a
                                      bank letter), newest first
  recipient(tenant, to)               an email / number as given, else a client (clients.resolve) or a
                                      supplier (name or alias) — never guessed between two
  send(tenant, doc_id, to, …)         a preview first; with confirm, the file goes by email (the
                                      business's own mailbox, else Vula's) or WhatsApp (a document
                                      inside 24 h, else the approved `vula_document_share` template,
                                      else email, else to the person asking so they can forward it)
  is_send_request(text)               a message asking Vula to send/forward one of these

Every figure and word of the reply is built here, never by a model. Each send is kept on the
document (`fields.sent`) and in vula_merchant_audit. A bank statement (a whole account's history)
goes only on an owner's or manager's say; a private document (vula_private_documents) is never in
reach — it isn't in vula_filed_documents.
"""
from __future__ import annotations

import base64
import logging
import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

log = logging.getLogger(__name__)

TEMPLATE = "vula_document_share"
TEMPLATE_BODY = "{{1}} has sent you {{2}}. Reply here if you have any questions."
TEMPLATE_EXAMPLE = ["Sunrise Trading", "our bank confirmation letter"]
EMAIL_MAX_BYTES = 10 * 1024 * 1024
FULL_ONLY = {"Bank Statement"}                       # a whole account's history: owner/manager only

_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[a-z]{2,}$", re.I)

# What a request's words name → the categories it may be filed under, and words its own text must
# have (a "bank letter" is a Compliance Certificate that talks about a bank, not a B-BBEE one).
_KINDS = [
    ("bank letter", re.compile(r"\b(bank (account )?(confirmation )?letters?|proof of (bank|banking)( account| details)?|"
                               r"bank(ing)? (account )?confirmation|account confirmation|banking details)\b", re.I),
     {"Compliance Certificate"}, re.compile(r"\b(bank|fnb|absa|nedbank|capitec|investec)\b", re.I)),
    ("tax clearance", re.compile(r"\b(tax clearance|tax compliance( status)?|tcs|compliance pin|tax pin)\b", re.I),
     {"Tax / SARS"}, re.compile(r"\b(tax clearance|tax compliance|pin)\b", re.I)),
    ("VAT registration", re.compile(r"\bvat (registration|certificate|number letter)\b", re.I),
     {"Tax / SARS"}, re.compile(r"\bvat\b", re.I)),
    ("company registration", re.compile(r"\b(cor ?14\.?3|cipc|company registration|registration certificate|"
                                        r"company reg|certificate of incorporation)\b", re.I),
     {"Company Registration"}, None),
    ("B-BBEE certificate", re.compile(r"\b(b-?bbee|bbbee|bee (certificate|affidavit|cert))\b", re.I),
     {"Compliance Certificate"}, re.compile(r"\b(b-?bbee|bbbee|bee)\b", re.I)),
    ("letter of good standing", re.compile(r"\b(coida|letter of good standing|good standing|compensation fund)\b", re.I),
     {"Compliance Certificate"}, re.compile(r"\b(coida|good standing|compensation)\b", re.I)),
    ("proof of payment", re.compile(r"\b(proof of payment|pop)\b", re.I), {"Proof of Payment"}, None),
]
_SEND = re.compile(r"\b(send|forward|share|email|e-mail|whatsapp|give)\b", re.I)
_DOCWORD = re.compile(r"\b(letter|certificate|cert|clearance|registration|proof|document|doc|pdf|cor ?14|b-?bbee|"
                      r"pop|statement)\b", re.I)


class Refused(Exception):
    """The send can't go ahead; the message says why, in plain words."""


def _client():
    from vula.commerce import service
    return service._client()


def _digits(s: str) -> str:
    return re.sub(r"\D", "", s or "")


def _wa_phone(s: str) -> str:
    d = _digits(s)
    return "27" + d[1:] if d.startswith("0") else d


def is_send_request(text: str) -> bool:
    """A message asking Vula to send or forward one of the business's documents to someone."""
    t = text or ""
    if not _SEND.search(t):
        return False
    return any(rx.search(t) for _k, rx, _c, _w in _KINDS) or bool(
        re.search(r"\b(send|forward|share)\b[^.?!\n]{0,40}\b(our|the|my)\b[^.?!\n]{0,30}" + _DOCWORD.pattern, t, re.I))


def _account_of(doc: Dict[str, Any]) -> str:
    d = _digits(str((doc.get("fields") or {}).get("account_number") or ""))
    return d[-4:] if len(d) >= 4 else ""


def _date_of(doc: Dict[str, Any]) -> str:
    f = doc.get("fields") or {}
    return str(f.get("date") or f.get("document_date") or doc.get("created_at") or "")[:10]


def label(doc: Dict[str, Any]) -> str:
    """How the document is named to people: what it is, the bank account's last 4, its date."""
    cat = doc.get("category") or "Document"
    summary = (doc.get("summary") or "").lower()
    if "bank" in summary and "confirm" in summary:
        name = "bank confirmation letter"
    elif "tax clearance" in summary or "tax compliance" in summary:
        name = "tax clearance certificate"
    elif cat == "Company Registration" or "cipc" in summary or "registration certificate" in summary:
        name = "company registration certificate"
    elif cat == "Proof of Payment":
        name = "proof of payment"
    else:
        name = cat.lower() if cat not in ("General Document", "Correspondence") else (doc.get("filename") or "document")
    acct = _account_of(doc)
    return f"{name} (account …{acct})" if acct else name


def _shown(doc: Dict[str, Any]) -> Dict[str, Any]:
    return {"id": doc["id"], "document": label(doc), "filename": doc.get("filename"), "category": doc.get("category"),
            "date": _date_of(doc), "account": _account_of(doc) or None}


def _rows(tenant_id: str, categories: List[str]) -> List[Dict[str, Any]]:
    from vula.commerce.doc_audit import DOC_COLS, _pages
    return _pages(lambda: _client().table("vula_filed_documents").select(DOC_COLS)
                  .eq("tenant_id", tenant_id).in_("category", categories).order("id"))


def _sendable(doc: Dict[str, Any]) -> bool:
    from vula.commerce.doc_audit import live
    f = doc.get("fields") or {}
    return live(doc) and bool(doc.get("file_url")) and not f.get("_internal") and not f.get("_hidden_from_client")


def _newest_first(docs: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    seen, out = set(), []
    for d in sorted(docs, key=lambda d: (_date_of(d), str(d.get("created_at") or "")), reverse=True):
        key = d.get("content_hash") or d["id"]
        if key in seen:
            continue
        seen.add(key)
        out.append(d)
    return out


def find(tenant_id: str, query: str) -> Dict[str, Any]:
    """The documents the words name. {"documents": [...]} newest first — for a bank letter, the newest
    per account, and only the account asked for when the request names its last 4 digits."""
    from vula.commerce import company_docs
    q = query or ""
    for kind, rx, cats, must in _KINDS:
        if not rx.search(q):
            continue
        rows = _rows(tenant_id, sorted(cats | set(company_docs.LOOSE)))
        hits = []
        for d in rows:
            if not _sendable(d):
                continue
            text = f"{d.get('filename') or ''} {d.get('summary') or ''}"
            filed_so = d.get("category") in cats
            reads_so = company_docs.kind_of(d.get("filename"), d.get("summary")) in cats
            if not (filed_so or reads_so):
                continue
            if must is not None and not must.search(text):
                continue
            if cats & set(company_docs.CATEGORIES) and not company_docs.names_us(
                    tenant_id, d.get("filename"), d.get("summary")):
                continue                         # a supplier's bank letter or B-BBEE is theirs, not ours
            hits.append(d)
        hits = _newest_first(hits)
        if kind == "bank letter":
            per: Dict[str, Dict[str, Any]] = {}
            for d in hits:
                per.setdefault(_account_of(d) or d["id"], d)
            wanted = [a for a in re.findall(r"\b(\d{4})\b", q) if a in per]
            hits = [d for a, d in per.items() if not wanted or a in wanted]
        if kind == "proof of payment" and hits:
            break                                # a proof of payment is found by its amount/payee below
        return {"kind": kind, "documents": [_shown(d) for d in hits]}
    return {"kind": None, "documents": []}


async def find_any(tenant_id: str, query: str) -> Dict[str, Any]:
    """`find`, else the general document search (find_filed_document) for anything else asked for by
    name, number, amount or party."""
    out = find(tenant_id, query)
    if out["documents"]:
        return out
    from vula.commerce import service
    res = await service.find_filed_document(tenant_id, query, limit=5)
    ids = [m.get("id") for m in (res.get("matches") or []) if m.get("id")]
    if not ids:
        return {"kind": out.get("kind"), "documents": []}
    from vula.commerce.doc_audit import DOC_COLS
    rows = (_client().table("vula_filed_documents").select(DOC_COLS).eq("tenant_id", tenant_id)
            .in_("id", ids).execute().data or [])
    by = {r["id"]: r for r in rows if _sendable(r)}
    return {"kind": out.get("kind"), "documents": [_shown(by[i]) for i in ids if i in by]}


# ── who it goes to ──────────────────────────────────────────────────────────────────────────────

def recipient(tenant_id: str, to: str) -> Dict[str, Any]:
    """{"name", "email", "phone"} for an email or number as given, a client, or a supplier;
    {"ambiguous": [labels]} when a name fits several; {"none": True} when nothing does."""
    t = (to or "").strip()
    if not t:
        return {"none": True}
    if _EMAIL.match(t):
        return {"name": "", "email": t.lower(), "phone": ""}
    if len(_digits(t)) >= 9 and not re.search(r"[a-z]{3,}", t, re.I):
        return {"name": "", "email": "", "phone": _wa_phone(t)}
    found: List[Dict[str, Any]] = []
    try:
        from vula import clients
        r = clients.resolve(tenant_id, t)
        if r.get("ambiguous"):
            return {"ambiguous": r["ambiguous"]}
        c = r.get("client")
        if c is not None:
            found.append({"name": c.name or c.company or t, "email": (c.emails[0] if c.emails else ""),
                          "phone": c.key})
    except Exception as exc:  # noqa: BLE001 — a client lookup failing still lets a supplier be found
        log.debug("client lookup skipped: %s", exc)
    try:
        sups = (_client().table("commerce_suppliers").select("name,aliases,contact_email,contact_phone")
                .eq("tenant_id", tenant_id).execute().data or [])
    except Exception:  # noqa: BLE001
        sups = []
    want = t.lower()
    for s in sups:
        names = [s.get("name") or ""] + list(s.get("aliases") or [])
        if any(n and (n.lower() == want or want in n.lower() or (len(n) > 3 and n.lower() in want)) for n in names):
            found.append({"name": s.get("name") or t, "email": (s.get("contact_email") or "").strip().lower(),
                          "phone": _wa_phone(s.get("contact_phone") or "") if s.get("contact_phone") else ""})
    uniq = {(f["email"] or f["phone"] or f["name"]).lower(): f for f in found}
    if len(uniq) > 1:
        return {"ambiguous": [f"{f['name']} ({f['email'] or ('…' + f['phone'][-4:] if f['phone'] else 'no contact')})"
                              for f in uniq.values()]}
    if uniq:
        return next(iter(uniq.values()))
    return {"none": True}


def _business_name(tenant_id: str) -> str:
    try:
        from vula.commerce import bank_rec
        names = [n for n in bank_rec._own_names_now(tenant_id) if n and n != tenant_id]
        return names[0] if names else tenant_id
    except Exception:  # noqa: BLE001
        return tenant_id


def _doc(tenant_id: str, doc_id: str) -> Dict[str, Any]:
    from vula.commerce.doc_audit import DOC_COLS
    rows = (_client().table("vula_filed_documents").select(DOC_COLS).eq("tenant_id", tenant_id)
            .eq("id", doc_id).limit(1).execute().data or [])
    if not rows:
        raise Refused("That document isn't on file.")
    return rows[0]


def _say_to(r: Dict[str, Any], via: str) -> str:
    addr = r.get("email") if via == "email" else ("+" + r["phone"] if r.get("phone") else "")
    return f"{r['name']} ({addr})" if r.get("name") else addr


# ── sending ─────────────────────────────────────────────────────────────────────────────────────

async def _by_email(tenant_id: str, to: str, doc: Dict[str, Any], data: bytes, business: str, greet: str,
                    note: str) -> Optional[str]:
    """Through the business's own mailbox, else Vula's (Resend). Returns which, or None."""
    name = label(doc)
    subject = f"{name[0].upper() + name[1:]} – {business}"
    body = (f"Hi {greet},\n\nPlease find attached our {name}.\n\n" + (f"{note.strip()}\n\n" if note else "")
            + f"Kind regards\n{business}")
    mime = doc.get("mime") or "application/pdf"
    filename = doc.get("filename") or "document.pdf"
    from vula.commerce import mail_router
    if await mail_router.send_tenant_email(tenant_id, to, subject, body,
                                           attachments=[{"filename": filename, "content": data, "mimetype": mime}]):
        return "your mailbox"
    from vula.api import email as vula_email
    html = "<p>" + body.replace("\n\n", "</p><p>").replace("\n", "<br>") + "</p>"
    if await vula_email._send(to, subject, html, body,
                              attachments=[{"filename": filename, "content": base64.b64encode(data).decode()}]):
        return "Vula's email"
    return None


async def _by_whatsapp(tenant_id: str, phone: str, doc: Dict[str, Any], data: bytes, business: str,
                       note: str) -> Optional[str]:
    """A document message (inside the 24-hour window), else the approved template with the file."""
    from vula.api import whatsapp
    name = label(doc)
    filename = doc.get("filename") or "document.pdf"
    caption = f"{business}: our {name}." + (f" {note.strip()}" if note else "")
    if await whatsapp._send_invoice_document(phone, data, filename, caption=caption, tenant_id=tenant_id,
                                             content_type=doc.get("mime") or "application/pdf"):
        return "WhatsApp"
    try:
        from vula.storage_links import SHARE_TTL, signed
        link = signed(doc["file_url"], SHARE_TTL)
    except Exception:  # noqa: BLE001
        link = None
    if link and await whatsapp.send_template_message(tenant_id, phone, TEMPLATE, [business, f"our {name}"],
                                                     header_document={"link": link, "filename": filename}):
        return "WhatsApp (message template)"
    return None


async def send(tenant_id: str, doc_id: str, to: str, *, channel: Optional[str] = None, note: str = "",
               confirm: bool = False, actor: Optional[Dict[str, Any]] = None, full_access: bool = True,
               requester_phone: str = "") -> Dict[str, Any]:
    """Preview, or (confirm) send. Returns {"preview": True, …} | {"sent": True, …} |
    {"status": "need_info", "message"} ; raises Refused with a plain reason."""
    doc = _doc(tenant_id, doc_id)
    if not _sendable(doc):
        raise Refused("That document can't be sent — it's set aside, kept internal, or its file is missing.")
    if doc.get("category") in FULL_ONLY and not full_access:
        raise Refused("A bank statement shows the whole account — only an owner or manager can send one.")
    r = recipient(tenant_id, to)
    if r.get("ambiguous"):
        return {"status": "need_info", "message": "Which one do you mean? " + "; ".join(r["ambiguous"])}
    if r.get("none"):
        return {"status": "need_info",
                "message": f"I couldn't find {to.strip() or 'who to send it to'}. What's their email address "
                           "or WhatsApp number?"}
    ch = (channel or "").lower().replace("-", "")
    if ch in ("mail", "e-mail"):
        ch = "email"
    if ch not in ("email", "whatsapp"):
        ch = "email" if r.get("email") else "whatsapp"
    if ch == "email" and not r.get("email"):
        return {"status": "need_info", "message": f"I don't have an email address for {r['name'] or to}. "
                                                  "What is it — or shall I send it on WhatsApp?"}
    if ch == "whatsapp" and not r.get("phone"):
        return {"status": "need_info", "message": f"I don't have a WhatsApp number for {r['name'] or to}. "
                                                  "What is it — or shall I email it?"}
    via = "email" if ch == "email" else "WhatsApp"
    shown = _shown(doc)
    if not confirm:
        return {"preview": True, "document": shown["document"], "filename": doc.get("filename"),
                "to": _say_to(r, ch), "via": via, "note": note or None,
                "message": f"I'll send our {shown['document']} ({doc.get('filename')}) to {_say_to(r, ch)} by {via}."}

    if ch == "whatsapp":
        from vula import consent
        if consent.opted_out(tenant_id, r["phone"]):
            raise Refused(f"I can't WhatsApp {r['name'] or 'them'}: they've opted out of your messages (or that "
                          "couldn't be checked just now) — " + ("email it instead." if r.get("email") else
                                                                 "send it another way."))
    from vula.storage_links import fetch
    data = await fetch(doc["file_url"])
    if not data:
        raise Refused("I couldn't open that document's file just now — nothing was sent.")
    business = _business_name(tenant_id)
    greet = (r.get("name") or "").split(" ")[0] or "there"
    how, note_back = None, ""
    if ch == "email":
        if len(data) > EMAIL_MAX_BYTES:
            raise Refused("That file is over 10 MB, too big to email — send it on WhatsApp instead.")
        how = await _by_email(tenant_id, r["email"], doc, data, business, greet, note)
    else:
        how = await _by_whatsapp(tenant_id, r["phone"], doc, data, business, note)
        if not how and r.get("email") and len(data) <= EMAIL_MAX_BYTES:
            how = await _by_email(tenant_id, r["email"], doc, data, business, greet, note)
            if how:
                ch, via = "email", "email"
                note_back = " WhatsApp couldn't take it (they haven't messaged in the last 24 hours), so I emailed it."
    if not how:
        if requester_phone:
            from vula.api import whatsapp
            if await whatsapp._send_invoice_document(
                    requester_phone, data, doc.get("filename") or "document.pdf",
                    caption=f"I couldn't send this to {r['name'] or to} — here it is to forward.",
                    tenant_id=tenant_id, content_type=doc.get("mime") or "application/pdf"):
                return {"sent": False, "forward": True, "document": shown["document"],
                        "reply_verbatim": f"I couldn't send it to {_say_to(r, ch)} by {via}"
                                          + (" — they haven't messaged you in the last 24 hours" if ch == "whatsapp" else "")
                                          + ". I've sent you the file here so you can forward it."}
        raise Refused(f"I couldn't send it to {_say_to(r, ch)} by {via} — nothing was sent. "
                      + ("They haven't messaged you in the last 24 hours; email it instead."
                         if ch == "whatsapp" else "Your mailbox isn't connected and Vula's email didn't go through."))

    # kept on the document and in the audit, read back
    from vula.commerce.doc_audit import _save_fields
    f = dict(doc.get("fields") or {})
    entry = {"to": r.get("email") if ch == "email" else r.get("phone"), "name": r.get("name") or None,
             "channel": ch, "how": how, "at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
             "by": (actor or {}).get("email") or (actor or {}).get("name") or (actor or {}).get("phone") or "owner"}
    f["sent"] = (list(f.get("sent") or []) + [entry])[-50:]
    back = _save_fields(tenant_id, doc_id, f)
    kept = any(s.get("at") == entry["at"] for s in ((back.get("fields") or {}).get("sent") or []))
    try:
        from vula.api.merchant_audit import audit
        audit(tenant_id, actor, "document_sent", doc_id=doc_id, document=shown["document"], channel=ch, how=how,
              to=entry["to"])
    except Exception as exc:  # noqa: BLE001
        log.warning("document send audit failed for %s: %s", tenant_id, exc)
    return {"sent": True, "document": shown["document"], "to": _say_to(r, ch), "via": via, "how": how,
            "recorded": kept,
            "reply_verbatim": f"Sent our {shown['document']} to {_say_to(r, ch)} by {via}"
                              + (f" (from {how})" if ch == "email" else "") + "." + note_back}


def sends(doc: Dict[str, Any]) -> List[Dict[str, Any]]:
    return list(((doc.get("fields") or {}).get("sent") or []))
