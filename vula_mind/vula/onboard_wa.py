"""
vula/onboard_wa.py — a new business set up over WhatsApp, from Vula's own number (5 Nov 2026).

Ian, after a test business made in Master turned out an empty shell (no owner number, no
WhatsApp line, no details, no welcome): "we brag about WhatsApp … send a welcome and let him add
details per question". Now:

1. Master › New business takes the owner's name and WhatsApp number (create_client). Vula makes
   the business, puts the owner on its team (so they can sign in with WhatsApp) and sends the
   welcome template from Vula's own number (settings.platform_wa_*).
2. The owner replies START and answers the setup questions one at a time (STEPS). Every answer
   is saved straight away and read back from the database before Vula says it's saved. SKIP
   leaves one for later, STOP pauses, START carries on.
3. Until the business connects a WhatsApp number of its own, its owner and team work with Vula
   through Vula's number: route_platform() hands their messages to the normal pipeline as that
   business, with REPLY_VIA_PLATFORM set so every reply goes out from Vula's number.
4. Master sees each business's progress (progress()) and can nudge (nudge()); one reminder goes
   out by itself 24 hours after the last message (remind_due(), server._onboarding_loop).

Anyone else messaging Vula's number gets one polite reply a day and Ian is told (a lead).
State lives in vula_onboarding (migration 249), read and written with the service key only.
"""
from __future__ import annotations

import logging
import re
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

log = logging.getLogger(__name__)

TABLE = "vula_onboarding"
ACTIVE = ("invited", "in_progress")
REMIND_AFTER = timedelta(hours=24)

_SKIP_RE = re.compile(r"^\s*(?:skip|next|later|pass|not now|n/?a)\s*[.!]?\s*$", re.I)
_STOP_RE = re.compile(r"^\s*(?:stop|pause|cancel)\s*[.!]?\s*$", re.I)
_START_RE = re.compile(r"^\s*(?:start|resume|continue|carry on)\b", re.I)
_DONE_RE = re.compile(r"^\s*(?:done|finished|that'?s all|that is all|no more|none|nobody|no one|no)\s*[.!]?\s*$", re.I)
_EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
_LOGIN_RE = re.compile(r"^\s*login\s+[A-Z0-9]{6}\s*$", re.I)


def _db():
    from vula.commerce import service
    return service._client()


def _now() -> datetime:
    return datetime.now(timezone.utc)


def digits(phone: str) -> str:
    from vula.team_index import digits_za
    return digits_za(phone or "")


def _first_name(name: Optional[str]) -> str:
    return (name or "").strip().split(" ")[0] or "there"


# ── The questions ──────────────────────────────────────────────────────────────────────────────
# kind: profile (business_profile answer), name, hours, email, vat, bank, logo, documents, team.
STEPS: List[Dict[str, str]] = [
    {"key": "name", "kind": "name", "label": "Business name",
     "q": "What's the business called, the way customers should see it on invoices and messages?"},
    {"key": "what_we_do", "kind": "profile", "label": "What you do",
     "q": "In a sentence or two: what does the business sell or do, and for whom?"},
    {"key": "area", "kind": "profile", "label": "Where you work",
     "q": "Where do you work or deliver? (areas or towns, or 'collection only')"},
    {"key": "hours", "kind": "hours", "label": "Hours",
     "q": "What are your working hours, and when are you closed? (e.g. Mon–Fri 8–5, Sat 8–1, closed Sundays)"},
    {"key": "payment", "kind": "profile", "label": "How customers pay",
     "q": "How do customers pay you (EFT, card, cash, payment link), and what are your payment terms?"},
    {"key": "pricing", "kind": "profile", "label": "Pricing",
     "q": "How do you price: a fixed price list, a quote per job, or per hour/m²? Anything you always add (call-out, delivery)?"},
    {"key": "email", "kind": "email", "label": "Email",
     "q": "Which email address should go on your invoices and quotes?"},
    {"key": "vat", "kind": "vat", "label": "VAT",
     "q": "Is the business VAT registered? Send the VAT number (10 digits, starts with 4), or reply *no*."},
    {"key": "bank", "kind": "bank", "label": "Bank details",
     "q": "Which bank account should customers pay into? Send the bank, account name, account number and "
          "branch code, e.g. *FNB, Seaview Tiles, 62812345678, 250655*."},
    {"key": "logo", "kind": "logo", "label": "Logo",
     "q": "Send your logo as a photo (it goes on your invoices and quotes), or reply *skip*."},
    {"key": "documents", "kind": "documents", "label": "Documents",
     "q": "Send any price list, menu, brochure or document customers ask about, one at a time, so I can "
          "answer from them. Reply *done* when that's all (or *skip*)."},
    {"key": "team", "kind": "team", "label": "Team",
     "q": "Who else in the team should use Vula? Send each person as *name, WhatsApp number, role* "
          "(role: manager or staff), e.g. *Thandi, 082 123 4567, manager*. Reply *done* when that's everyone."},
]
_BY_KEY = {s["key"]: s for s in STEPS}


