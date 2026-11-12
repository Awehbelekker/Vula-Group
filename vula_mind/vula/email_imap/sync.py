"""
vula/email_imap/sync.py — email auto-sync (contacts library + work-attachment filing).

A background loop pulls NEW mail per connected tenant and, work-relevant-only:
  • builds a contact directory (internal co-workers vs external contacts/suppliers),
  • auto-files genuine attachments into the KB + Documents library (project-matched),
    skipping inline signature images and bulk/no-reply senders.
Sync position is the IMAP UID stored on the account row, so nothing is processed twice.
"""
from __future__ import annotations

import email
import logging
import re
from datetime import datetime, timezone
from email.utils import getaddresses, parsedate_to_datetime
from typing import Optional

from vula.email_imap.credentials import get_email_creds, _client
from vula.email_imap.service import _hdr, _imap_login, _is_real_attachment

logger = logging.getLogger(__name__)

import asyncio as _asyncio
_sync_locks: dict = {}

def _lock_for(account_id: str) -> "_asyncio.Lock":
    """One lock per mailbox (not per tenant) — a tenant's several connected accounts sync
    independently rather than serializing behind each other."""
    lk = _sync_locks.get(account_id)
    if lk is None:
        lk = _sync_locks[account_id] = _asyncio.Lock()
    return lk

_NOISE = re.compile(r"(no[-_.]?reply|do[-_.]?not[-_.]?reply|notification|mailer|newsletter|"
                    r"bounce|postmaster|marketing|updates?@|alerts?@)", re.IGNORECASE)
_MAX_ATTACH_BYTES = 15 * 1024 * 1024
# Consecutive failed sync attempts (each ~15 min apart, see server.py's _email_sync_loop)
# before flagging a mailbox as broken — enough to ride out a single transient blip without
# nagging, short enough that a real outage (revoked password, host change) surfaces same-day.
_FAIL_NOTIFY_THRESHOLD = 3
SAVED_PASSWORD_LOCKED = ("the saved password can't be unlocked (it was saved before Vula's encryption key "
                         "changed) - connect the mailbox again")

_SCHEDULE_KW = ("schedule", "meeting", "meet ", "available", "availability", "calendar",
                "appointment", "site visit", "what time", "when can", "set up a call")
_REQUEST_KW = ("please", "could you", "can you", "kindly", "let me know", "would you",
               "need ", "requesting", "request ", "awaiting", "send me", "advise", "confirm",
               "feedback", "approve", "quote", "quotation")
# Request-y phrasing ("please confirm", "kindly advise") is exactly how phishing/scam mail
# reads too — these phrases are specific enough to phishing/promo copy that a genuine business
# follow-up essentially never needs them, so their presence overrides a request-keyword match.
_SPAM_KW = ("unsubscribe", "view in browser", "verify your account", "account has been suspended",
            "account will be suspended", "click here to", "claim your", "you have won",
            "you've won", "congratulations you", "wire transfer", "gift card", "bitcoin",
            "crypto", "inheritance", "lottery", "urgent action required", "act now",
            "risk-free", "% off", "limited time offer", "security alert")


def _needs_reply(em: dict, own_domain: str):
    """Best-guess whether an inbound external email is awaiting a reply. Returns
    (reason, ) or None. Heuristic — cheap, runs on every synced email."""
    frm = (em.get("from") or "").lower()
    if own_domain in frm or _NOISE.search(frm) or em.get("bulk"):
        return None           # our own mail / bulk senders / has a List-Unsubscribe header
    blob = f"{em.get('subject','')} {em.get('body','')}".lower()
    if any(k in blob for k in _SPAM_KW):
        return None            # phishing/promo phrasing, however request-y it reads
    if any(k in blob for k in _SCHEDULE_KW):
        return "schedule"
    if "?" in blob or any(k in blob for k in _REQUEST_KW):
        return "request" if any(k in blob for k in _REQUEST_KW) else "question"
    return None


_SUMMARY_SYSTEM = (
    "You triage a small business inbox. Given an email's subject and body, judge its tone "
    "and urgency and write one skimmable sentence of what the sender wants or said. "
    'Reply with strict JSON only: {"summary": "<one sentence, <=160 chars>", '
    '"urgency": "high"|"normal"|"low", "tone": "<one or two words, e.g. frustrated, friendly, '
    'formal, urgent, casual>"}. urgency=high only for genuine time pressure, anger, or an '
    "explicit deadline/complaint — most business email is normal. Never invent details not in the email."
)


async def _summarize_followup(subject: str, body: str) -> dict:
    """Cheap-tier LLM read of tone/urgency + a one-sentence skim summary. Best-effort —
    only called for emails that already passed _needs_reply, so this runs on a small
    subset of synced mail, not every message."""
    import json as _json
    import litellm
    from core.llm_router import resolve_generation_route
    from core.prompt_safety import UNTRUSTED_CONTENT_RULE, fence

    if not (subject or "").strip() and not (body or "").strip():
        return {}
    text = f"Subject: {subject or ''}\n\n{body or ''}"[:3000]
    litellm.drop_params = True
    try:
        model, api_key, api_base = await resolve_generation_route(task_type="email_summary")
        resp = await litellm.acompletion(
            model=model,
            messages=[{"role": "system", "content": _SUMMARY_SYSTEM + "\n" + UNTRUSTED_CONTENT_RULE},
                      {"role": "user", "content": fence("EMAIL", text)}],
            temperature=0, max_tokens=150, api_key=api_key, api_base=api_base,
        )
        raw = resp.choices[0].message.content or ""
    except Exception as exc:
        logger.debug("email summary skipped: %s", exc)
        return {}
    raw = re.sub(r"<think>.*?</think>", "", raw, flags=re.DOTALL)
    raw = re.sub(r"^```(?:json)?|```$", "", raw.strip(), flags=re.MULTILINE).strip()
    i, j = raw.find("{"), raw.rfind("}")
    if i < 0 or j < 0:
        return {}
    try:
        d = _json.loads(raw[i:j + 1])
    except Exception:
        return {}
    if not isinstance(d, dict):
        return {}
    urgency = str(d.get("urgency") or "normal").strip().lower()
    if urgency not in ("high", "normal", "low"):
        urgency = "normal"
    return {"summary": str(d.get("summary") or "").strip()[:200],
            "urgency": urgency, "tone": str(d.get("tone") or "").strip()[:40]}


async def _notify_urgent_followup(tenant_id: str, row: dict) -> None:
    """A high-urgency email gets pinged immediately rather than waiting for the daily
    digest (which batches everything and skips itself entirely within 20h of the last
    send) — reuses the same followup_digest subscription/opt-out, just a separate,
    un-batched message."""
    try:
        from vula.integrations.notify import notify_team
        who = row.get("sender_name") or row.get("sender") or "someone"
        msg = (f"🔥 Urgent-looking email from {who}: {row.get('subject') or '(no subject)'}\n"
              f"{row.get('summary') or ''}\n\nOpen Vula → Follow-ups to reply.")
        await notify_team(tenant_id, "followup_digest", msg)
    except Exception as exc:
        logger.debug("urgent followup notify skipped: %s", exc)


