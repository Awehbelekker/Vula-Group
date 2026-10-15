"""
vula/api/wa_login.py — log in to the Vula dashboard with WhatsApp.

15 Oct 2026 (Ian: "we're using WhatsApp as the front of the front office, back office,
communications with clients and staff — would it not be simpler to log in as we do with
WhatsApp, with a QR code, or a PIN sent to the staff member's WhatsApp number?").

The staff member's WhatsApp number is already their identity on the team (vula_team_members).
Two ways to prove they hold it — no password to forget:

  QR / button   POST /v1/auth/wa/start → the page shows a QR (computer) or a button (phone) that
                opens WhatsApp with "Login K7F4QX" to the business's number. When Vula receives
                it from an active team member it replies "Log in on Chrome on Windows?
                [Approve] [Not me]". Approve → the page (polling GET /v1/auth/wa/status) gets
                its session. The Approve step stops someone showing a person THEIR code to get
                logged in as that person.
  6-digit code  POST /v1/auth/wa/code → the code goes to the number's WhatsApp (the shared
                "vula_login_code" authentication template, or plain text inside the 24-hour
                window); POST /v1/auth/wa/code/verify with it.

Either way the session is an ordinary Supabase one: the server asks Supabase's admin API for a
one-time sign-in token for the member's login (created if they have none — no password is ever
set) and the browser exchanges it (supabase.auth.verifyOtp). Everything after that is the same
as an email login. Codes expire in minutes, are stored hashed, are single-use, and attempts and
sends are limited per number and per address. Every route here is public by design (it's how
you get in) and returns nothing about whether a number is on a team.
"""
from __future__ import annotations

import hashlib
import logging
import re
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

log = logging.getLogger(__name__)
router = APIRouter(tags=["wa-login"])

_TABLE = "vula_wa_login_requests"
QR_TTL = timedelta(minutes=3)
CODE_TTL = timedelta(minutes=5)
MAX_CODE_ATTEMPTS = 5
MAX_CODES_PER_PHONE = 3          # per 15 minutes
MAX_STARTS_PER_IP = 20           # per 10 minutes
_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"     # no 0/O/1/I
LOGIN_RE = re.compile(r"^\s*(?:log\s*in|login|sign\s*in)\s*[:#-]?\s*([A-Za-z0-9]{6})\s*[.!]?\s*$", re.I)
AUTH_TEMPLATE = "vula_login_code"
SYNTHETIC_EMAIL_DOMAIN = "users.vula-ai.com"


# ── small helpers ───────────────────────────────────────────────────────────────────────

def _db():
    from vula.commerce import service
    return service._client()


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _h(*parts: str) -> str:
    return hashlib.sha256("|".join(parts).encode()).hexdigest()


def _digits(phone: str) -> str:
    from vula.team_index import digits_za
    return digits_za(phone or "")


def _parse_ts(v: Any) -> datetime:
    return datetime.fromisoformat(str(v).replace("Z", "+00:00"))


def device_label(user_agent: str) -> str:
    """'Chrome on Windows' — shown on the Approve message so the person can tell it's them."""
    ua = user_agent or ""
    browser = next((name for pat, name in (
        (r"Edg/", "Edge"), (r"OPR/|Opera", "Opera"), (r"SamsungBrowser", "Samsung Internet"),
        (r"Firefox/|FxiOS", "Firefox"), (r"Chrome/|CriOS", "Chrome"), (r"Safari/", "Safari"))
        if re.search(pat, ua)), "a browser")
    os_name = next((name for pat, name in (
        (r"iPhone|iPad", "iPhone"), (r"Android", "Android"), (r"Windows", "Windows"),
        (r"Mac OS X|Macintosh", "Mac"), (r"Linux", "Linux")) if re.search(pat, ua)), "")
    return f"{browser} on {os_name}" if os_name else browser


def _new_login_code() -> str:
    return "".join(secrets.choice(_ALPHABET) for _ in range(6))


def _business_number(tenant_id: Optional[str]) -> Optional[str]:
    """The WhatsApp number people message this business on (digits), for the wa.me link."""
    if not tenant_id:
        return None
    try:
        rows = (_db().table("vula_whatsapp_accounts").select("phone_number").eq("tenant_id", tenant_id)
                .eq("status", "connected").limit(1).execute().data or [])
    except Exception as exc:
        log.debug("business number lookup failed for %s: %s", tenant_id, exc)
        return None
    n = _digits((rows[0].get("phone_number") if rows else "") or "")
    return n or None