def _index(step: Optional[str]) -> int:
    return next((i for i, s in enumerate(STEPS) if s["key"] == step), 0)


def _ask(step_key: str) -> str:
    i = _index(step_key)
    return f"*Question {i + 1} of {len(STEPS)}:* {STEPS[i]['q']}"


# ── Rows ───────────────────────────────────────────────────────────────────────────────────────

def get(tenant_id: str) -> Optional[Dict[str, Any]]:
    rows = (_db().table(TABLE).select("*").eq("tenant_id", tenant_id).limit(1).execute().data or [])
    return rows[0] if rows else None


def _for_phone(phone: str) -> List[Dict[str, Any]]:
    """This number's onboardings, newest first."""
    rows = (_db().table(TABLE).select("*").eq("owner_phone", digits(phone)).execute().data or [])
    return sorted(rows, key=lambda r: r.get("invited_at") or "", reverse=True)


def _update(tenant_id: str, patch: Dict[str, Any]) -> Dict[str, Any]:
    patch = {**patch, "updated_at": _now().isoformat()}
    _db().table(TABLE).update(patch).eq("tenant_id", tenant_id).execute()
    _USES_PLATFORM.pop(tenant_id, None)
    return get(tenant_id) or {}


_USES_PLATFORM: Dict[str, Tuple[float, bool]] = {}


def uses_platform(tenant_id: str) -> bool:
    """Was this business set up through Vula's own number? Then, while it has no WhatsApp number
    of its own, Vula's number speaks for it (whatsapp._get_tenant_wa_creds). Cached 5 minutes;
    False on any read failure."""
    hit = _USES_PLATFORM.get(tenant_id)
    if hit and time.time() - hit[0] < 300:
        return hit[1]
    try:
        yes = bool(_db().table(TABLE).select("tenant_id").eq("tenant_id", tenant_id).limit(1).execute().data)
    except Exception:
        return False
    _USES_PLATFORM[tenant_id] = (time.time(), yes)
    return yes


def _business_name(tenant_id: str) -> str:
    try:
        from vula.api.tenants import get_config
        return (get_config(tenant_id, fresh=True) or {}).get("display_name") or tenant_id
    except Exception:
        return tenant_id


def _has_own_number(tenant_id: str) -> bool:
    from vula import whatsapp_numbers
    try:
        return whatsapp_numbers.main_row(whatsapp_numbers.rows_for(tenant_id, _db())) is not None
    except Exception:
        return False


# ── Master: create, welcome, nudge, progress ───────────────────────────────────────────────────

async def send_welcome(row: Dict[str, Any]) -> str:
    """The welcome template from Vula's own number. Returns what happened, in words, and stores it."""
    from vula.integrations import platform_support as ps
    if not ps.platform_creds():
        status = "not sent: Vula's own WhatsApp number isn't set yet"
    elif await ps.send_template(row["owner_phone"], ps.SETUP_TEMPLATE, _first_name(row.get("owner_name")),
                                _business_name(row["tenant_id"])):
        status = "sent"
    else:
        status = "not sent: Meta refused the welcome (is vula_business_setup approved yet?)"
    try:
        _update(row["tenant_id"], {"welcome_status": status})
    except Exception as exc:
        log.warning("onboarding welcome status not saved for %s: %s", row["tenant_id"], exc)
    return status


