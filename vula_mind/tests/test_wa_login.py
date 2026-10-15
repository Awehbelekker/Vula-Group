"""Log in to the dashboard with WhatsApp (vula/api/wa_login.py, 15 Oct 2026).

QR/button: the page gets a code, the team member sends "Login K7F4QX" to the business's number,
taps Approve, and the page receives a one-time Supabase token. 6-digit code: sent to the number's
WhatsApp and typed in. Strangers, wrong codes, expired codes, a second use and a different
person's Approve are all refused; nothing is ever said about whether a number is on a team."""
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from vula.api import users as users_api
from vula.api import wa_login as wl

MATT = {"id": "m1", "tenant_id": "seedleme", "name": "Matt Wilkinson", "whatsapp": "27662545068",
        "role": "owner", "email": "seedleme@gmail.com"}
RIO = {"id": "m2", "tenant_id": "seedleme", "name": "Rio Koertze", "whatsapp": "0662016358",
       "role": "manager", "email": None}


class _Q:
    def __init__(self, db, table):
        self.db, self.table, self.filters, self.op, self.vals = db, table, [], "select", None

    def select(self, *_a):
        return self

    def insert(self, row):
        self.op, self.vals = "insert", row
        return self

    def update(self, vals):
        self.op, self.vals = "update", vals
        return self

    def eq(self, col, val):
        self.filters.append(lambda r, c=col, v=val: r.get(c) == v)
        return self

    def in_(self, col, vals):
        self.filters.append(lambda r, c=col, v=vals: r.get(c) in v)
        return self

    def gte(self, col, val):
        self.filters.append(lambda r, c=col, v=val: str(r.get(c)) >= v)
        return self

    def limit(self, *_a):
        return self

    def execute(self):
        rows = self.db.tables.setdefault(self.table, [])
        if self.op == "insert":
            row = {"id": f"r{len(rows) + 1}", "status": "pending", "attempts": 0,
                   "created_at": datetime.now(timezone.utc).isoformat(), **self.vals}
            rows.append(row)
            return SimpleNamespace(data=[row])
        hit = [r for r in rows if all(f(r) for f in self.filters)]
        if self.op == "update":
            for r in hit:
                r.update(self.vals)
        return SimpleNamespace(data=hit)


class _DB:
    def __init__(self):
        self.tables = {"vula_whatsapp_accounts": [{"tenant_id": "seedleme", "phone_number": "+27 71 312 8891",
                                                   "status": "connected"}],
                       "vula_tenant_users": []}

    def table(self, name):
        return _Q(self, name)


@pytest.fixture
def env(monkeypatch):
    db = _DB()
    sent, buttons, users = [], [], {}
    monkeypatch.setattr(wl, "_db", lambda: db)
    monkeypatch.setattr("vula.api.tenants.tenant_for_host", lambda h: "seedleme" if "seedleme" in h else None)
    monkeypatch.setattr("vula.api.tenants.get_config", lambda tid, fresh=False: {"display_name": "SeedleMe"})
    team = [MATT, RIO]
    monkeypatch.setattr("vula.team_index.active_members", lambda tid: [dict(m) for m in team if m["tenant_id"] == tid])
    monkeypatch.setattr(wl, "_memberships", lambda phone, tid=None: [
        dict(m) for m in team if wl._digits(m["whatsapp"]) == wl._digits(phone) and (not tid or m["tenant_id"] == tid)])
    monkeypatch.setattr("vula.commerce.plan_limits.check_seat_quota", lambda tid: None)

    async def auth_admin(method, path, json=None):
        if path == "/users" and method == "POST":
            users[json["email"]] = f"u{len(users) + 1}"
            return SimpleNamespace(status_code=200, json=lambda: {"id": users[json["email"]]}, text="")
        if path == "/generate_link":
            return SimpleNamespace(status_code=200, json=lambda: {"hashed_token": "tok-" + json["email"]}, text="")
        return SimpleNamespace(status_code=404, json=lambda: {}, text="")

    async def find_user(email):
        return users.get(email)

    monkeypatch.setattr(users_api, "_auth_admin", auth_admin)
    monkeypatch.setattr(users_api, "_find_user_id", find_user)

    async def creds(tid):
        return {"token": "t", "phone_id": "p"}

    async def send_buttons(c, number, body, btns, image_url=None):
        buttons.append((number, body, btns))
        return True

    async def send_reply(to, text, tid="", **k):
        sent.append((to, text))
        return True

    from vula.api import whatsapp as wa
    monkeypatch.setattr(wa, "_get_tenant_wa_creds", creds)
    monkeypatch.setattr(wa, "_send_wa_buttons", send_buttons)
    monkeypatch.setattr(wa, "_send_reply", send_reply)
    return SimpleNamespace(db=db, sent=sent, buttons=buttons, users=users)


