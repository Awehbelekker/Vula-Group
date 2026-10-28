"""Express pay: a tap opens one page (amount, tip, Pay) and goes straight to PayFast; the slip on
WhatsApp is an optional last step. Same claim, states and confirmation as the WhatsApp flow."""
import re

import pytest
from fastapi.testclient import TestClient

from tests.tap_fakes import Clock, FakeGateway, FakeMessenger, MemoryRepo
from vula.tap.express import COOKIE, ExpressError
from vula.tap.service import REF_PREFIX, TapConfig, TapService

T = "tenant-a"
CUST = "+27 82 111 4482"


@pytest.fixture
def env():
    clock = Clock()
    repo = MemoryRepo(clock)
    msg, gw = FakeMessenger(), FakeGateway()
    cfg = TapConfig(pepper="pep", public_base_url="https://api.test",
                    encrypt=lambda s: "enc:" + s, decrypt=lambda s: s[4:], clock=clock)
    svc = TapService(repo, msg, gw, cfg)
    tag = repo.add_tag(T, "coach-sipho", bound_id="coach")
    repo.rules[(T, "coach")] = {"staff_share_bp": 7000, "tip_rule": "direct"}
    repo.team[(T, "coach")] = ["27800000001"]
    repo.team[(T, None)] = ["27800000001"]
    return svc, repo, msg, gw, tag, clock


def bill(svc, tag, cents=50000, **kw):
    return svc.create_bill(tenant_id=T, tag_id=tag["id"], amount_cents=cents, description="2 beers",
                           staff_id="coach", created_by="u1", **kw)


async def pay(svc, cookie="browser-1", **kw):
    v = svc.express.view("coach-sipho", cookie)
    return v, await svc.express.checkout("coach-sipho", v["token"], cookie, **kw)


def itn(sid, cents, paid=True):
    return {"reference": REF_PREFIX + sid, "paid": paid, "amount_cents": cents, "pf_payment_id": "PF1",
            "fee_cents": 0}


def only_session(repo, state=None):
    ss = [s for s in repo.sessions.values() if state is None or s["state"] == state]
    assert len(ss) == 1
    return ss[0]


# ── the page ─────────────────────────────────────────────────────────────────

def test_page_shows_the_bill_and_mints_a_token(env):
    svc, repo, *_ , tag, _ = env
    bill(svc, tag)
    v = svc.express.view("coach-sipho", "browser-1")
    assert (v["kind"], v["bill_cents"], v["description"], v["staff"]) == ("bill", 50000, "2 beers", "Sipho")
    assert v["token"] and v["tip_presets"] == [10, 15, 20]
    assert not repo.sessions                                  # looking claims nothing


def test_page_kinds(env):
    svc, repo, *_ , tag, _ = env
    assert svc.express.view("coach-sipho", "b")["kind"] == "no_bill"
    assert svc.express.view("nope", "b") is None
    repo.add_tag(T, "guard-1", mode="quick_tip", bound_id="guard")
    assert svc.express.view("guard-1", "b")["kind"] == "quick_tip"
    repo.add_tag(T, "open-1", allow_open_amount=True)
    assert svc.express.view("open-1", "b")["kind"] == "open_amount"
    bill(svc, tag, customer_phone=CUST)                       # sent to a number: WhatsApp proves it
    assert svc.express.view("coach-sipho", "b")["kind"] == "whatsapp"


# ── paying ───────────────────────────────────────────────────────────────────

async def test_tap_page_to_paid_with_tip(env):
    svc, repo, msg, gw, tag, _ = env
    b = bill(svc, tag)
    _, url = await pay(svc, tip="10")
    s = only_session(repo)
    assert url == f"https://pay.example/checkout/{s['id']}"
    assert (s["bill_cents"], s["tip_cents"], s["state"], s["payer_enc"]) == (50000, 5000, "awaiting_payment", None)
    assert repo.bills[b["id"]]["status"] == "claimed"
    gw.itn = itn(s["id"], 55000)
    assert await svc.confirm_payment(T, {}, b"", {}) == "paid"
    assert repo.bills[b["id"]]["status"] == "paid"
    assert sum(l["cents"] for l in repo.ledger if l["cents"] > 0) == 55000
    alert = msg.to("27800000001", "text")[0][3]
    assert "R 500.00" in alert and "R 50.00 tip" in alert and "Paid by tap" in alert
    assert not [m for m in msg.sent if m[2] not in ("27800000001",)]    # no number, so no slip yet