async def create_client(identity: Dict[str, Any], *, tenant_id: str, display_name: str,
                        business_type: str, owner_name: str, owner_whatsapp: str,
                        owner_email: Optional[str] = None, send_welcome_now: bool = True) -> Dict[str, Any]:
    """Make the business, put its owner on the team, start its WhatsApp setup and send the
    welcome. Raises ValueError for a bad number; HTTPException(409) if the id is taken."""
    phone = digits(owner_whatsapp)
    if not (10 <= len(phone) <= 15):
        raise ValueError("Give the owner's WhatsApp number with its area code, e.g. 082 123 4567.")
    if not (owner_name or "").strip():
        raise ValueError("Give the owner's name.")
    email = (owner_email or "").strip().lower() or None
    if email and not _EMAIL_RE.fullmatch(email):
        raise ValueError("That email address doesn't look right.")
    from vula.api import tenants
    out = await tenants.create_tenant(tenants.TenantIn(
        tenant_id=tenant_id, display_name=display_name, business_type=business_type), identity=identity)
    if out.get("error"):
        return out
    db = _db()
    member = {"tenant_id": tenant_id, "name": owner_name.strip(), "whatsapp": phone, "role": "owner",
              "active": True}
    if email:
        member["email"] = email
    db.table("vula_team_members").upsert(member, on_conflict="tenant_id,whatsapp").execute()
    from vula import team_index
    team_index.invalidate(tenant_id)
    row = {"tenant_id": tenant_id, "owner_phone": phone, "owner_name": owner_name.strip(),
           "owner_email": email, "status": "invited", "step": STEPS[0]["key"], "answers": {},
           "created_by": (identity or {}).get("email") or (identity or {}).get("sub"),
           "invited_at": _now().isoformat(), "updated_at": _now().isoformat()}
    db.table(TABLE).upsert(row).execute()
    _USES_PLATFORM.pop(tenant_id, None)
    saved = get(tenant_id)
    if not saved or saved.get("owner_phone") != phone:
        return {**out, "error": "The business was made, but its WhatsApp setup couldn't be saved — "
                                "has migration 249 been applied?"}
    welcome = await send_welcome(saved) if send_welcome_now else "not sent (you chose not to)"
    return {**out, "onboarding": public(get(tenant_id) or saved), "welcome": welcome}


async def nudge(tenant_id: str) -> Dict[str, Any]:
    row = get(tenant_id)
    if not row:
        return {"error": "This business wasn't set up over WhatsApp."}
    if row.get("status") == "done":
        return {"error": "This business has finished its setup."}
    status = await send_welcome(row)
    if status == "sent":
        _update(tenant_id, {"reminded_at": _now().isoformat()})
    return {"welcome": status, "onboarding": public(get(tenant_id) or row)}


def public(row: Dict[str, Any]) -> Dict[str, Any]:
    answers = row.get("answers") or {}
    i = _index(row.get("step"))
    return {
        "tenant_id": row.get("tenant_id"), "owner_name": row.get("owner_name"),
        "owner_phone": row.get("owner_phone"), "status": row.get("status"),
        "step": row.get("step"), "step_label": _BY_KEY.get(row.get("step") or "", {}).get("label"),
        "step_number": len(STEPS) if row.get("status") == "done" else i + 1, "total": len(STEPS),
        "answered": sum(1 for s in STEPS if answers.get(s["key"]) not in (None, "", "skipped")),
        "answers": answers, "welcome_status": row.get("welcome_status"),
        "invited_at": row.get("invited_at"), "last_message_at": row.get("last_message_at"),
        "reminded_at": row.get("reminded_at"), "completed_at": row.get("completed_at"),
        "own_number": _has_own_number(row["tenant_id"]) if row.get("tenant_id") else False,
    }


def progress() -> List[Dict[str, Any]]:
    rows = _db().table(TABLE).select("*").execute().data or []
    rows.sort(key=lambda r: r.get("invited_at") or "", reverse=True)
    out = []
    for r in rows:
        p = public(r)
        p["display_name"] = _business_name(r["tenant_id"])
        out.append(p)
    return out


# ── Saving each answer (read back before it's reported saved) ──────────────────────────────────

async def _save_profile(tid: str, key: str, text: str) -> Optional[str]:
    from vula.commerce import business_profile
    res = await business_profile.save_answers(tid, {key: text}, by="whatsapp setup")
    if not res.get("saved"):
        return None
    return (business_profile.get_answers(tid) or {}).get(key)