def _memberships(phone: str, tenant_id: Optional[str] = None) -> List[Dict[str, Any]]:
    """Active team rows for this WhatsApp number — on one business, or on any."""
    target = _digits(phone)
    if not target:
        return []
    if tenant_id:
        from vula.team_index import member_for_phone
        m = member_for_phone(tenant_id, target)
        return [{**m, "tenant_id": tenant_id}] if m else []
    rows = (_db().table("vula_team_members").select("id,tenant_id,name,whatsapp,role,email")
            .eq("active", True).execute().data or [])
    return [r for r in rows if _digits(r.get("whatsapp") or "") == target]


def _qr_svg(link: str) -> str:
    import io

    import segno
    buf = io.BytesIO()
    segno.make(link, error="m").save(buf, kind="svg", scale=6, border=2, dark="#111", light=None,
                                     xmldecl=False, svgns=True)
    return buf.getvalue().decode()


def _get(request_id: str) -> Optional[Dict[str, Any]]:
    try:
        rows = _db().table(_TABLE).select("*").eq("id", request_id).limit(1).execute().data or []
    except Exception:
        return None
    return rows[0] if rows else None


def _owned(request_id: str, secret: str) -> Dict[str, Any]:
    """The request, only for the browser holding its secret."""
    row = _get(request_id) if request_id else None
    if not row or not secret or not secrets.compare_digest(row["secret_hash"], _h(secret)):
        raise HTTPException(status_code=404, detail="That login has expired — start again.")
    return row


def _expired(row: Dict[str, Any]) -> bool:
    return _now() >= _parse_ts(row["expires_at"])


def _set(request_id: str, values: Dict[str, Any], *, only_status: Optional[str] = None) -> bool:
    """Update the request; with only_status, only if it is still in that state (no races)."""
    q = _db().table(_TABLE).update(values).eq("id", request_id)
    if only_status:
        q = q.eq("status", only_status)
    res = q.execute()
    return bool(res.data)


def _recent_count(column: str, value: str, minutes: int, kind: Optional[str] = None) -> int:
    since = (_now() - timedelta(minutes=minutes)).isoformat()
    q = _db().table(_TABLE).select("id").eq(column, value).gte("created_at", since)
    if kind:
        q = q.eq("kind", kind)
    return len(q.execute().data or [])


def _client_ip(request: Request) -> str:
    fwd = request.headers.get("x-forwarded-for", "")
    return (fwd.split(",")[0].strip() if fwd else (request.client.host if request.client else "")) or "?"


# ── the session: a one-time Supabase sign-in token for the member's login ──────────────────

async def _login_for(member: Dict[str, Any], tenant_id: str) -> Dict[str, str]:
    """The member's Supabase user (created without a password if they have none), mapped to the
    business, and a one-time token the browser exchanges for a session. Raises HTTPException."""
    from vula.api import users as users_api
    email = (member.get("email") or "").strip().lower()
    if not email or "@" not in email:
        email = f"wa-{_digits(member.get('whatsapp') or '')}@{SYNTHETIC_EMAIL_DOMAIN}"
    user_id = await users_api._find_user_id(email)
    if not user_id:
        r = await users_api._auth_admin("POST", "/users", {
            "email": email, "email_confirm": True,
            "user_metadata": {"full_name": member.get("name") or email,
                              "whatsapp": _digits(member.get("whatsapp") or "")}})
        if r.status_code not in (200, 201):
            log.warning("WA login: couldn't create login for %s on %s: %s", member.get("id"), tenant_id, r.text[:200])
            raise HTTPException(status_code=502, detail="Couldn't open your login just now — try again.")
        user_id = r.json().get("id")
    _ensure_mapping(user_id, tenant_id, member.get("role") or "staff")
    r = await users_api._auth_admin("POST", "/generate_link", {"type": "magiclink", "email": email})
    if r.status_code not in (200, 201):
        log.warning("WA login: generate_link failed for %s: %s", tenant_id, r.text[:200])
        raise HTTPException(status_code=502, detail="Couldn't open your login just now — try again.")
    data = r.json()
    token_hash = data.get("hashed_token") or (data.get("properties") or {}).get("hashed_token")
    if not token_hash:
        raise HTTPException(status_code=502, detail="Couldn't open your login just now — try again.")
    return {"email": email, "token_hash": token_hash, "tenant_id": tenant_id}