def _req(ua="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/141.0 Safari/537.36", ip="1.2.3.4"):
    return SimpleNamespace(headers={"user-agent": ua, "x-forwarded-for": ip}, client=None)


def test_device_label():
    assert wl.device_label(_req().headers["user-agent"]) == "Chrome on Windows"
    assert wl.device_label("Mozilla/5.0 (iPhone; CPU iPhone OS 18_0) AppleWebKit Version/18.0 Mobile Safari/604.1") == "Safari on iPhone"
    assert wl.LOGIN_RE.match("Login K7F4QX") and wl.LOGIN_RE.match("login: k7f4qx")
    assert not wl.LOGIN_RE.match("login to the dashboard please")


@pytest.mark.asyncio
async def test_qr_login_end_to_end_for_a_manager_without_an_email(env):
    s = await wl.start(wl.StartIn(host="seedleme.vula-ai.com"), _req())
    assert s["available"] and s["link"] == f"https://wa.me/27713128891?text=Login%20{s['code']}"
    assert s["qr_svg"].lstrip().startswith("<svg")
    assert (await wl.status(s["id"], s["secret"]))["status"] == "pending"

    # Rio sends the code from WhatsApp → Approve buttons naming the browser, nothing opened yet
    assert await wl.handle_inbound("seedleme", "27662016358", f"Login {s['code']}") is None
    number, body, btns = env.buttons[-1]
    assert number == "27662016358" and "Log in to SeedleMe's Vula dashboard on Chrome on Windows?" in body
    assert [b["title"] for b in btns] == ["Approve", "Not me"]
    assert (await wl.status(s["id"], s["secret"]))["status"] == "awaiting_approval"

    # someone else tapping the button does nothing
    assert "isn't yours" in await wl.handle_button("27662545068", btns[0]["id"])
    assert "Approved" in await wl.handle_button("27662016358", btns[0]["id"])
    done = await wl.status(s["id"], s["secret"])
    assert done["status"] == "approved" and done["token_hash"] == "tok-wa-27662016358@users.vula-ai.com"
    assert [(r["user_id"], r["tenant_id"], r["role"]) for r in env.db.tables["vula_tenant_users"]] == [
        ("u1", "seedleme", "staff")]
    assert any("Rio Koertze just logged in" in t for to, t in env.sent if to == "27662545068")   # owner told
    # the token is handed out once
    assert (await wl.status(s["id"], s["secret"]))["status"] == "used"
    with pytest.raises(HTTPException):
        await wl.status(s["id"], "wrong-secret")


@pytest.mark.asyncio
async def test_a_number_not_on_the_team_and_not_me(env):
    s = await wl.start(wl.StartIn(host="seedleme.vula-ai.com"), _req())
    reply = await wl.handle_inbound("seedleme", "27820000000", f"Login {s['code']}")
    assert "isn't on the team" in reply and not env.buttons
    assert (await wl.status(s["id"], s["secret"]))["status"] == "denied"

    s2 = await wl.start(wl.StartIn(host="seedleme.vula-ai.com"), _req())
    await wl.handle_inbound("seedleme", "27662545068", f"login {s2['code'].lower()}")
    no = env.buttons[-1][2][1]["id"]
    assert "Blocked" in await wl.handle_button("27662545068", no)
    assert (await wl.status(s2["id"], s2["secret"]))["status"] == "denied"