async def _invoice_settings(tid: str, patch: Dict[str, Any]) -> Dict[str, Any]:
    from vula.commerce import service
    await service.upsert_invoice_settings(tid, patch)
    return await service.get_invoice_settings(tid) or {}


def _order_settings(tid: str, patch: Dict[str, Any]) -> None:
    try:
        from vula.commerce import order_workflow
        order_workflow.upsert_order_settings(tid, patch)
    except Exception as exc:
        log.info("order settings not updated during setup for %s: %s", tid, exc)


_BANKS = (
    ("First National Bank", ("fnb", "first national"), "250655"),
    ("ABSA", ("absa",), "632005"),
    ("Standard Bank", ("standard bank", "standard"), "051001"),
    ("Nedbank", ("nedbank", "ned bank"), "198765"),
    ("Capitec", ("capitec",), "470010"),
    ("Investec", ("investec",), "580105"),
    ("TymeBank", ("tymebank", "tyme"), "678910"),
    ("Discovery Bank", ("discovery",), "679000"),
    ("African Bank", ("african bank",), "430000"),
    ("Bank Zero", ("bank zero",), "888000"),
)


def parse_bank(text: str) -> Optional[Dict[str, str]]:
    """Bank, account name, account number and branch code from one message, or None without an
    account number. A known bank with no branch code gets its universal branch code."""
    raw = text or ""
    low = raw.lower()
    bank = next(((name, aliases, code) for name, aliases, code in _BANKS
                 if any(re.search(rf"\b{re.escape(a)}\b", low) for a in aliases)), None)
    nums = [re.sub(r"\s", "", n) for n in re.findall(r"\d[\d ]{4,}\d", raw)]
    labelled = re.search(r"(?:branch|code)\D{0,12}(\d{6})\b", low)
    branch = labelled.group(1) if labelled else None
    accounts = [n for n in nums if 7 <= len(n) <= 13 and n != branch]
    if not branch:
        sixes = [n for n in nums if len(n) == 6]
        branch = sixes[-1] if sixes else None
    if not accounts:
        return None
    account = max(accounts, key=len)
    name = raw
    for n in re.findall(r"\d[\d ]{4,}\d", raw):
        name = name.replace(n, " ")
    if bank:
        for a in sorted(bank[1], key=len, reverse=True):
            name = re.sub(rf"\b{re.escape(a)}\b", " ", name, flags=re.I)
        name = re.sub(r"\bbank\b", " ", name, flags=re.I)
    name = re.sub(r"\b(?:account|acc|acct|name|number|no|branch|code|holder|type|cheque|current|savings)\b[:.]?",
                  " ", name, flags=re.I)
    name = re.sub(r"[,;:\n]+", " ", name)
    name = re.sub(r"\s+", " ", name).strip(" -.")
    out = {"bank_name": bank[0] if bank else "", "account_name": name, "account_number": account,
           "branch_code": branch or (bank[2] if bank else "")}
    out["universal_branch"] = "yes" if (not branch and bank) else ""
    return out


def parse_member(text: str) -> Optional[Dict[str, str]]:
    m = re.search(r"\+?\d[\d ()-]{8,}\d", text or "")
    if not m:
        return None
    phone = digits(m.group(0))
    if not (10 <= len(phone) <= 15):
        return None
    rest = (text[:m.start()] + " " + text[m.end():])
    role = "manager" if re.search(r"\bmanager\b", rest, re.I) else \
        "bookkeeper" if re.search(r"\b(?:bookkeeper|accounts?)\b", rest, re.I) else "staff"
    name = re.sub(r"\b(?:manager|staff|bookkeeper|accounts?|role)\b", " ", rest, flags=re.I)
    name = re.sub(r"[,;:\n]+", " ", name)
    name = re.sub(r"\s+", " ", name).strip(" -.")
    if not name:
        return None
    return {"name": name[:80], "whatsapp": phone, "role": role}