def _ensure_mapping(user_id: str, tenant_id: str, team_role: str) -> None:
    """Give the login access to this business if it hasn't any yet — never downgrade an existing
    mapping (an owner or master stays one). A new seat is checked against the plan's limit."""
    db = _db()
    existing = (db.table("vula_tenant_users").select("role").eq("user_id", user_id)
                .eq("tenant_id", tenant_id).limit(1).execute().data or [])
    if existing:
        return
    from vula.commerce.plan_limits import PlanLimitError, check_seat_quota
    try:
        check_seat_quota(tenant_id)
    except PlanLimitError as exc:
        raise HTTPException(status_code=403, detail=str(exc))
    role = "owner" if team_role == "owner" else "staff"
    db.table("vula_tenant_users").insert({"user_id": user_id, "tenant_id": tenant_id, "role": role}).execute()


async def _issue(row: Dict[str, Any]) -> Dict[str, str]:
    """Spend an approved request (once) and return the session token."""
    if not _set(row["id"], {"status": "used", "used_at": _now().isoformat()}, only_status="approved"):
        raise HTTPException(status_code=409, detail="That login was already used — start again.")
    members = _memberships(row.get("phone") or "", row.get("tenant_id"))
    if not members:
        raise HTTPException(status_code=403, detail="This number isn't on the team any more.")
    out = await _login_for(members[0], members[0]["tenant_id"])
    await _alert_owners(members[0], row)
    return out


async def _alert_owners(member: Dict[str, Any], row: Dict[str, Any]) -> None:
    """Tell the business's owners someone logged in (not the owner about themselves). Best-effort."""
    try:
        from vula.api.whatsapp import _send_reply
        from vula.team_index import active_members
        tid = member["tenant_id"]
        me = _digits(member.get("whatsapp") or "")
        for o in active_members(tid):
            if (o.get("role") or "") != "owner" or _digits(o.get("whatsapp") or "") in ("", me):
                continue
            await _send_reply(o["whatsapp"], f"🔐 {member.get('name') or 'A team member'} just logged in to the "
                                             f"Vula dashboard ({row.get('device') or 'a browser'}).", tid)
    except Exception as exc:
        log.debug("login alert skipped: %s", exc)


# ── 1. QR / button ──────────────────────────────────────────────────────────────────────

class StartIn(BaseModel):
    host: Optional[str] = None


@router.post("/start")
async def start(body: StartIn, request: Request) -> Dict[str, Any]:
    """A QR login for the business this address belongs to: the code to send, the wa.me link,
    a QR of it, and the secret this browser polls with."""
    from vula.api.tenants import tenant_for_host
    ip = _client_ip(request)
    if _recent_count("ip", ip, 10) >= MAX_STARTS_PER_IP:
        raise HTTPException(status_code=429, detail="Too many tries — wait a few minutes.")
    tenant_id = tenant_for_host(body.host or "") if body.host else None
    number = _business_number(tenant_id)
    if not number:
        return {"available": False,
                "message": "Use your WhatsApp number below — we'll send you a code."}
    secret = secrets.token_urlsafe(32)
    for _ in range(5):
        code = _new_login_code()
        try:
            row = _db().table(_TABLE).insert({
                "kind": "qr", "tenant_id": tenant_id, "login_code": code, "secret_hash": _h(secret),
                "device": device_label(request.headers.get("user-agent", "")), "ip": ip,
                "expires_at": (_now() + QR_TTL).isoformat()}).execute().data[0]
            break
        except Exception as exc:                      # the one-in-a-billion code clash → try another
            log.debug("login code clash/insert retry: %s", exc)
    else:
        raise HTTPException(status_code=503, detail="Couldn't start a login just now — try again.")
    link = f"https://wa.me/{number}?text=Login%20{code}"
    return {"available": True, "id": row["id"], "secret": secret, "code": code, "link": link,
            "qr_svg": _qr_svg(link), "expires_in": int(QR_TTL.total_seconds())}


@router.get("/status")
async def status(id: str, secret: str) -> Dict[str, Any]:
    """Polled by the login page. On 'approved' it returns the session token, once."""
    row = _owned(id, secret)
    if row["status"] in ("pending", "awaiting_approval") and _expired(row):
        _set(row["id"], {"status": "expired"}, only_status=row["status"])
        return {"status": "expired"}
    if row["status"] == "approved":
        return {"status": "approved", **(await _issue(row))}
    return {"status": row["status"]}