async def test_another_phone_is_locked_out_but_the_same_browser_can_retry(env):
    svc, repo, *_ , tag, _ = env
    bill(svc, tag)
    early = svc.express.view("coach-sipho", "browser-2")       # opened the page first, paid second
    await pay(svc, "browser-1", tip="0")
    assert svc.express.view("coach-sipho", "browser-2")["kind"] == "locked"
    with pytest.raises(ExpressError, match="another phone"):
        await svc.express.checkout("coach-sipho", early["token"], "browser-2")
    _, url = await pay(svc, "browser-1", tip="15")            # backed out, tapped again
    live = only_session(repo, "awaiting_payment")
    assert live["tip_cents"] == 7500 and url.endswith(live["id"])


async def test_the_page_token_works_once(env):
    svc, repo, *_ , tag, _ = env
    bill(svc, tag)
    v, _ = await pay(svc)
    with pytest.raises(ExpressError, match="expired"):
        await svc.express.checkout("coach-sipho", v["token"], "browser-1")


async def test_backing_out_frees_the_bill_at_once(env):
    svc, repo, *_ , tag, _ = env
    b = bill(svc, tag)
    await pay(svc)
    s = only_session(repo)
    assert svc.express.cancel(s["id"], "browser-2") is False          # not your payment
    assert svc.express.cancel(s["id"], "browser-1") is True
    assert repo.bills[b["id"]]["status"] == "open" and repo.sessions[s["id"]]["state"] == "cancelled"


async def test_walking_away_frees_the_bill_instead_of_reminders(env):
    svc, repo, *_ , tag, clock = env
    b = bill(svc, tag)
    await pay(svc)
    clock.t += __import__("datetime").timedelta(minutes=11)
    await svc.sweep(T)
    assert repo.bills[b["id"]]["status"] == "open"            # no number to remind: back to open


async def test_a_bad_custom_tip_releases_the_bill(env):
    svc, repo, *_ , tag, _ = env
    b = bill(svc, tag)
    with pytest.raises(ExpressError, match="amount"):
        await pay(svc, tip="custom", custom_tip="lots")
    assert repo.bills[b["id"]]["status"] == "open"
    with pytest.raises(ExpressError, match="tips shown"):
        await pay(svc, tip="37")
    assert repo.bills[b["id"]]["status"] == "open"


async def test_no_payfast_account_says_so_and_releases(env):
    svc, repo, _, gw, tag, _ = env
    b = bill(svc, tag)

    async def none(**kw):
        return None
    gw.create_checkout = none
    with pytest.raises(ExpressError, match="aren't set up"):
        await pay(svc)
    assert repo.bills[b["id"]]["status"] == "open"


async def test_car_guard_tip_only(env):
    svc, repo, msg, gw, tag, _ = env
    repo.add_tag(T, "guard-1", mode="quick_tip", bound_id="coach")
    v = svc.express.view("guard-1", "b")
    await svc.express.checkout("guard-1", v["token"], "b", amount="10")
    s = only_session(repo)
    assert (s["bill_cents"], s["tip_cents"], s["staff_ref"]) == (0, 1000, "coach")
    gw.itn = itn(s["id"], 1000)
    assert await svc.confirm_payment(T, {}, b"", {}) == "paid"


async def test_customer_enters_the_amount(env):
    svc, repo, *_ = env
    repo.add_tag(T, "open-1", allow_open_amount=True, bound_id="coach")
    v = svc.express.view("open-1", "b")
    await svc.express.checkout("open-1", v["token"], "b", amount="250", tip="15")
    s = only_session(repo)
    assert (s["bill_cents"], s["tip_cents"]) == (25000, 3750)


# ── the slip, on request ─────────────────────────────────────────────────────