async def _save(row: Dict[str, Any], step: Dict[str, str], text: str) -> Tuple[Optional[str], Optional[str]]:
    """(what was saved, as read back) or (None, why not / what to send instead)."""
    tid = row["tenant_id"]
    kind = step["kind"]
    text = (text or "").strip()
    if kind == "name":
        name = text[:120]
        _db().table("vula_tenant_config").update({"display_name": name}).eq("tenant_id", tid).execute()
        try:
            from vula.api import tenants
            tenants._CACHE.pop(tid, None)
        except Exception:
            pass
        s = await _invoice_settings(tid, {"company_name": name})
        back = _business_name(tid)
        return (back, None) if back == name and s.get("company_name") == name else (None, "I couldn't save the name just now — send it again?")
    if kind in ("profile", "hours"):
        back = await _save_profile(tid, step["key"], text)
        if not back:
            return None, "I couldn't save that just now — send it again?"
        if kind == "hours":
            _order_settings(tid, {"business_hours_note": text[:500]})
        return back, None
    if kind == "email":
        m = _EMAIL_RE.search(text)
        if not m:
            return None, "That doesn't look like an email address — send it like *name@business.co.za*, or reply *skip*."
        email = m.group(0).lower()
        s = await _invoice_settings(tid, {"company_email": email})
        _db().table("vula_team_members").update({"email": email}).eq("tenant_id", tid) \
            .eq("whatsapp", row["owner_phone"]).execute()
        _db().table(TABLE).update({"owner_email": email}).eq("tenant_id", tid).execute()
        return (email, None) if s.get("company_email") == email else (None, "I couldn't save that just now — send it again?")
    if kind == "vat":
        compact = re.sub(r"\D", "", text)
        if re.match(r"^\s*(?:no|nope|not|we'?re not|we are not|n)\b", text, re.I):
            s = await _invoice_settings(tid, {"vat_registered": False})
            return ("Not VAT registered", None) if s.get("vat_registered") is False else (None, "I couldn't save that just now — send it again?")
        if len(compact) == 10 and compact.startswith("4"):
            s = await _invoice_settings(tid, {"vat_registered": True, "vat_number": compact})
            ok = s.get("vat_registered") is True and s.get("vat_number") == compact
            return (f"VAT registered, number {compact}", None) if ok else (None, "I couldn't save that just now — send it again?")
        return None, "A South African VAT number has 10 digits and starts with 4. Send it again, or reply *no*."
    if kind == "bank":
        b = parse_bank(text)
        if not b:
            return None, ("I couldn't find an account number in that. Send it like "
                          "*FNB, Seaview Tiles, 62812345678, 250655*, or reply *skip*.")
        patch = {k: b[k] for k in ("bank_name", "account_name", "account_number", "branch_code") if b[k]}
        s = await _invoice_settings(tid, patch)
        if any(s.get(k) != v for k, v in patch.items()):
            return None, "I couldn't save the bank details just now — send them again?"
        line = ", ".join(v for v in (s.get("bank_name"), s.get("account_name"), s.get("account_number")) if v)
        line += f", branch {s.get('branch_code')}" if s.get("branch_code") else ""
        if b["universal_branch"]:
            line += " (the universal branch code)"
        _order_settings(tid, {"eft_details": line})
        return line, None
    return None, None


async def _save_logo(row: Dict[str, Any], media_id: str, mime: str) -> Tuple[Optional[str], Optional[str]]:
    from vula.api import whatsapp as wa
    data = await wa._download_media_bytes(media_id)
    if not data:
        return None, "I couldn't fetch that photo — send it again?"
    ext = "png" if "png" in (mime or "") else "jpg"
    path = f"{row['tenant_id']}/logo/{int(time.time())}-whatsapp.{ext}"
    url = wa._upload_to_storage("product-images", path, data, mime or "image/jpeg")
    if not url:
        return None, "I couldn't store that photo just now — send it again?"
    s = await _invoice_settings(row["tenant_id"], {"logo_url": url})
    return ("Logo saved", None) if s.get("logo_url") == url else (None, "I couldn't save the logo just now — send it again?")