@pytest.mark.asyncio
async def test_expired_codes_and_unknown_addresses(env):
    s = await wl.start(wl.StartIn(host="seedleme.vula-ai.com"), _req())
    env.db.tables["vula_wa_login_requests"][-1]["expires_at"] = (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()
    assert "expired" in await wl.handle_inbound("seedleme", "27662545068", f"Login {s['code']}")
    assert (await wl.status(s["id"], s["secret"]))["status"] == "expired"
    # the shared dashboard has no business number → the page offers the code instead
    assert (await wl.start(wl.StartIn(host="vuladashboard.vercel.app"), _req()))["available"] is False
    assert await wl.handle_inbound("seedleme", "27662545068", "hello") is None


@pytest.mark.asyncio
async def test_six_digit_code(env, monkeypatch):
    codes = []

    async def deliver(tid, phone, code):
        codes.append((tid, phone, code))
        return True
    monkeypatch.setattr(wl, "_deliver_code", deliver)
    d = await wl.send_code(wl.CodeIn(phone="066 254 5068"), _req())
    assert d["sent"] and codes == [("seedleme", "27662545068", codes[0][2])]
    with pytest.raises(HTTPException) as bad:
        await wl.verify_code(wl.VerifyIn(id=d["id"], secret=d["secret"], code="000000" if codes[0][2] != "000000" else "111111"))
    assert "isn't right" in bad.value.detail
    ok = await wl.verify_code(wl.VerifyIn(id=d["id"], secret=d["secret"], code=codes[0][2]))
    assert ok["token_hash"] == "tok-seedleme@gmail.com"
    assert env.db.tables["vula_tenant_users"][0]["role"] == "owner"
    with pytest.raises(HTTPException):                      # single use
        await wl.verify_code(wl.VerifyIn(id=d["id"], secret=d["secret"], code=codes[0][2]))


@pytest.mark.asyncio
async def test_code_for_a_stranger_says_the_same_and_sends_nothing(env, monkeypatch):
    codes = []

    async def deliver(*a):
        codes.append(a)
        return True
    monkeypatch.setattr(wl, "_deliver_code", deliver)
    d = await wl.send_code(wl.CodeIn(phone="0820000000"), _req())
    assert d["message"] == wl._SENT["message"] and not codes
    with pytest.raises(HTTPException):
        await wl.verify_code(wl.VerifyIn(id=d["id"], secret=d["secret"], code="123456"))


@pytest.mark.asyncio
async def test_limits(env, monkeypatch):
    async def deliver(*a):
        return True
    monkeypatch.setattr(wl, "_deliver_code", deliver)
    for _ in range(wl.MAX_CODES_PER_PHONE):
        await wl.send_code(wl.CodeIn(phone="0662545068"), _req(ip="9.9.9.9"))
    with pytest.raises(HTTPException) as many:
        await wl.send_code(wl.CodeIn(phone="0662545068"), _req(ip="9.9.9.9"))
    assert many.value.status_code == 429
    d = await wl.send_code(wl.CodeIn(phone="0662016358"), _req(ip="8.8.8.8"))
    for _ in range(wl.MAX_CODE_ATTEMPTS):
        with pytest.raises(HTTPException):
            await wl.verify_code(wl.VerifyIn(id=d["id"], secret=d["secret"], code="999999x"))
    with pytest.raises(HTTPException) as locked:
        await wl.verify_code(wl.VerifyIn(id=d["id"], secret=d["secret"], code="999999"))
    assert "Too many wrong codes" in locked.value.detail


def test_auth_template_is_metas_shape():
    p = wl.auth_template_payload()
    assert p["category"] == "AUTHENTICATION" and p["name"] == "vula_login_code"
    assert {"type": "BODY", "add_security_recommendation": True} in p["components"]
    assert p["components"][2]["buttons"][0]["otp_type"] == "COPY_CODE"