async def test_slip_requested_before_the_payment_is_confirmed(env):
    svc, repo, msg, gw, tag, _ = env
    bill(svc, tag)
    await pay(svc)
    s = only_session(repo)
    assert svc.express.slip_link(s["id"], "browser-2") is None      # only the browser that paid
    link = svc.express.slip_link(s["id"], "browser-1")
    assert link.startswith("https://wa.me/27737815979?text=SLIP%20")
    text = re.search(r"text=(.+)$", link).group(1).replace("%20", " ")
    assert await svc.handle_text(T, CUST, text) is True
    assert "as soon as the payment is confirmed" in msg.to(CUST, "text")[-1][3]
    gw.itn = itn(s["id"], 50000)
    await svc.confirm_payment(T, {}, b"", {})
    assert any("Paid R 500.00" in m[3] for m in msg.to(CUST, "text"))


async def test_slip_requested_after_payment_and_guarded(env):
    svc, repo, msg, gw, tag, _ = env
    bill(svc, tag)
    await pay(svc)
    s = only_session(repo)
    gw.itn = itn(s["id"], 50000)
    await svc.confirm_payment(T, {}, b"", {})
    text = re.search(r"text=(.+)$", svc.express.slip_link(s["id"], "browser-1")).group(1).replace("%20", " ")
    await svc.handle_text(T, CUST, text)
    assert any("Paid R 500.00" in m[3] for m in msg.to(CUST, "text"))
    other = "+27 83 222 9999"
    await svc.handle_text(T, other, text)                      # someone else with the same link
    assert "couldn't find" in msg.to(other, "text")[-1][3]
    await svc.handle_text(T, other, f"SLIP {s['id']}.{'0' * 16}")     # forged
    assert "couldn't find" in msg.to(other, "text")[-1][3]
    n = len(msg.to(CUST, "text"))
    await svc.handle_text(T, CUST, text)                       # the payer asks again: resent
    assert len(msg.to(CUST, "text")) == n + 1 and "Paid R 500.00" in msg.to(CUST, "text")[-1][3]


async def test_knowing_the_payment_id_is_not_enough_for_its_slip(env):
    svc, repo, msg, gw, tag, _ = env
    bill(svc, tag)
    await pay(svc)
    s = only_session(repo)
    import hashlib
    import hmac
    guess = hmac.new(b"pep", f"slip:{s['id']}".encode(), hashlib.sha256).hexdigest()[:16]   # old, id-only MAC
    await svc.handle_text(T, "+27 83 222 9999", f"SLIP {s['id']}.{guess}")
    assert repo.sessions[s["id"]]["payer_enc"] is None          # not linked to the stranger


# ── HTTP ─────────────────────────────────────────────────────────────────────

@pytest.fixture
def http(env, monkeypatch):
    from vula.api.server import app
    from vula.tap import api as tap_api
    svc, repo, msg, gw, tag, clock = env
    svc.cfg.enabled = lambda t: True
    monkeypatch.setattr(tap_api, "get_service", lambda: svc)
    monkeypatch.setattr(tap_api.settings, "tap_express", True)
    return TestClient(app, follow_redirects=False), svc, repo, tag


def test_http_tap_page_then_pay(http):
    client, svc, repo, tag = http
    bill(svc, tag)
    r = client.get("/t/coach-sipho")
    assert r.status_code == 200 and "R 500.00" in r.text and "Add a tip?" in r.text
    assert COOKIE in r.headers.get("set-cookie", "") and "HttpOnly" in r.headers["set-cookie"]
    token = re.search(r"name=token value='([^']+)'", r.text).group(1)
    r = client.post("/t/coach-sipho/pay", data={"token": token, "tip": "10"}, cookies={COOKIE: "c1"})
    assert r.status_code == 303 and r.headers["location"].startswith("https://pay.example/checkout/")
    s = only_session(repo)
    assert s["tip_cents"] == 5000
    done = client.get(f"/v1/tap/done/{s['id']}", cookies={COOKIE: "c1"})
    assert "Get my slip on WhatsApp" in done.text and "wa.me" in done.text
    assert "wa.me" not in client.get(f"/v1/tap/done/{s['id']}", cookies={COOKIE: "c2"}).text
    client.get(f"/v1/tap/cancelled/{s['id']}", cookies={COOKIE: "c1"})
    assert repo.sessions[s["id"]]["state"] == "cancelled"