async def _add_member(row: Dict[str, Any], text: str) -> Tuple[Optional[str], Optional[str]]:
    m = parse_member(text)
    if not m:
        return None, ("Send each person as *name, WhatsApp number, role*, e.g. *Thandi, 082 123 4567, "
                      "manager*, or reply *done*.")
    tid = row["tenant_id"]
    _db().table("vula_team_members").upsert({**m, "tenant_id": tid, "active": True},
                                            on_conflict="tenant_id,whatsapp").execute()
    from vula import team_index
    team_index.invalidate(tid)
    back = team_index.member_for_phone(tid, m["whatsapp"])
    if not back:
        return None, "I couldn't add them just now — send it again?"
    return f"{back.get('name')} ({back.get('whatsapp')}, {back.get('role')})", None


# ── The conversation ───────────────────────────────────────────────────────────────────────────

def _answers_with(row: Dict[str, Any], key: str, value: str) -> Dict[str, Any]:
    return {**(row.get("answers") or {}), key: value}


def _next_key(step_key: str) -> Optional[str]:
    i = _index(step_key)
    return STEPS[i + 1]["key"] if i + 1 < len(STEPS) else None


async def _advance(row: Dict[str, Any], answers: Dict[str, Any], prefix: str) -> str:
    nxt = _next_key(row.get("step") or STEPS[0]["key"])
    if not nxt:
        return await _finish(row, answers, prefix)
    _update(row["tenant_id"], {"step": nxt, "answers": answers, "status": "in_progress"})
    return f"{prefix}\n\n{_ask(nxt)}".strip()


async def _finish(row: Dict[str, Any], answers: Dict[str, Any], prefix: str) -> str:
    tid = row["tenant_id"]
    _update(tid, {"status": "done", "answers": answers, "completed_at": _now().isoformat(), "step": None})
    from vula.api import tenants
    biz = _business_name(tid)
    lines = [f"• {s['label']}: {answers.get(s['key'])}" for s in STEPS
             if answers.get(s["key"]) not in (None, "", "skipped")]
    skipped = [s["label"] for s in STEPS if answers.get(s["key"]) in (None, "", "skipped")]
    text = (f"{prefix}\n\nThat's {biz} set up ✅\n\n" + "\n".join(lines)).strip()
    if skipped:
        text += "\n\nStill to fill in (Settings, any time): " + ", ".join(skipped)
    text += (f"\n\nYour dashboard: {tenants.dashboard_link(tid, 'home')}\n"
             "To sign in, open it and choose *Sign in with WhatsApp* with this number.\n\n"
             "Until the business's own WhatsApp number is connected, keep messaging me here: ask me "
             "anything about the business, or send invoices, slips and documents. To connect your own "
             f"number: {tenants.dashboard_link(tid, 'settings')} › WhatsApp.")
    try:
        from vula.integrations.platform_support import alert_operator
        await alert_operator(f"{biz} ({tid}) finished its WhatsApp setup: {len(lines)} of {len(STEPS)} answered.")
    except Exception:
        pass
    return text


def _intro(row: Dict[str, Any]) -> str:
    biz = _business_name(row["tenant_id"])
    return (f"Welcome to Vula, {_first_name(row.get('owner_name'))}! 👋 Let's set up {biz} together: "
            f"{len(STEPS)} short questions, one at a time. Answer each in a message. Reply *skip* to "
            "leave one for later, or *stop* to pause and *start* to carry on.")