async def handle_inbound(route_tenant: Optional[str], phone: str, text: str) -> Optional[str]:
    """A "Login K7F4QX" message to a business's number. Returns the text reply when the message
    was a login attempt (the Approve buttons are sent here), None when it wasn't one."""
    m = LOGIN_RE.match(text or "")
    if not m:
        return None
    code = m.group(1).upper()
    try:
        rows = (_db().table(_TABLE).select("*").eq("login_code", code).eq("kind", "qr")
                .in_("status", ["pending", "awaiting_approval"]).limit(1).execute().data or [])
    except Exception as exc:
        log.warning("WA login lookup failed: %s", exc)
        return "I couldn't check that login just now — try again in a moment."
    row = rows[0] if rows else None
    if not row or _expired(row):
        return "That login code has expired. Refresh the login page and scan the new code."
    tenant_id = row.get("tenant_id") or route_tenant
    if row.get("tenant_id") and route_tenant and row["tenant_id"] != route_tenant:
        return "That code is for a different business's dashboard — scan it again from that login page."
    members = _memberships(phone, tenant_id)
    if not members:
        _set(row["id"], {"status": "denied", "decided_at": _now().isoformat(), "phone": _digits(phone)})
        return ("This WhatsApp number isn't on the team for this dashboard, so I can't log you in. "
                "Ask the owner to add you on the Team page.")
    if row["status"] == "awaiting_approval" and row.get("phone") != _digits(phone):
        return "That code is already waiting for someone else's approval."
    _set(row["id"], {"status": "awaiting_approval", "phone": _digits(phone), "tenant_id": tenant_id})
    business = _business_name(tenant_id)
    sent = await _send_approve(tenant_id, phone, row["id"],
                               f"Log in to {business}'s Vula dashboard on {row.get('device') or 'a browser'}?\n\n"
                               "Only tap Approve if you just scanned the code yourself.")
    return None if sent else "I couldn't send the Approve button just now — scan the code again in a moment."


async def handle_button(phone: str, reply_id: str) -> Optional[str]:
    """walogin_ok:<id> / walogin_no:<id> from the Approve message."""
    ok = reply_id.startswith("walogin_ok:")
    request_id = reply_id.split(":", 1)[1] if ":" in reply_id else ""
    row = _get(request_id)
    if not row or row.get("phone") != _digits(phone):
        return "That login request isn't yours or has gone — nothing was opened."
    if row["status"] != "awaiting_approval" or _expired(row):
        return "That login has expired — scan a fresh code on the login page."
    if ok:
        _set(row["id"], {"status": "approved", "decided_at": _now().isoformat()}, only_status="awaiting_approval")
        return "✅ Approved — your dashboard is opening now."
    _set(row["id"], {"status": "denied", "decided_at": _now().isoformat()}, only_status="awaiting_approval")
    return ("🚫 Blocked — nobody was logged in. If you didn't scan a code yourself, someone may have "
            "shown you theirs; it's safe to ignore.")


def _business_name(tenant_id: Optional[str]) -> str:
    try:
        from vula.api.tenants import get_config
        return (get_config(tenant_id) or {}).get("display_name") or tenant_id or "the business"
    except Exception:
        return tenant_id or "the business"


async def _send_approve(tenant_id: Optional[str], phone: str, request_id: str, body: str) -> bool:
    from vula.api import whatsapp as wa
    creds = await wa._get_tenant_wa_creds(tenant_id) if tenant_id else None
    if not creds:
        return False
    return await wa._send_wa_buttons(creds, wa._wa_number(phone), body, [
        {"id": f"walogin_ok:{request_id}", "title": "Approve"},
        {"id": f"walogin_no:{request_id}", "title": "Not me"}])


# ── 2. the 6-digit code ─────────────────────────────────────────────────────────────────

class CodeIn(BaseModel):
    phone: str
    host: Optional[str] = None


class VerifyIn(BaseModel):
    id: str
    secret: str
    code: str


_SENT = {"sent": True, "message": "If that number is on a Vula team, a code is on its way to its WhatsApp."}