async def _track_followup(db, tenant_id: str, account_id: str, em: dict, reason: str) -> None:
    try:
        name = ""
        frm = em.get("from") or ""
        if "<" in frm:
            name = frm.split("<")[0].strip().strip('"')
        row = {
            "tenant_id": tenant_id, "account_id": account_id, "email_uid": em.get("uid"),
            "sender": frm, "sender_name": name or None, "subject": em.get("subject"),
            "preview": (em.get("body") or "")[:240], "received_at": em.get("when"),
            "reason": reason, "status": "open",
            "message_id": em.get("message_id") or None,  # for reply-detection (migration 096)
        }
        row.update(await _summarize_followup(em.get("subject") or "", em.get("body") or ""))
        # email_uid is only unique WITHIN a mailbox (migration 093) — the same uid from two
        # different connected accounts must not collide/overwrite each other.
        db.table("vula_email_followups").upsert(
            row, on_conflict="tenant_id,account_id,email_uid").execute()
        # Fires at most once per real email — a given UID is only ever synced once, since the
        # cursor advances past it (a from_uid=0 backfill re-running is the only exception, an
        # explicit/rare operator action, not a normal-operation double-fire risk).
        if row.get("urgency") == "high":
            await _notify_urgent_followup(tenant_id, row)
    except Exception as exc:
        logger.debug("followup track skipped (run migration 093?): %s", exc)


async def backfill_followup_summaries(tenant_id: str, limit: int = 20) -> int:
    """Retry AI summarization for open follow-ups that missed it the first time — a
    transient local-LLM blip at sync time otherwise leaves summary/urgency/tone null
    forever, since _track_followup only ever runs once per email. Called once per tenant
    per sync cycle (see process_all_email_sync), so this catches up within a cycle or two
    rather than needing a manual fix."""
    db = _client()
    try:
        rows = (db.table("vula_email_followups").select("id,subject,preview,sender,sender_name")
                .eq("tenant_id", tenant_id).eq("status", "open")
                .is_("summary", "null").limit(limit).execute().data or [])
    except Exception as exc:
        logger.debug("summary backfill lookup skipped (run migration 092?): %s", exc)
        return 0
    n = 0
    for r in rows:
        fields = await _summarize_followup(r.get("subject") or "", r.get("preview") or "")
        if not fields:
            continue
        try:
            db.table("vula_email_followups").update(fields).eq("id", r["id"]).execute()
            n += 1
            if fields.get("urgency") == "high":
                await _notify_urgent_followup(tenant_id, {**r, **fields})
        except Exception as exc:
            logger.debug("summary backfill write failed: %s", exc)
    return n


async def send_followup_reminders(tenant_id: str, notify_phone: str = None) -> int:
    """Once a day, WhatsApp the team a digest of emails still awaiting a reply.
    Routing (team members + fallback) is handled by notify_team."""
    from datetime import datetime, timedelta, timezone
    db = _client()
    try:
        rows = (db.table("vula_email_followups").select("*")
                .eq("tenant_id", tenant_id).eq("status", "open")
                .order("received_at", desc=False).execute().data or [])
    except Exception:
        return 0
    if not rows:
        return 0
    # Don't re-nag within 20h of the last reminder.
    recent = [r.get("last_reminded_at") for r in rows if r.get("last_reminded_at")]
    if recent:
        try:
            newest = max(datetime.fromisoformat(t.replace("Z", "+00:00")) for t in recent)
            if datetime.now(timezone.utc) - newest < timedelta(hours=20):
                return 0
        except Exception:
            pass
    lines = []
    for r in rows[:6]:
        who = r.get("sender_name") or r.get("sender") or "someone"
        lines.append(f"• {who}: {(r.get('subject') or '(no subject)')[:50]}")
    msg = (f"📬 You have {len(rows)} email(s) awaiting a reply:\n" + "\n".join(lines) +
           "\n\nReply in your inbox, or open Vula → Follow-ups."
           "\n(Reply 'stop follow-up emails' anytime to turn these off.)")
    try:
        from vula.integrations.notify import notify_team
        sent = await notify_team(tenant_id, "followup_digest", msg)
        if not sent:
            return 0
        ids = [r["id"] for r in rows]
        now = datetime.now(timezone.utc).isoformat()
        for i in ids:
            db.table("vula_email_followups").update({"last_reminded_at": now}).eq("id", i).execute()
        return len(rows)
    except Exception as exc:
        logger.debug("followup reminder send failed: %s", exc)
        return 0


def _extract_msg_bytes(md) -> Optional[bytes]:
    """Some IMAP servers interleave an untagged FLAGS line for a DIFFERENT message
    (e.g. a \\Seen update) into the same fetch response, so md[0] isn't reliably the
    (info, literal) tuple we asked for — scan for the entry that actually carries the
    RFC822 literal rather than assuming position 0."""
    for item in md or []:
        if isinstance(item, tuple) and len(item) >= 2 and isinstance(item[1], (bytes, bytearray)):
            return item[1]
    return None


# Sent folder name varies by provider (Gmail nests it under "[Gmail]/", most others don't) —
# same trial-list approach service.py already uses to find Drafts.
_SENT_FOLDER_CANDIDATES = ("Sent", "INBOX.Sent", "[Gmail]/Sent Mail", "Sent Items", "Sent Messages")


def _fetch_new(creds: dict, last_uid: int, max_emails: int, folder: str = "INBOX",
               folder_candidates: Optional[tuple] = None) -> dict:
    """[blocking] Fetch new emails (UID > last_uid) from one folder, OLDEST first. Returns
    {emails: [...], max_uid: int, folder: <resolved name> | None, oversized: [...]}. Each
    email carries parsed contacts + real attachments. `folder_candidates`, if given, tries
    each name in turn and uses whichever one the server actually SELECTs (for Sent, whose
    name isn't standardised); `folder` alone is used as-is otherwise (INBOX needs no
    guessing).

    Oldest-first matters: the cursor advances to max(batch) after every call, so if a mailbox
    ever gets more than `max_emails` new messages between syncs, taking the NEWEST batch would
    permanently strand the older ones behind the advanced cursor — they'd never be fetched.
    Oldest-first means a backlog gets caught up over successive syncs instead of silently
    losing whatever didn't fit in one batch."""
    m = _imap_login(creds)
    try:
        resolved = None
        for name in (folder_candidates or (folder,)):
            try:
                typ, _sel = m.select(name)
                if typ == "OK":
                    resolved = name
                    break
            except Exception:
                continue
        if not resolved:
            return {"emails": [], "max_uid": last_uid, "folder": None, "oversized": []}
        typ, data = m.uid("search", None, "ALL")
        uids = [int(u) for u in (data[0].split() if data and data[0] else [])]
        new = sorted(u for u in uids if u > last_uid)
        batch = new[:max_emails]  # oldest-first — see docstring
        out, oversized = _read_messages(m, batch)
        return {"emails": out, "max_uid": max(batch) if batch else last_uid, "folder": resolved,
                "oversized": oversized}
    finally:
        try: m.logout()
        except Exception: pass