async def interview(row: Dict[str, Any], *, text: str = "", media: Optional[Dict[str, str]] = None) -> str:
    """The reply to one message from the owner while their business is being set up."""
    tid = row["tenant_id"]
    status = row.get("status")
    step_key = row.get("step") or STEPS[0]["key"]
    step = _BY_KEY.get(step_key, STEPS[0])
    _update(tid, {"last_message_at": _now().isoformat()})
    if status == "invited":
        _update(tid, {"status": "in_progress", "step": step_key})
        return f"{_intro(row)}\n\n{_ask(step_key)}"
    if status == "paused":
        _update(tid, {"status": "in_progress"})
        return f"Welcome back 👋 Let's carry on.\n\n{_ask(step_key)}"
    if text and _STOP_RE.match(text):
        _update(tid, {"status": "paused"})
        return ("Paused ✅ — everything so far is saved. Reply *start* when you want to carry on. "
                "Until then you can ask me anything about the business here.")
    answers = row.get("answers") or {}
    if text and _SKIP_RE.match(text):
        if step["kind"] in ("documents", "team") and answers.get(step["key"]):
            return await _advance(row, answers, "Got it.")
        return await _advance(row, _answers_with(row, step["key"], "skipped"), "Skipped — you can add it later in Settings.")
    if step["kind"] == "logo":
        if media and (media.get("mime") or "").startswith("image/"):
            back, why = await _save_logo(row, media["id"], media.get("mime") or "image/jpeg")
            if back:
                return await _advance(row, _answers_with(row, "logo", back), "Logo saved ✅")
            return why or "Send it again?"
        return "Please send the logo as a photo, or reply *skip*."
    if step["kind"] == "documents":
        if text and _DONE_RE.match(text):
            return await _advance(row, answers if answers.get("documents") else _answers_with(row, "documents", "skipped"),
                                  "Thanks ✅")
        return "Send the documents as files or photos, one at a time, or reply *done*."
    if step["kind"] == "team":
        if text and _DONE_RE.match(text):
            return await _advance(row, answers if answers.get("team") else _answers_with(row, "team", "skipped"),
                                  "Thanks ✅")
        back, why = await _add_member(row, text)
        if not back:
            return why or "Send it again?"
        team = [t for t in str(answers.get("team") or "").split("; ") if t and t != "skipped"] + [back]
        _update(tid, {"answers": _answers_with(row, "team", "; ".join(team))})
        return (f"Added ✅ {back}. They can message me here too, and sign in to the dashboard with "
                "WhatsApp. Send the next person, or reply *done*.")
    if not text:
        return _ask(step_key)
    back, why = await _save(row, step, text)
    if not back:
        return why or f"Send it again?\n\n{_ask(step_key)}"
    return await _advance(row, _answers_with(row, step["key"], back), f"Saved ✅ {step['label']}: {back}")


def note_document(tenant_id: str, filename: str) -> None:
    """A document the owner sent during the documents step (handled by the normal filing)."""
    try:
        row = get(tenant_id)
        if row and row.get("step") == "documents" and row.get("status") == "in_progress":
            docs = [d for d in str((row.get("answers") or {}).get("documents") or "").split("; ")
                    if d and d != "skipped"]
            _update(tenant_id, {"answers": _answers_with(row, "documents", "; ".join(docs + [filename or "a document"])),
                                "last_message_at": _now().isoformat()})
    except Exception as exc:
        log.debug("onboarding document note skipped: %s", exc)


# ── Messages to Vula's own number ──────────────────────────────────────────────────────────────

_LEADS: Dict[str, float] = {}


def _platform_tenant_for(phone: str) -> Optional[str]:
    """The business this team member works with through Vula's number: one set up here, with no
    WhatsApp number of its own, where they're on the team (owner, manager, staff…). Newest first."""
    target = digits(phone)
    rows = (_db().table(TABLE).select("tenant_id,invited_at").execute().data or [])
    rows.sort(key=lambda r: r.get("invited_at") or "", reverse=True)
    from vula import team_index
    for r in rows:
        tid = r["tenant_id"]
        try:
            m = team_index.member_for_phone(tid, target)
        except Exception:
            m = None
        if m and not _has_own_number(tid):
            return tid
    return None


def _message_parts(msg: Dict[str, Any]) -> Tuple[str, Optional[Dict[str, str]]]:
    t = msg.get("type")
    if t == "text":
        return (msg.get("text") or {}).get("body", "").strip(), None
    if t == "button":
        b = msg.get("button") or {}
        return (b.get("text") or b.get("payload") or "").strip(), None
    if t == "interactive":
        i = msg.get("interactive") or {}
        r = i.get("button_reply") or i.get("list_reply") or {}
        return (r.get("title") or r.get("id") or "").strip(), None
    if t in ("image", "document"):
        m = msg.get(t) or {}
        return (m.get("caption") or "").strip(), {"id": m.get("id") or "", "mime": m.get("mime_type") or "",
                                                   "type": t, "filename": m.get("filename") or ""}
    return "", {"type": t or "", "id": "", "mime": ""}


def _setup_row(phone: str) -> Optional[Dict[str, Any]]:
    rows = _for_phone(phone)
    return next((r for r in rows if r.get("status") in ACTIVE), None) \
        or next((r for r in rows if r.get("status") == "paused"), None)