def test_http_errors_reshow_the_page(http):
    client, svc, repo, tag = http
    bill(svc, tag)
    r = client.post("/t/coach-sipho/pay", data={"token": "x", "tip": "0"})            # no cookie
    assert r.status_code == 200 and "allow cookies" in r.text
    r = client.post("/t/coach-sipho/pay", data={"token": "x", "tip": "0"}, cookies={COOKIE: "c1"})
    assert "expired" in r.text and "name=token" in r.text      # fresh token to try again


def test_http_car_guard_preset_buttons_are_cents(http):
    client, svc, repo, tag = http
    repo.add_tag(T, "guard-1", mode="quick_tip", bound_id="coach")
    r = client.get("/t/guard-1", cookies={COOKIE: "c1"})
    token = re.search(r"name=token value='([^']+)'", r.text).group(1)
    r = client.post("/t/guard-1/pay", data={"token": token, "amount": "1000"}, cookies={COOKIE: "c1"})
    assert r.status_code == 303 and only_session(repo)["tip_cents"] == 1000


# ── after paying: the printed slip opens by itself ───────────────────────────

async def test_status_opens_the_printed_slip_for_the_payer_only(env):
    svc, repo, msg, gw, tag, _ = env
    svc.cfg.receipt_secret, svc.cfg.receipt_base_url = "rs", "https://dash.test"
    bill(svc, tag)
    await pay(svc)
    s = only_session(repo)
    assert svc.express.status(s["id"], "browser-1") == {"paid": False, "receipt_url": None, "failed": False}
    gw.itn = itn(s["id"], 50000)
    await svc.confirm_payment(T, {}, b"", {})
    st = svc.express.status(s["id"], "browser-1")
    assert st["paid"] and st["receipt_url"].startswith("https://dash.test/r/")
    assert svc.express.status(s["id"], "browser-2") == {"paid": False, "receipt_url": None}


async def test_status_reports_a_failed_payment(env):
    svc, repo, msg, gw, tag, _ = env
    bill(svc, tag)
    await pay(svc)
    s = only_session(repo)
    gw.itn = itn(s["id"], 50000, paid=False)
    await svc.confirm_payment(T, {}, b"", {})
    assert svc.express.status(s["id"], "browser-1")["failed"] is True


def test_http_done_page_polls_then_opens_the_slip(http):
    client, svc, repo, tag = http
    bill(svc, tag)
    r = client.get("/t/coach-sipho", cookies={COOKIE: "c1"})
    token = re.search(r"name=token value='([^']+)'", r.text).group(1)
    client.post("/t/coach-sipho/pay", data={"token": token, "tip": "0"}, cookies={COOKIE: "c1"})
    s = only_session(repo)
    page = client.get(f"/v1/tap/done/{s['id']}", cookies={COOKIE: "c1"}).text
    assert f"data-status='/v1/tap/done/{s['id']}/status'" in page and "location.replace" in page
    st = client.get(f"/v1/tap/done/{s['id']}/status", cookies={COOKIE: "c1"})
    assert st.json()["paid"] is False and st.headers["cache-control"] == "no-store"


def test_pay_pages_send_a_strict_content_security_policy(http):
    client, svc, repo, tag = http
    bill(svc, tag)
    r = client.get("/t/coach-sipho")
    csp = r.headers["content-security-policy"]
    nonce = re.search(r"'nonce-([^']+)'", csp).group(1)
    assert f"<script nonce='{nonce}'>" in r.text                    # the page's own script still runs
    assert "frame-ancestors 'none'" in csp and "default-src 'none'" in csp and "unsafe-eval" not in csp
    assert "form-action 'self' https://*.payfast.co.za https://*.payfast.io" in csp
    assert r.headers["x-frame-options"] == "DENY" and r.headers["x-content-type-options"] == "nosniff"
    assert r.headers["cache-control"] == "no-store"
    assert nonce not in client.get("/t/coach-sipho").headers["content-security-policy"]   # fresh per response
    plain = client.get("/t/nope")                                   # the plain "Tag not verified" page too
    assert plain.status_code == 404 and "script-src 'none'" in plain.headers["content-security-policy"]