def _read_messages(m, batch: list) -> tuple:
    """[blocking] Read `batch` (UIDs in the folder `m` has selected) into the sync's email dicts.
    Returns (emails, oversized attachments)."""
    out, oversized = [], []
    for u in batch:
        # BODY.PEEK[] (not RFC822/BODY[]) — a plain fetch implicitly sets \Seen server-side,
        # which was silently marking the owner's real inbox as "read" before they ever opened
        # it themselves (confirmed live — Judy was missing real mail because it no longer
        # showed as unread by the time she checked). PEEK returns identical bytes, no side effect.
        typ, md = m.uid("fetch", str(u).encode(), "(BODY.PEEK[])")
        raw = _extract_msg_bytes(md)
        if not raw:
            continue
        msg = email.message_from_bytes(raw)
        when = None
        try:
            when = parsedate_to_datetime(msg.get("Date")).astimezone(timezone.utc).isoformat()
        except Exception:
            when = datetime.now(timezone.utc).isoformat()
        people = [(_hdr(n), a) for n, a in
                  getaddresses([msg.get("From", ""), msg.get("To", ""), msg.get("Cc", "")])]
        body, attachments = "", []
        for part in msg.walk():
            if part.get_content_type() == "text/plain" and not body and "attachment" not in str(part.get("Content-Disposition") or "").lower():
                try:
                    body = part.get_payload(decode=True).decode(part.get_content_charset() or "utf-8", "ignore")
                except Exception:
                    body = ""
            elif _is_real_attachment(part):
                payload = part.get_payload(decode=True)
                name = _hdr(part.get_filename()) or f"attachment-{u}"
                if payload and len(payload) <= _MAX_ATTACH_BYTES:
                    attachments.append({"name": name, "data": payload,
                                        "mime": part.get_content_type() or "application/octet-stream"})
                elif payload:
                    # Previously dropped with no trace — now surfaced so the team knows
                    # something didn't get auto-filed, instead of it just vanishing.
                    oversized.append({"name": name, "size_mb": round(len(payload) / 1024 / 1024, 1),
                                      "subject": _hdr(msg.get("Subject")) or "(no subject)",
                                      "from": _hdr(msg.get("From")), "when": when})
        out.append({"uid": u, "when": when, "subject": _hdr(msg.get("Subject")) or "(no subject)",
                    "from": _hdr(msg.get("From")), "people": people, "body": body[:2000],
                    "attachments": attachments,
                    "bulk": bool(msg.get("List-Unsubscribe")),
                    # Threading headers — captured on every email (cheap), used for
                    # reply-detection: an inbound message's own Message-ID gets stored on
                    # the follow-up it creates; an outbound (Sent) message's In-Reply-To/
                    # References name the message(s) it's replying to.
                    "message_id": (msg.get("Message-ID") or "").strip(),
                    "in_reply_to": (msg.get("In-Reply-To") or "").strip(),
                    "references": (msg.get("References") or "").strip(),
                    # Set by vula/email_imap/service.py::_build() on anything Vula composed
                    # (auto-sent or a draft) — excluded from voice-profile learning below.
                    "vula_sent": bool((msg.get("X-Vula-Sent") or "").strip())})
    return out, oversized


# ── Every other folder (11 Nov 2026, migration 260) ───────────────────────────────────────────
# HPC's payment certificates 1 and 5-13 sat in Judy's mailbox but never reached Vula: the sync read
# INBOX and Sent only, and anything a mail rule (or Judy) moved into a folder was never seen. Now
# every other selectable folder is read too, each with its own cursor, for every connected mailbox
# (sync_all_folders, on by default; false keeps a mailbox to Inbox and Sent).

# Folders never worth reading: deleted, junk, drafts, and Gmail's views that repeat other folders.
_SKIP_FOLDER_FLAGS = {"\\noselect", "\\nonexistent", "\\trash", "\\junk", "\\drafts", "\\all",
                      "\\flagged", "\\important", "\\sent"}
_SKIP_FOLDER_NAME = re.compile(
    r"(^|[/.])(trash|deleted( items| messages)?|bin|junk( e-?mail)?|spam|bulk mail|drafts?|outbox|"
    r"sync issues|conflicts|local failures|server failures|notes|calendar|contacts|tasks|journal|"
    r"rss( feeds| subscriptions)?|conversation history|all mail|starred|important)$", re.IGNORECASE)
# Mail older than this, found in a folder for the first time, is filed without asking anyone
# anything: a "which project?" WhatsApp per old document would bury the owner.
HISTORIC_DAYS = 7
_LIST_LINE = re.compile(r'^\((?P<flags>[^)]*)\)\s+(?P<delim>"(?:[^"\\]|\\.)*"|NIL)\s*(?P<name>.*)$', re.IGNORECASE)


def _parse_list_item(item) -> Optional[tuple]:
    """One entry of an IMAP LIST reply → (flags as a lower-case set, folder name), else None.
    imaplib gives a name with special characters as a (line, literal) tuple."""
    if isinstance(item, tuple):
        line = item[0].decode("utf-8", "replace") if isinstance(item[0], (bytes, bytearray)) else str(item[0])
        lit = item[1].decode("utf-8", "replace") if isinstance(item[1], (bytes, bytearray)) else str(item[1])
        line = re.sub(r"\{\d+\}\s*$", "", line).rstrip() + " " + '"' + lit.replace('"', '\\"') + '"'
    elif isinstance(item, (bytes, bytearray)):
        line = item.decode("utf-8", "replace")
    else:
        return None
    m = _LIST_LINE.match(line.strip())
    if not m:
        return None
    name = m.group("name").strip()
    if len(name) >= 2 and name[0] == name[-1] == '"':
        name = re.sub(r'\\(["\\])', r"\1", name[1:-1])
    flags = {f.lower() for f in m.group("flags").split()}
    return (flags, name) if name else None


def _folder_skipped(flags: set, name: str, skip: set) -> bool:
    if flags & _SKIP_FOLDER_FLAGS or name.lower() in skip:
        return True
    return bool(_SKIP_FOLDER_NAME.search(name))


def _quote_mailbox(name: str) -> str:
    """imaplib passes a mailbox name as-is; one with a space must be quoted."""
    return '"' + name.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _uidvalidity(m) -> Optional[int]:
    try:
        typ, data = m.response("UIDVALIDITY")
        return int(data[0]) if data and data[0] is not None else None
    except Exception:
        return None