def decide(phone: str, msg: Dict[str, Any]) -> Tuple[str, Optional[str]]:
    """Where a message to Vula's own number goes (database reads only, quick; run it off the
    event loop):
      ("setup", tenant_id)     a question of the business's setup — answer(), in the background
      ("as_tenant", tenant_id) the normal pipeline, as that business, replies from Vula's number
      ("lead", None)           someone not on any business set up here — answer()
    Never raises: a failed read is a lead (one polite reply), never another business's chat."""
    try:
        text, media = _message_parts(msg)
        row = _setup_row(phone)
        if row and not (text and _LOGIN_RE.match(text)):
            if row.get("status") == "paused" and not (text and _START_RE.match(text)):
                return "as_tenant", row["tenant_id"]   # paused: Vula as usual, until they say start
            step = row.get("step")
            logo_photo = step == "logo" and media and (media.get("mime") or "").startswith("image/")
            if media and not logo_photo:
                # documents, photos of slips, voice notes: the normal pipeline, as this business
                if (step == "documents" and row.get("status") == "in_progress"
                        and media.get("type") in ("image", "document")):
                    note_document(row["tenant_id"], media.get("filename") or "a photo")
                return "as_tenant", row["tenant_id"]
            return "setup", row["tenant_id"]
        tid = (row or {}).get("tenant_id") or _platform_tenant_for(phone)
        return ("as_tenant", tid) if tid else ("lead", None)
    except Exception as exc:
        log.warning("Vula-number message not routed: %s", exc)
        return "lead", None


_LOCKS: Dict[str, Any] = {}


async def answer(phone: str, msg: Dict[str, Any], action: str) -> None:
    """Reply to a "setup" or "lead" message (see decide). One message at a time per person, so two
    quick answers never land on the same question. Never raises."""
    import asyncio
    lock = _LOCKS.setdefault(digits(phone), asyncio.Lock())
    async with lock:
        try:
            text, media = _message_parts(msg)
            if action == "setup":
                row = await asyncio.to_thread(_setup_row, phone)
                if row:
                    await _reply(phone, await interview(row, text=text, media=media))
                    return
            await _lead(phone, text)
        except Exception as exc:
            log.warning("Vula-number reply failed: %s", exc)


async def _reply(phone: str, text: str) -> None:
    from vula.integrations import platform_support as ps
    from vula import turns as _turns
    _turns.reply(text, kind="onboarding", to=phone)
    await ps.send_text(phone, text)


async def _lead(phone: str, text: str) -> None:
    """Someone who isn't on any business set up through Vula's number: one polite reply a day,
    and Ian is told (the number and the first words only)."""
    now = time.time()
    if now - _LEADS.get(digits(phone), 0) < 24 * 3600:
        return
    _LEADS[digits(phone)] = now
    await _reply(phone, (
        "Hi! 👋 This is Vula, the WhatsApp assistant for South African businesses. If your business "
        "already uses Vula, message your business's own WhatsApp number. To set your business up on "
        "Vula, reply with your name and your business's name and the Vula team will be in touch."))
    try:
        from vula.integrations.platform_support import alert_operator
        await alert_operator(f"New message to Vula's own number from {digits(phone)}: {(text or '(no text)')[:200]}")
    except Exception:
        pass


# ── The reminder ───────────────────────────────────────────────────────────────────────────────

async def remind_due() -> Dict[str, int]:
    """One reminder (the welcome template again) to each business still setting up whose owner
    has been quiet for 24 hours. Never a second one: Master can nudge again by hand."""
    sent = 0
    try:
        rows = (_db().table(TABLE).select("*").in_("status", list(ACTIVE)).execute().data or [])
    except Exception as exc:
        log.info("onboarding reminders skipped: %s", exc)
        return {"reminded": 0}
    cutoff = _now() - REMIND_AFTER
    for r in rows:
        if r.get("reminded_at"):
            continue
        last = r.get("last_message_at") or r.get("invited_at")
        try:
            when = datetime.fromisoformat(str(last).replace("Z", "+00:00"))
        except Exception:
            continue
        if when > cutoff:
            continue
        if await send_welcome(r) == "sent":
            _update(r["tenant_id"], {"reminded_at": _now().isoformat()})
            sent += 1
    return {"reminded": sent}
