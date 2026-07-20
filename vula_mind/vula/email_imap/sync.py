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

    if not (subject or "").strip() and not (body or "").strip():
        return {}
    text = f"Subject: {subject or ''}\n\n{body or ''}"[:3000]
    litellm.drop_params = True
    try:
        model, api_key, api_base = await resolve_generation_route(task_type="email_summary")
        resp = await litellm.acompletion(
            model=model,
            messages=[{"role": "system", "content": _SUMMARY_SYSTEM},
                      {"role": "user", "content": text}],
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
        out = []
        oversized = []
        for u in batch:
            typ, md = m.uid("fetch", str(u).encode(), "(RFC822)")
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
                                          "from": _hdr(msg.get("From"))})
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
                        "references": (msg.get("References") or "").strip()})
        return {"emails": out, "max_uid": max(batch) if batch else last_uid, "folder": resolved,
                "oversized": oversized}
    finally:
        try: m.logout()
        except Exception: pass


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

    oversized = inbox_result.get("oversized", []) + sent_result.get("oversized", [])
    if oversized:
        await _notify_oversized(tenant_id, oversized)

    # Outbound mail this sync picked up might BE the reply to something still tracked as
    # open — close that loop before processing this batch's own new follow-ups below.
    resolved_followups = await _resolve_replied_followups(db, tenant_id, sent_result["emails"])

    emails = inbox_result["emails"] + sent_result["emails"]
    contacts_seen, filed = set(), 0
    for em in emails:
        for name, addr in em["people"]:
            kind = "internal" if addr.lower().endswith("@" + own_domain) else "external"
            _upsert_contact(db, tenant_id, addr, name, kind, em["when"])
            contacts_seen.add(addr.lower())
        for att in em["attachments"]:
            try:
                await _file_attachment(tenant_id, em, att, notify_phone)
                filed += 1
            except Exception as exc:
                logger.debug("attachment file failed: %s", exc)
        # Track emails that look like they need a reply — mail the tenant SENT never needs
        # one from them, so skip Sent-folder items outright rather than rely on _needs_reply's
        # own-domain check to filter them out incidentally.
        if not em["is_sent"]:
            reason = _needs_reply(em, own_domain)
            if reason:
                await _track_followup(db, tenant_id, account_id, em, reason)

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
            "sent_last_uid": sent_result["max_uid"], "resolved_followups": resolved_followups}


async def _file_attachment(tenant_id: str, em: dict, att: dict, notify_phone: str = None) -> None:
    """Ingest an attachment into the KB + record it in the Documents library. The AI
    analyses the document (category/summary/fields) and project-matches on its CONTENT —
    so e.g. a 'payment notification' that is actually a wage payment for a project gets
    classified and filed correctly rather than dropped as noise."""
    from config import settings
    from vula.ingestion.pipeline import VulaIngestionPipeline

    db = _client()
    # Dedup: don't re-file the same attachment if it's already in the library.
    try:
        dup = (db.table("vula_filed_documents").select("id")
               .eq("tenant_id", tenant_id).eq("source", "email")
               .eq("filename", att["name"]).limit(1).execute().data or [])
        if dup:
            return
    except Exception:
        pass

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
            logger.info("bank statement auto-reconciled (%s): %s", tenant_id, rec)
            # Ask the owner to finish the ones Vula wasn't sure about (one nudge per statement).
            to_review = int(rec.get("needs_input", 0)) + int(rec.get("unmatched_credits", 0))
            if not rec.get("error") and to_review > 0:
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
        from vula.integrations.doc_filing import (file_document, match_project, project_examples,
                                                  lookup_learned_project)
        field_text = " ".join(str(v) for v in fields.values() if isinstance(v, (str, int, float)))
        hint = f"{em.get('subject','')} {em.get('from','')} {summary or ''} {field_text} {em.get('body','')}"
        # Learned rules (from past corrections) win — they're high-confidence by definition.
        match = lookup_learned_project(tenant_id, fields) or match_project(tenant_id, hint)
        # Auto-file on ANY non-ambiguous match with a resolved project — not just the old
        # "high" string, which silently discarded a real (if less certain) project guess and
        # asked a human unnecessarily. Only a genuine absence of signal, or an unresolved tie
        # between multiple plausible projects (match.get("ambiguous")), should ask.
        confident = bool(match and not match.get("ambiguous") and match.get("project"))

        # Only auto-file on a CONFIDENT match. Anything weaker (no match, or a single
        # coincidental token) → ask the team on WhatsApp rather than risk mis-filing.
        # Unless this was already handled as a payment confirmation above — "which project?"
        # is the wrong question for a POP, and the owner's already been told either way.
        ask = not confident and not payment_matched
        if not confident:
            match = None
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
                await commerce_service.commit_inbound_document(
                    tenant_id, commit_fields, auto_commit=True, source="email",
                    filed_document_id=filed_row.get("id") if filed_row else None,
                    project=match["project"] if match else None,
                )
        except Exception as exc:
            logger.warning("commit_inbound_document failed for email attachment %s: %s",
                           att.get("name"), exc)

        if ask:
            try:
                from vula.integrations.notify import notify_team
                ex = project_examples(tenant_id)
                hint_txt = f" (e.g. {', '.join(ex)})" if ex else ""
                await notify_team(tenant_id, "which_project", (
                    f"📎 A document came in by email — *{att['name']}* "
                    f"from {em.get('from','')}. Which project should I file it under?{hint_txt} "
                    f"Reply with the project name, or 'skip'."))
            except Exception as exc:
                logger.debug("notify ask failed: %s", exc)
    except Exception as exc:
        logger.debug("filed_documents record skipped: %s", exc)


async def process_all_email_sync() -> int:
    """Sync every connected mailbox, tenant or not (called by the background loop). Each
    connected account is its own row now (migration 093) — iterate rows directly rather than
    tenant_id, which would otherwise sync the same tenant's several mailboxes redundantly."""
    try:
        rows = (_client().table("vula_email_accounts").select("id,tenant_id,notify_phone")
                .eq("status", "connected").execute().data or [])
    except Exception:
        return 0
    total = 0
    reminded_tenants = set()
    for r in rows:
        tenant_id, account_id = r["tenant_id"], r["id"]
        try:
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