def _fetch_other_folders(creds: dict, cursors: dict, skip: set, budget: int) -> dict:
    """[blocking] New mail (oldest first) from every folder except INBOX, the Sent folder and the
    ones never worth reading (_folder_skipped). `cursors` is the account's folder_cursors; at most
    `budget` emails are read per sync, shared out between the folders that have new mail so one big
    Archive can't hold the others back. Folders are opened read-only (EXAMINE): nothing changes in
    the mailbox. Returns {emails (each with its "folder"), cursors (updated), oversized, folders}."""
    m = _imap_login(creds)
    try:
        typ, listing = m.list()
        names = []
        for item in (listing or []) if typ == "OK" else []:
            parsed = _parse_list_item(item)
            if parsed and not _folder_skipped(parsed[0], parsed[1], skip):
                names.append(parsed[1])
        names = sorted(set(names), key=str.lower)
        pending = []      # (name, uidvalidity, new uids)
        cursors = {k: dict(v) for k, v in (cursors or {}).items() if isinstance(v, dict)}
        for name in names:
            try:
                typ, _ = m.select(_quote_mailbox(name), readonly=True)
                if typ != "OK":
                    continue
                validity = _uidvalidity(m)
                cur = cursors.get(name) or {}
                last = int(cur.get("last_uid") or 0) if cur.get("uidvalidity") == validity else 0
                typ, data = m.uid("search", None, f"UID {last + 1}:*")
                uids = sorted(int(u) for u in (data[0].split() if typ == "OK" and data and data[0] else [])
                              if int(u) > last)
                if cur.get("uidvalidity") != validity:
                    cursors[name] = {"uidvalidity": validity, "last_uid": last}
                if uids:
                    pending.append((name, validity, uids))
            except Exception as exc:
                logger.info("email folder %r skipped: %s", name, exc)
        out, oversized, left = [], [], max(0, int(budget))
        for i, (name, validity, uids) in enumerate(pending):
            if left <= 0:
                break
            share = max(1, -(-left // (len(pending) - i)))      # ceil: what's left, split evenly
            batch = uids[:share]
            try:
                typ, _ = m.select(_quote_mailbox(name), readonly=True)
                if typ != "OK":
                    continue
                emails, big = _read_messages(m, batch)
            except Exception as exc:
                logger.info("email folder %r read failed: %s", name, exc)
                continue
            for em in emails:
                em["folder"], em["uidvalidity"] = name, validity
            out += emails
            oversized += big
            left -= len(batch)
            cursors[name] = {"uidvalidity": validity, "last_uid": max(batch)}
        return {"emails": out, "cursors": cursors, "oversized": oversized, "folders": names}
    finally:
        try: m.logout()
        except Exception: pass


def _is_historic(em: dict, now: Optional[datetime] = None) -> bool:
    try:
        when = datetime.fromisoformat(str(em.get("when")))
    except (TypeError, ValueError):
        return False
    now = now or datetime.now(timezone.utc)
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    return (now - when).days >= HISTORIC_DAYS


def _save_folder_cursors(db, account_id: str, cursors: dict) -> None:
    try:
        db.table("vula_email_accounts").update({"folder_cursors": cursors}).eq("id", account_id).execute()
    except Exception as exc:
        logger.debug("folder cursor save skipped (run migration 260?): %s", exc)


def _upsert_contact(db, tenant_id: str, addr: str, name: str, kind: str, when: str) -> None:
    addr = (addr or "").strip().lower()
    if not addr or "@" not in addr or _NOISE.search(addr):
        return
    try:
        existing = (db.table("vula_email_contacts").select("id,message_count")
                    .eq("tenant_id", tenant_id).eq("email", addr).limit(1).execute().data or [])
        if existing:
            e = existing[0]
            db.table("vula_email_contacts").update({
                "message_count": (e.get("message_count") or 0) + 1, "last_seen": when,
                **({"name": name} if name else {})}).eq("id", e["id"]).execute()
        else:
            db.table("vula_email_contacts").insert({
                "tenant_id": tenant_id, "email": addr, "name": name or None,
                "domain": addr.split("@")[-1], "kind": kind, "message_count": 1,
                "first_seen": when, "last_seen": when}).execute()
    except Exception as exc:
        logger.debug("contact upsert skipped (run migration 024?): %s", exc)


async def process_email_sync(tenant_id: str, account_id: str, max_emails: int = 20,
                             from_uid: Optional[int] = None) -> dict:
    """Sync one connected mailbox: build contacts + file work attachments from new mail.
    `from_uid` overrides the stored cursor (from_uid=0 → historical backfill of the most-recent
    `max_emails`), so we can catch Vula up on a mailbox's back-history in one controlled pass."""
    lock = _lock_for(account_id)
    if lock.locked():
        return {"synced": 0, "skipped": "sync already running"}
    async with lock:
        return await _do_email_sync(tenant_id, account_id, max_emails, from_uid)


async def process_tenant_email_sync(tenant_id: str, max_emails: int = 20,
                                    from_uid: Optional[int] = None) -> dict:
    """Sync every connected mailbox for a tenant (manual 'sync now' / backfill from the
    dashboard, where the caller thinks in terms of a tenant, not a specific account)."""
    from vula.email_imap.credentials import list_email_accounts
    accounts = list_email_accounts(tenant_id)
    if not accounts:
        return {"synced": 0, "accounts": 0}
    results = {}
    total = 0
    for acct in accounts:
        r = await process_email_sync(tenant_id, acct["id"], max_emails, from_uid)
        results[acct["email"]] = r
        total += r.get("synced", 0)
    return {"synced": total, "accounts": len(accounts), "by_account": results}


def _normalize_subject(subject: str) -> str:
    """Strip reply/forward prefixes — repeatedly, so "Re: Re: Fwd: Quote for deck" and "Quote
    for deck" compare equal — used only as the fallback matcher below, when threading headers
    don't line up."""
    s = (subject or "").strip()
    while True:
        stripped = re.sub(r"^\s*(re|fwd?|fw)\s*:\s*", "", s, flags=re.IGNORECASE)
        if stripped == s:
            return s.lower()
        s = stripped


_EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")


async def _resolve_replied_followups(db, tenant_id: str, sent_emails: list) -> int:
    """Mark open follow-ups 'done' when a Sent-folder email turns out to be the reply. Matched
    by Message-ID first (an outbound reply's In-Reply-To/References name the message it's
    replying to — reliable, since mail clients set these automatically) — falling back to
    same-counterparty + normalized-subject for the rarer case where those headers are missing
    (some webmail composers drop them). Cross-account on purpose: a reply sent from ANY of the
    tenant's connected mailboxes should resolve a follow-up that arrived in any other."""
    if not sent_emails:
        return 0
    try:
        open_rows = (db.table("vula_email_followups")
                     .select("id,message_id,sender,subject").eq("tenant_id", tenant_id)
                     .eq("status", "open").execute().data or [])
    except Exception as exc:
        logger.debug("followup resolve lookup skipped (run migration 096?): %s", exc)
        return 0
    if not open_rows:
        return 0
    by_msgid = {r["message_id"]: r for r in open_rows if r.get("message_id")}

    resolved = {}
    for em in sent_emails:
        candidates = set()
        if em.get("in_reply_to"):
            candidates.add(em["in_reply_to"])
        candidates.update((em.get("references") or "").split())
        match = next((by_msgid[c] for c in candidates if c in by_msgid), None)
        if not match:
            to_addrs = {a.lower() for _, a in em.get("people", [])}
            subj = _normalize_subject(em.get("subject"))
            if subj:
                for r in open_rows:
                    if r["id"] in resolved:
                        continue
                    m = _EMAIL_RE.search((r.get("sender") or "").lower())
                    addr = m.group(0) if m else ""
                    if addr and addr in to_addrs and _normalize_subject(r.get("subject")) == subj:
                        match = r
                        break
        if match:
            resolved[match["id"]] = True

    if resolved:
        try:
            db.table("vula_email_followups").update({"status": "done"}).in_(
                "id", list(resolved.keys())).execute()
        except Exception as exc:
            logger.debug("followup auto-resolve write failed: %s", exc)
            return 0
    return len(resolved)


_QUOTE_MARKERS = re.compile(
    r"\n\s*(On .{0,80} wrote:|-{2,}\s*Original Message\s*-{2,}|From:\s*.+\nSent:\s*.+|>.*)",
    re.IGNORECASE | re.DOTALL,
)


def _strip_quoted(body: str) -> str:
    """Keep only the new text above the quoted thread — the quoted history isn't the owner's
    own voice, just noise/duplication if left in."""
    m = _QUOTE_MARKERS.search(body or "")
    return (body[:m.start()] if m else (body or "")).strip()


async def _capture_voice_samples(db, tenant_id: str, sent_emails: list) -> None:
    """Keep a short, redacted excerpt of genuinely human-composed Sent-folder emails (never
    Vula's own AI-composed mail — see the vula_sent flag) for voice_profile.py to learn tone
    from. Best-effort: a failure here must never break the actual email sync."""
    rows = []
    for em in sent_emails:
        if em.get("vula_sent"):
            continue
        text = _strip_quoted(em.get("body", ""))
        if len(text) < 20:
            continue
        rows.append({"tenant_id": tenant_id, "excerpt": text[:600]})
    if not rows:
        return
    try:
        db.table("vula_email_voice_samples").insert(rows).execute()
    except Exception as exc:
        logger.debug("voice-sample capture skipped (run migration 120?): %s", exc)


async def _record_sync_failure(db, tenant_id: str, account_id: str, email_addr: str,
                               prior_fail_count: int, error: str) -> None:
    """Bump the consecutive-failure streak. Only notify AT the threshold crossing, not every
    run after — otherwise a mailbox down for a week nags every 15 minutes forever."""
    new_count = prior_fail_count + 1
    update = {"sync_fail_count": new_count, "last_error": error}
    try:
        if new_count == _FAIL_NOTIFY_THRESHOLD:
            update["status"] = "error"
            db.table("vula_email_accounts").update(update).eq("id", account_id).execute()
            from vula.integrations.notify import notify_team
            await notify_team(tenant_id, "email_sync_broken",
                              f"⚠️ {email_addr} has failed to sync {new_count} times in a row and looks "
                              f"disconnected ({error[:100]}). Check its password/connection in Email settings.")
        else:
            db.table("vula_email_accounts").update(update).eq("id", account_id).execute()
    except Exception as exc:
        logger.debug("sync failure record skipped: %s", exc)


async def _record_sync_recovery(db, tenant_id: str, account_id: str, email_addr: str, was_error: bool) -> None:
    try:
        db.table("vula_email_accounts").update(
            {"sync_fail_count": 0, "last_error": None, "status": "connected"}).eq("id", account_id).execute()
        if was_error:
            from vula.integrations.notify import notify_team
            await notify_team(tenant_id, "email_sync_broken", f"✅ {email_addr} is syncing again.")
    except Exception as exc:
        logger.debug("sync recovery record skipped: %s", exc)


async def _notify_oversized(tenant_id: str, items: list) -> None:
    try:
        from vula.integrations.notify import notify_team
        lines = [f"• {it['name']} ({it['size_mb']}MB) from {it['from']} — \"{it['subject'][:40]}\""
                for it in items[:5]]
        msg = (f"📎 {len(items)} attachment(s) were too large to auto-file (over 15MB) — "
               f"you'll need to grab these from the mailbox directly:\n" + "\n".join(lines))
        await notify_team(tenant_id, "oversized_attachment", msg)
    except Exception as exc:
        logger.debug("oversized attachment notify skipped: %s", exc)


async def _report_filing_failure(tenant_id: str, em: dict, att: dict, exc: Exception, where: str) -> None:
    """A document came in by email and the filing pipeline threw somewhere before it ever got
    a row in vula_filed_documents. 2026-09-22 incident: every catch along that pipeline logged
    at DEBUG, which vula/api/server.py's logging.basicConfig() suppresses entirely in production
    (INFO unless settings.debug) — so a failed attachment vanished with literally zero trace:
    no log line, no notification, and the sync cursor still advanced past it (each attachment is
    caught independently in the loop above), meaning it was never retried either. Warning-level
    + a WhatsApp nudge makes a future failure visible and actionable instead of silent data loss."""
    logger.warning("%s for %s (%s): %s", where, att.get("name"), tenant_id, exc)
    try:
        from vula.integrations.notify import notify_team
        await notify_team(tenant_id, "attachment_filing_failed", (
            f"⚠️ A document came in by email — *{att.get('name') or '(unnamed)'}* "
            f"from {em.get('from','')} — but Vula couldn't file it ({str(exc)[:100]}). "
            f"You may need to forward it again or add it manually."))
    except Exception:
        pass


def _advance_cursor(db, account_id: str, em: dict, sent_folder: Optional[str]) -> None:
    """Move this account's cursor for `em`'s folder to `em["uid"]`, so a crash while filing
    this email's attachments skips it next sweep instead of replaying it forever. Best-effort;
    never moves a cursor backwards (UIDs only grow per folder)."""
    col = "last_sync_uid_sent" if em.get("is_sent") else "last_sync_uid"
    if em.get("is_sent") and not sent_folder:
        return
    uid = int(em.get("uid") or 0)
    if uid <= 0:
        return
    try:
        db.table("vula_email_accounts").update({col: uid}).eq("id", account_id) \
            .lt(col, uid).execute()
    except Exception as exc:
        logger.debug("email sync cursor advance skipped: %s", exc)


async def _do_email_sync(tenant_id: str, account_id: str, max_emails: int,
                         from_uid: Optional[int] = None) -> dict:
    import asyncio
    creds = get_email_creds(tenant_id, account_id)
    if not creds:
        return {"synced": 0}
    db = _client()
    try:
        row = (db.table("vula_email_accounts")
               .select("last_sync_uid,last_sync_uid_sent,auto_sync,sync_sent,notify_phone,"
                       "status,sync_fail_count")
               .eq("id", account_id).limit(1).execute().data or [{}])[0]
    except Exception:
        return {"synced": 0}
    if row.get("auto_sync") is False and from_uid is None:
        return {"synced": 0}
    notify_phone = row.get("notify_phone")
    own_domain = creds["email"].split("@")[-1].lower()
    fail_count = int(row.get("sync_fail_count") or 0)

    # Backfill (from_uid given) scans from that floor for BOTH folders; normal sync continues
    # from each folder's own stored cursor — UIDs are only unique within a folder, so Inbox
    # and Sent can't share one.
    inbox_last = int(from_uid) if from_uid is not None else int(row.get("last_sync_uid") or 0)
    if creds.get("locked"):
        # 2 Nov 2026: a blank password went to the mail server for three weeks and came back as
        # "authentication failed", which read like a wrong password. Say what is actually wrong.
        err = SAVED_PASSWORD_LOCKED
        logger.warning("email sync skipped for %s/%s: %s", tenant_id, creds["email"], err)
        await _record_sync_failure(db, tenant_id, account_id, creds["email"], fail_count, err)
        return {"synced": 0, "error": err}
    try:
        inbox_result = await asyncio.to_thread(_fetch_new, creds, inbox_last, max_emails)
    except Exception as exc:
        logger.warning("email sync fetch (INBOX) failed for %s/%s: %s", tenant_id, creds["email"], exc)
        await _record_sync_failure(db, tenant_id, account_id, creds["email"], fail_count, str(exc)[:200])
        return {"synced": 0, "error": str(exc)[:120]}
    # A successful fetch means the connection itself is fine — clear any prior failure streak,
    # and if this mailbox had been flagged broken, tell the team it's back.
    if fail_count or row.get("status") == "error":
        await _record_sync_recovery(db, tenant_id, account_id, creds["email"], was_error=row.get("status") == "error")
    for em in inbox_result["emails"]:
        em["is_sent"] = False
        em["historic"] = _is_historic(em)     # a new mailbox's history: filed, nobody messaged

    sent_result = {"emails": [], "max_uid": int(row.get("last_sync_uid_sent") or 0), "folder": None, "oversized": []}
    if row.get("sync_sent") is not False:
        sent_last = int(from_uid) if from_uid is not None else int(row.get("last_sync_uid_sent") or 0)
        try:
            sent_result = await asyncio.to_thread(
                _fetch_new, creds, sent_last, max_emails, folder_candidates=_SENT_FOLDER_CANDIDATES)
        except Exception as exc:
            logger.debug("email sync fetch (Sent) skipped for %s/%s: %s", tenant_id, creds["email"], exc)
        for em in sent_result["emails"]:
            em["is_sent"] = True
            em["historic"] = _is_historic(em)

    # Every other folder (migration 260: on for every mailbox unless switched off). Read separately
    # so a database without the migration syncs Inbox and Sent exactly as before.
    other = {"emails": [], "cursors": None, "oversized": []}
    try:
        extra = (db.table("vula_email_accounts").select("sync_all_folders,folder_cursors")
                 .eq("id", account_id).limit(1).execute().data or [{}])[0]
    except Exception:
        extra = {}
    if extra.get("sync_all_folders") is True:
        skip = {"inbox"} | {n.lower() for n in _SENT_FOLDER_CANDIDATES}
        if sent_result.get("folder"):
            skip.add(sent_result["folder"].lower())
        try:
            other = await asyncio.to_thread(_fetch_other_folders, creds, extra.get("folder_cursors") or {},
                                            skip, max_emails)
        except Exception as exc:
            logger.warning("email sync fetch (other folders) failed for %s/%s: %s",
                           tenant_id, creds["email"], exc)
            other = {"emails": [], "cursors": None, "oversized": []}
        for em in other["emails"]:
            em["is_sent"] = False
            em["historic"] = _is_historic(em)
    folder_cursors = dict(other["cursors"] or {}) if other["cursors"] is not None else None
    if folder_cursors is not None:
        # Each folder restarts where it was before this batch; it moves up email by email below,
        # so a crash while filing one email's attachments skips that email next time (as for INBOX).
        before = extra.get("folder_cursors") or {}
        for em in other["emails"]:
            prev = before.get(em["folder"]) or {}
            if prev.get("uidvalidity") == em.get("uidvalidity"):
                folder_cursors[em["folder"]] = dict(prev)
            else:
                folder_cursors[em["folder"]] = {"uidvalidity": em.get("uidvalidity"), "last_uid": 0}

    oversized = [o for o in inbox_result.get("oversized", []) + sent_result.get("oversized", [])
                 + other.get("oversized", []) if not _is_historic(o)]
    if oversized:
        await _notify_oversized(tenant_id, oversized)

    # Outbound mail this sync picked up might BE the reply to something still tracked as
    # open — close that loop before processing this batch's own new follow-ups below.
    resolved_followups = await _resolve_replied_followups(db, tenant_id, sent_result["emails"])

    await _capture_voice_samples(db, tenant_id, sent_result["emails"])

    emails = inbox_result["emails"] + sent_result["emails"] + other["emails"]
    contacts_seen, filed = set(), 0
    for em in emails:
        if em.get("folder") is not None and folder_cursors is not None:
            # Another folder's own cursor, saved before its attachments are filed (as below).
            folder_cursors[em["folder"]] = {"uidvalidity": em.get("uidvalidity"), "last_uid": int(em["uid"])}
            if em["attachments"]:
                _save_folder_cursors(db, account_id, folder_cursors)
        elif em["attachments"] and from_uid is None:
            # Save the cursor up to this email BEFORE filing its attachments. 2026-09-23: one
            # emailed drawing pack killed the worker mid-ingest; the cursor only advanced after
            # the whole batch, so every sweep re-fetched the same batch and died again — mail
            # sync stalled for every tenant for over a day. At-most-once for this one email's
            # attachments beats never getting past it. (Backfill never moves the live cursor.)
            _advance_cursor(db, account_id, em, sent_result["folder"])
        for name, addr in em["people"]:
            kind = "internal" if addr.lower().endswith("@" + own_domain) else "external"
            _upsert_contact(db, tenant_id, addr, name, kind, em["when"])
            contacts_seen.add(addr.lower())
        for att in em["attachments"]:
            try:
                await _file_attachment(tenant_id, em, att, notify_phone, quiet=bool(em.get("historic")))
                filed += 1
            except Exception as exc:
                if em.get("historic"):
                    logger.warning("attachment file failed for %s (%s, folder %s): %s",
                                   att.get("name"), tenant_id, em.get("folder"), exc)
                else:
                    await _report_filing_failure(tenant_id, em, att, exc, "attachment file failed")
        # Track emails that look like they need a reply — mail the tenant SENT never needs
        # one from them, so skip Sent-folder items outright rather than rely on _needs_reply's
        # own-domain check to filter them out incidentally. Nor does old mail found in a folder.
        if not em["is_sent"] and not em.get("historic"):
            reason = _needs_reply(em, own_domain)
            if reason:
                await _track_followup(db, tenant_id, account_id, em, reason)
            if from_uid is None:
                # Property24 / Private Property enquiry emails → listings enquiries (real-estate
                # add-on; a no-op unless the business has `listings`). Not on backfill, nor old mail
                # read by the catch-up or found in a folder: old leads.
                try:
                    from vula.listings import portal_leads
                    await portal_leads.maybe_take(tenant_id, em)
                except Exception as exc:
                    logger.warning("portal lead check failed for %s: %s", tenant_id, exc)
    if folder_cursors is not None:
        _save_folder_cursors(db, account_id, folder_cursors)

    try:
        # Scoped to this ONE account's row — with several mailboxes per tenant, filtering by
        # tenant_id alone here would overwrite every account's cursor with this account's uid.
        update = {"last_sync_uid": inbox_result["max_uid"], "last_sync_at": "now()"}
        if sent_result["folder"]:  # only advance the Sent cursor if a Sent folder was actually found
            update["last_sync_uid_sent"] = sent_result["max_uid"]
        db.table("vula_email_accounts").update(update).eq("id", account_id).execute()
    except Exception:
        pass
    return {"synced": len(emails), "contacts": len(contacts_seen), "filed_attachments": filed,
            "last_uid": inbox_result["max_uid"], "sent_folder": sent_result["folder"],
            "sent_last_uid": sent_result["max_uid"], "resolved_followups": resolved_followups,
            "other_folders": len(other.get("folders") or []), "other_folder_emails": len(other["emails"])}


def _already_filed(db, tenant_id: str, att: dict) -> bool:
    """The same file is already in the library. By its bytes (content_hash, migration 143): until
    11 Nov 2026 this matched the FILENAME, so the second "Payment Certificate.pdf" or "Invoice.pdf"
    a business ever received by email was dropped as a duplicate of the first, whatever was in it.
    A row saved before content hashes existed still matches by name, so old mail found in a folder
    for the first time doesn't file (and read into the bank) a second copy of something already in."""
    import hashlib
    digest = hashlib.sha256(att.get("data") or b"").hexdigest()
    try:
        if (db.table("vula_filed_documents").select("id").eq("tenant_id", tenant_id)
                .eq("content_hash", digest).limit(1).execute().data):
            return True
        return bool(db.table("vula_filed_documents").select("id").eq("tenant_id", tenant_id)
                    .eq("source", "email").eq("filename", att["name"]).is_("content_hash", "null")
                    .limit(1).execute().data)
    except Exception:
        return False


async def _file_attachment(tenant_id: str, em: dict, att: dict, notify_phone: str = None,
                           quiet: bool = False) -> None:
    """Ingest an attachment into the KB + record it in the Documents library. The AI
    analyses the document (category/summary/fields) and project-matches on its CONTENT —
    so e.g. a 'payment notification' that is actually a wage payment for a project gets
    classified and filed correctly rather than dropped as noise.

    `quiet` (old mail found in a folder for the first time): filed the same way, but nobody is
    messaged about it — a document Vula can't place waits under To check instead of a WhatsApp."""
    from config import settings
    from vula.ingestion.pipeline import VulaIngestionPipeline

    db = _client()
    if _already_filed(db, tenant_id, att):
        return

    d = settings.upload_dir / tenant_id
    d.mkdir(parents=True, exist_ok=True)
    safe = re.sub(r"[^A-Za-z0-9._-]+", "_", att["name"])
    p = d / safe
    p.write_bytes(att["data"])

    # Bank statement, or a single payment-confirmation/POP? → unlock/read + reconcile against
    # invoices AND pending-EFT orders, in addition to filing it as a document. Best-effort;
    # never blocks normal filing. A POP that reconciles skips the generic "which project?" ask
    # below (payment_matched) — it's already been handled, and "project" is the wrong question
    # for a payment confirmation on a non-project business anyway.
    payment_matched = False
    bank_statement = False        # one of the business's own bank statements: the whole business's
    _BANK_DOMAINS = ("standardbank.co.za", "fnb.co.za", "nedbank.co.za", "absa.co.za",
                     "capitecbank.co.za", "investec.co.za", "tymebank.co.za", "africanbank.co.za",
                     "bidvestbank.co.za", "discovery.co.za")
    _POP_WORDS = ("payment confirmation", "proof of payment", "remittance advice",
                 "eft confirmation", " pop.pdf", "-pop.pdf")
    try:
        blob = f"{em.get('subject','')} {em.get('from','')} {att['name']}".lower()
        from vula.commerce import bank_rec
        if att["name"].lower().endswith(".pdf") and ("statement" in blob or "capitec" in blob):
            rec = await bank_rec.ingest_statement(tenant_id, p, source_file=att["name"])
            bank_statement = not rec.get("not_bank_statement") and not rec.get("error")
            if rec.get("private"):
                # One member's own account: filed for them alone, not the business (vula/private_docs.py).
                from vula import private_docs
                res = await VulaIngestionPipeline(tenant_id=tenant_id).ingest_file(
                    p, source_type="document", private_to=rec["private_to"])
                await private_docs.store(tenant_id, rec["private_to"], filename=att["name"],
                                         doc_id=getattr(res, "doc_id", None), category="Bank Statement",
                                         summary=f"Private bank statement, account ending {str(rec.get('bank_account'))[-4:]}",
                                         source="email")
                return
            logger.info("bank statement auto-reconciled (%s): %s", tenant_id, rec)
            # Ask the owner to finish the ones Vula wasn't sure about (one nudge per statement).
            to_review = int(rec.get("needs_input", 0)) + int(rec.get("unmatched_credits", 0))
            if not rec.get("error") and to_review > 0 and not quiet:
                try:
                    from vula.integrations.notify import notify_team
                    await notify_team(tenant_id, "bank_statement",
                                      f"🏦 Statement reconciled: {rec.get('matched_invoices', 0)} invoice(s), "
                                      f"{rec.get('matched_orders', 0)} order(s) marked paid, "
                                      f"{rec.get('matched_workers', 0)} labour payment(s) recognised. "
                                      f"*{to_review} transaction(s) need you to confirm where they go* — open the Bank tab.")
                except Exception as exc:
                    logger.debug("bank statement notify skipped: %s", exc)
        elif att["name"].lower().endswith(".pdf") and (
                any(d in blob for d in _BANK_DOMAINS) or any(w in blob for w in _POP_WORDS)):
            rec = await bank_rec.ingest_payment_confirmation(tenant_id, p, source_file=att["name"])
            logger.info("payment confirmation processed (%s): %s", tenant_id, rec)
            matched = int(rec.get("matched_invoices", 0)) + int(rec.get("matched_orders", 0))
            if not rec.get("error") and matched > 0:
                payment_matched = True  # suppress the generic "which project?" ask below
            elif not rec.get("error") and quiet:
                payment_matched = True  # old mail: it waits on the Bank tab, nobody is messaged
            elif not rec.get("error"):
                # Read fine, but nothing outstanding matched the amount — ask right here instead
                # of silently falling through to a "which project" question about a payment, or
                # just pointing at the dashboard (bank_review.start_client_review, same reply-
                # driven loop the WhatsApp answer handler expects).
                try:
                    from vula.integrations.notify import notify_team
                    from vula.commerce import bank_review
                    q = bank_review.start_client_review(tenant_id)
                    msg = f"💰 Payment confirmation received (*{att['name']}*) but I couldn't match it automatically."
                    await notify_team(tenant_id, "payment_unmatched",
                                      f"{msg}\n\n{q}" if q else f"{msg} Check the Bank tab to allocate it.")
                    payment_matched = True  # already told the owner in payment-specific language
                except Exception as exc:
                    logger.debug("payment unmatched notify skipped: %s", exc)
    except Exception as exc:
        logger.debug("bank statement/payment auto-reconcile skipped: %s", exc)

    res = await VulaIngestionPipeline(tenant_id=tenant_id).ingest_file(p, source_type="document")

    # Let the AI read it → category + summary + structured fields.
    category, summary, fields = "Email attachment", em.get("subject"), {}
    try:
        from vula.api.whatsapp import _analyze_document
        analysis = await _analyze_document(tenant_id, att["name"], p)
        if analysis:
            category = analysis.get("category") or category
            summary = analysis.get("summary") or summary
            fields = analysis.get("fields") or {}
    except Exception as exc:
        logger.debug("attachment analysis skipped: %s", exc)

    try:
        from vula.integrations.doc_filing import (file_document, match_project,
                                                  lookup_learned_project)
        field_text = " ".join(str(v) for v in fields.values() if isinstance(v, (str, int, float)))
        # The attachment's own evidence only: its name, what's in it, and the email subject. Not
        # the email body — a forwarded thread or signature mentions other jobs, and one
        # "Atlantis" in a quoted reply filed Bo-Kaap council letters and an Echium parking
        # drawing under ATLANTIS FOODS (2026-09-28). Nor the sender: Judy forwards everything.
        hint = f"{em.get('subject','')} {att['name']} {summary or ''} {field_text}"
        # Learned rules (from past corrections) win — they're high-confidence by definition.
        match = lookup_learned_project(tenant_id, fields) or match_project(tenant_id, hint)
        try:        # a project named in the document beats a learned rule (see whatsapp.py)
            from vula.api.tenants import uses_projects as _up
            if _up(tenant_id):
                from vula.integrations.project_resolver import resolve as _resolve
                _named = _resolve(tenant_id, {}, hint)
                if _named and _named.get("kind") == "named" and (match or {}).get("project") != _named["project"]:
                    match = {"project": _named["project"], "clickup_list_id": None,
                             "confidence": _named["confidence"], "ambiguous": False}
        except Exception as exc:
            logger.debug("named-project check (email) skipped: %s", exc)
        # Auto-file on ANY non-ambiguous match with a resolved project — not just the old
        # "high" string, which silently discarded a real (if less certain) project guess and
        # asked a human unnecessarily. Only a genuine absence of signal, or an unresolved tie
        # between multiple plausible projects (match.get("ambiguous")), should ask.
        # Same bar as the WhatsApp path (>= 0.6): the register-substring fallback (0.5) is a guess.
        confident = bool(match and not match.get("ambiguous") and match.get("project")
                         and (match.get("confidence") or 0) >= 0.6)
        if not confident:
            # Same evidence as the WhatsApp path: the project named in the document, the bank
            # payment that settled it, the supplier's usual project (project_resolver).
            try:
                from vula.api.tenants import uses_projects as _uses_projects
                from vula.integrations.project_resolver import resolve, AUTO_FILE
                if _uses_projects(tenant_id):
                    r = resolve(tenant_id, fields, hint)
                    if r and r.get("project") and r["confidence"] >= AUTO_FILE:
                        match = {"project": r["project"], "clickup_list_id": None,
                                 "confidence": r["confidence"], "ambiguous": False}
                        confident = True
            except Exception as exc:
                logger.debug("project resolver (email) skipped: %s", exc)

        # Only auto-file on a CONFIDENT match. Anything weaker (no match, or a single
        # coincidental token) → ask the team on WhatsApp rather than risk mis-filing.
        # Unless this was already handled as a payment confirmation above — "which project?"
        # is the wrong question for a POP, and the owner's already been told either way.
        from vula.api.tenants import uses_projects
        # A commerce business has no projects to ask about (Off the Hook, 2026-09-28).
        from vula.integrations.doc_filing import is_bank_statement
        bank_statement = bank_statement or is_bank_statement(category, summary or "", att["name"])
        ask = not confident and not payment_matched and not bank_statement and uses_projects(tenant_id)
        if not confident or bank_statement:
            match, confident = None, False
        # file_document() does what this used to hand-roll, plus the two things it was
        # missing: a durable Supabase Storage copy (file_url) and — when the match carries
        # a ClickUp list — attaching the file into that project's ClickUp task.
        filed_row = await file_document(
            tenant_id, filename=att["name"], data=att["data"],
            content_type=att.get("mime") or "application/octet-stream",
            category=category, summary=summary, fields=fields,
            doc_id=getattr(res, "doc_id", None), source="email",
            filed_by=notify_phone if ask else (em.get("from") or ""),
            project=match["project"] if match else None,
            clickup_list_id=match.get("clickup_list_id") if match else None,
            status="pending_project" if ask else "filed",
        )

        # Post to the project finance ledger if it carries an amount.
        if confident and match:
            try:
                from vula.integrations.finances import post_finance_from_doc
                post_finance_from_doc(tenant_id, match["project"], fields,
                                      getattr(res, "doc_id", None), att["name"],
                                      summary or "", category, owner_names=[em.get("from", "")])
            except Exception as exc:
                logger.debug("finance post skipped: %s", exc)

        # A real B2B bill/quote (not already handled as a bank statement/POP above) gets
        # committed into the books via the same shared path the Smart Scanner uses —
        # supplier match/auto-create, due-date calc, commerce_invoices/expenses row.
        committed = None
        try:
            from vula.api.whatsapp import _FINANCIAL_DOC_CATEGORIES, _CATEGORY_TO_DOC_TYPE
            if category in _FINANCIAL_DOC_CATEGORIES and not payment_matched:
                from vula.commerce import service as commerce_service
                # _analyze_document's fields never set doc_type (only the Smart Scanner's
                # vision-scan schema does) — commit_inbound_document defaults a missing
                # doc_type to "receipt", which would silently misfile every real inbound
                # invoice/quote as an expense. Map the deep-analysis category across.
                commit_fields = dict(fields or {})
                commit_fields.setdefault("doc_type", _CATEGORY_TO_DOC_TYPE.get(category, "invoice"))
                committed = await commerce_service.commit_inbound_document(
                    tenant_id, commit_fields, auto_commit=True, source="email",
                    filed_document_id=filed_row.get("id") if filed_row else None,
                    project=match["project"] if match else None, notify=not quiet,
                )
        except Exception as exc:
            logger.warning("commit_inbound_document failed for email attachment %s: %s",
                           att.get("name"), exc)

        # When the bill also needs "is this supplier X?" (commit_inbound_document's approval),
        # the project question follows that answer (approvals._ask_project_next) — two
        # questions at once got one answer and the other was never asked (2026-10-05).
        supplier_question = bool(committed and committed.get("needs_review")
                                 and committed.get("supplier_match")
                                 and committed.get("record_type") == "invoice")
        if ask and filed_row and filed_row.get("id") and not supplier_question and not quiet:
            try:
                from vula.integrations.doc_filing import ask_project
                from vula.integrations.notify import recipients_for
                await ask_project(tenant_id, filed_row, recipients_for(tenant_id, "which_project"),
                                  prefix=f"📎 A document came in by email from {em.get('from','')}.",
                                  queue=True)
            except Exception as exc:
                logger.debug("notify ask failed: %s", exc)
    except Exception as exc:
        if quiet:
            logger.warning("filed_documents record failed for %s (%s): %s", att.get("name"), tenant_id, exc)
        else:
            await _report_filing_failure(tenant_id, em, att, exc, "filed_documents record failed")


# ── The one-time catch-up when a mailbox is connected (11 Nov 2026) ─────────────────────────
# "We should have a once-off on onboarding to sync so Vula can be filled in on all comms." The
# 15-minute sync reads 20 emails a folder per run, so a 10,000-email mailbox took about five days to
# be read. Connecting a mailbox (and the Backfill button, and every mailbox whose history hasn't been
# read since migration 260) now starts a catch-up in the background: bigger batches, back to back,
# until Inbox, Sent and every folder are read to the newest email. Old mail asks nobody anything.

CATCH_UP_BATCH = 50
CATCH_UP_ROUNDS = 600          # a ceiling, not a target: 600 x 50 = 30,000 emails a folder
_catch_ups: dict = {}


async def catch_up(tenant_id: str, account_id: str, batch: int = CATCH_UP_BATCH,
                   rounds: int = CATCH_UP_ROUNDS, pause: float = 2.0) -> dict:
    """Read the whole mailbox once: sync rounds of `batch` until a round finds nothing new, then
    record history_read_at. A round the 15-minute sync is already running waits and tries again."""
    import asyncio
    total, done = 0, False
    for _ in range(rounds):
        res = await process_email_sync(tenant_id, account_id, max_emails=batch)
        if res.get("skipped"):
            await asyncio.sleep(pause * 5)
            continue
        if res.get("error"):
            break
        total += int(res.get("synced") or 0)
        if not res.get("synced"):
            done = True
            break
        await asyncio.sleep(pause)
    if done:
        try:
            _client().table("vula_email_accounts").update({"history_read_at": "now()"}) \
                .eq("id", account_id).execute()
        except Exception as exc:
            logger.debug("history_read_at not saved (run migration 260?): %s", exc)
    logger.info("email catch-up for %s/%s: %s emails, %s", tenant_id, account_id, total,
                "finished" if done else "will carry on")
    return {"synced": total, "finished": done}


def start_catch_up(tenant_id: str, account_id: str) -> bool:
    """Start the catch-up for this mailbox unless one is already running here. True if started."""
    import asyncio
    if not account_id:
        return False
    running = _catch_ups.get(account_id)
    if running and not running.done():
        return False
    _catch_ups[account_id] = asyncio.create_task(catch_up(tenant_id, account_id))
    return True


def reset_history(account_id: str) -> None:
    """Read this mailbox again from its first email: every cursor back to the start."""
    db = _client()
    try:
        db.table("vula_email_accounts").update({"last_sync_uid": 0, "last_sync_uid_sent": 0}) \
            .eq("id", account_id).execute()
    except Exception as exc:
        logger.warning("email history reset failed for %s: %s", account_id, exc)
    try:
        db.table("vula_email_accounts").update({"folder_cursors": {}, "history_read_at": None}) \
            .eq("id", account_id).execute()
    except Exception as exc:
        logger.debug("folder cursors not reset (run migration 260?): %s", exc)


def _history_unread(account_id: str) -> bool:
    """This mailbox's history (every folder) hasn't been read yet. False before migration 260."""
    try:
        row = (_client().table("vula_email_accounts").select("sync_all_folders,history_read_at")
               .eq("id", account_id).limit(1).execute().data or [{}])[0]
    except Exception:
        return False
    return row.get("sync_all_folders") is True and not row.get("history_read_at")


async def process_all_email_sync() -> int:
    """Sync every connected mailbox, tenant or not (called by the background loop). Each
    connected account is its own row now (migration 093) — iterate rows directly rather than
    tenant_id, which would otherwise sync the same tenant's several mailboxes redundantly."""
    # 'error' accounts are included ON PURPOSE. Until 2026-09-01 this filtered on
    # status == "connected" only, which made the error state a ONE-WAY TRAP: three consecutive
    # failures set status='error' (see _record_sync_failure), and the account was then dropped
    # from this loop forever — never retried, so it could never recover even once the mail
    # server came back, and _record_sync_recovery below was unreachable in practice.
    #
    # Confirmed live: off-the-hook (info@offthehook.capetown) stopped syncing on 2026-08-24 with
    # "[Errno 110] Connection timed out" and sat dark for 8 days. Its sync_fail_count was frozen
    # at exactly 3 — the threshold — proving nothing had tried it since, where an hourly retry
    # would have shown ~190. The tenant about to take live WhatsApp orders had silently stopped
    # ingesting supplier invoices and customer mail entirely.
    #
    # Retrying hourly is cheap (one IMAP connect) and does not re-nag: _record_sync_failure only
    # notifies AT the threshold crossing, not on every subsequent failure.
    try:
        rows = (_client().table("vula_email_accounts").select("id,tenant_id,notify_phone")
                .in_("status", ["connected", "error"]).execute().data or [])
    except Exception:
        return 0
    total = 0
    reminded_tenants = set()
    for r in rows:
        tenant_id, account_id = r["tenant_id"], r["id"]
        try:
            if _history_unread(account_id) and start_catch_up(tenant_id, account_id):
                continue                  # the catch-up reads this mailbox, new mail included
            res = await process_email_sync(tenant_id, account_id)
            total += res.get("synced", 0)
            # Daily nudge for emails still awaiting a reply — once per tenant, not once per
            # mailbox, since the digest already lists every open followup for that tenant.
            if tenant_id not in reminded_tenants:
                reminded_tenants.add(tenant_id)
                await backfill_followup_summaries(tenant_id)
                await send_followup_reminders(tenant_id, r.get("notify_phone"))
        except Exception as exc:
            logger.warning("email sync failed for %s/%s: %s", tenant_id, account_id, exc)
    return total