@router.post("/code")
async def send_code(body: CodeIn, request: Request) -> Dict[str, Any]:
    """Send a 6-digit code to a team member's WhatsApp. The answer is the same whether or not
    the number is on a team, so the page can't be used to find out who works where."""
    from vula.api.tenants import tenant_for_host
    ip = _client_ip(request)
    phone = _digits(body.phone)
    if len(phone) < 9:
        raise HTTPException(status_code=422, detail="Enter your WhatsApp number, e.g. 082 123 4567.")
    if _recent_count("ip", ip, 10) >= MAX_STARTS_PER_IP or \
            _recent_count("phone", phone, 15, kind="code") >= MAX_CODES_PER_PHONE:
        raise HTTPException(status_code=429, detail="Too many codes — wait 15 minutes and try again.")
    tenant_id = tenant_for_host(body.host or "") if body.host else None
    members = _memberships(phone, tenant_id)
    secret = secrets.token_urlsafe(32)
    row = _db().table(_TABLE).insert({
        "kind": "code", "tenant_id": (members[0]["tenant_id"] if members else tenant_id), "phone": phone,
        "secret_hash": _h(secret), "device": device_label(request.headers.get("user-agent", "")), "ip": ip,
        "status": "pending" if members else "denied",
        "expires_at": (_now() + CODE_TTL).isoformat()}).execute().data[0]
    if members:
        code = f"{secrets.randbelow(1_000_000):06d}"
        _set(row["id"], {"otp_hash": _h(row["id"], code)})
        if not await _deliver_code(members[0]["tenant_id"], phone, code):
            log.warning("WA login code not delivered to %s on %s", phone[-4:], members[0]["tenant_id"])
    return {**_SENT, "id": row["id"], "secret": secret, "expires_in": int(CODE_TTL.total_seconds())}


async def _deliver_code(tenant_id: str, phone: str, code: str) -> bool:
    """The approved authentication template (works any time); plain text if it isn't approved
    yet and the person messaged in the last 24 hours."""
    from vula.api import whatsapp as wa
    creds = await wa._get_tenant_wa_creds(tenant_id)
    if creds:
        import httpx
        payload = {"messaging_product": "whatsapp", "to": wa._wa_number(phone), "type": "template",
                   "template": {"name": AUTH_TEMPLATE, "language": {"code": "en"}, "components": [
                       {"type": "body", "parameters": [{"type": "text", "text": code}]},
                       {"type": "button", "sub_type": "url", "index": "0",
                        "parameters": [{"type": "text", "text": code}]}]}}
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.post(f"https://graph.facebook.com/v19.0/{creds['phone_id']}/messages",
                                         headers={"Authorization": f"Bearer {creds['token']}"}, json=payload)
            if resp.is_success:
                return True
            log.info("login code template not sent (%s) — trying plain text", resp.text[:160])
        except Exception as exc:
            log.info("login code template failed: %s", exc)
    return await wa._send_reply(phone, f"{code} is your Vula login code. It expires in 5 minutes — "
                                       "don't share it with anyone.", tenant_id)


@router.post("/code/verify")
async def verify_code(body: VerifyIn) -> Dict[str, Any]:
    row = _owned(body.id, body.secret)
    if row["kind"] != "code" or row["status"] != "pending" or _expired(row) or not row.get("otp_hash"):
        raise HTTPException(status_code=400, detail="That code has expired — ask for a new one.")
    if row["attempts"] >= MAX_CODE_ATTEMPTS:
        _set(row["id"], {"status": "expired"}, only_status="pending")
        raise HTTPException(status_code=400, detail="Too many wrong codes — ask for a new one.")
    _set(row["id"], {"attempts": row["attempts"] + 1})
    if not secrets.compare_digest(row["otp_hash"], _h(row["id"], re.sub(r"\D", "", body.code or ""))):
        raise HTTPException(status_code=400, detail="That code isn't right — check WhatsApp and try again.")
    if not _set(row["id"], {"status": "approved", "decided_at": _now().isoformat()}, only_status="pending"):
        raise HTTPException(status_code=409, detail="That code was already used — ask for a new one.")
    return {"status": "approved", **(await _issue({**row, "status": "approved"}))}


# ── the authentication template (submitted with the standard set) ─────────────────────────

def auth_template_payload() -> Dict[str, Any]:
    """Meta's authentication template: fixed wording, a security note, an expiry footer and a
    Copy code button. Shared by name across the WhatsApp account, like vula_notification."""
    return {"name": AUTH_TEMPLATE, "language": "en", "category": "AUTHENTICATION", "components": [
        {"type": "BODY", "add_security_recommendation": True},
        {"type": "FOOTER", "code_expiration_minutes": int(CODE_TTL.total_seconds() // 60)},
        {"type": "BUTTONS", "buttons": [{"type": "OTP", "otp_type": "COPY_CODE", "text": "Copy code"}]}]}
