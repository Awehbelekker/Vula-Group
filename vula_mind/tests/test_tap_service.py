"""Tap-to-pay service flows against in-memory fakes."""
import re

import pytest

from tests.tap_fakes import Clock, FakeGateway, FakeMessenger, MemoryRepo
from vula.tap.service import REF_PREFIX, TapConfig, TapService

T = "tenant-a"
CUST, OTHER_CUST = "+27 82 111 4482", "+27 83 222 9999"


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


async def tap_and_pay_msg(svc, tag, phone, tenant=T):
    url = svc.tap(tag["code"])
    token = re.search(r"text=PAY%20(\S+)", url).group(1)
    return await svc.handle_text(tenant, phone, f"PAY {token}"), token


def mk_bill(svc, repo, tag, cents=50000, **kw):
    return svc.create_bill(tenant_id=T, tag_id=tag["id"], amount_cents=cents,
                           description="Beginner lesson", staff_id="coach", created_by="u1", **kw)


async def run_to_checkout(svc, msg, repo, phone, tip="10"):
    s = next(iter(repo.sessions.values()))
    await svc.handle_interactive(T, phone, f"kb:tip:{s['id']}:{tip}")
    await svc.handle_interactive(T, phone, f"kb:pay:{s['id']}")
    link = [m for m in msg.to(phone, "text") if "/v1/tap/pay/" in m[3]][-1][3]
    nonce = re.search(r"/v1/tap/pay/\S+/(\S+)", link).group(1)
    return s["id"], nonce


def itn(sid, cents, paid=True, pf="PF1", fee=1650):
    return {"reference": REF_PREFIX + sid, "paid": paid, "amount_cents": cents,
            "pf_payment_id": pf, "fee_cents": fee}


async def test_tap_url_and_unknown_tag(env):
    svc, repo, msg, gw, tag, _ = env
    url = svc.tap("coach-sipho")
    assert url.startswith("https://wa.me/27737815979?text=PAY%20")
    assert svc.tap("nope") is None
    repo.tags[tag["id"]]["status"] = "disabled"
    assert svc.tap("coach-sipho") is None


async def test_full_happy_path(env):
    svc, repo, msg, gw, tag, _ = env
    mk_bill(svc, repo, tag)                       # unaddressed: first tap claims
    handled, _ = await tap_and_pay_msg(svc, tag, CUST)
    assert handled
    lst = msg.to(CUST, "list")[0][3]
    assert "R 500.00" in lst[0] and "Sipho" in lst[0]
    assert [r["title"] for r in lst[1]] == ["No tip", "10%", "15%", "20%", "Custom"]
    sid, nonce = await run_to_checkout(svc, msg, repo, CUST, "10")
    s = repo.sessions[sid]
    assert (s["bill_cents"], s["tip_cents"], s["state"]) == (50000, 5000, "awaiting_payment")
    assert "R 550.00" in msg.to(CUST, "buttons")[0][3][0]

    url = await svc.open_pay_link(sid, nonce)
    assert url == f"https://pay.example/checkout/{sid}"
    assert await svc.open_pay_link(sid, "wrong-nonce") is None

    gw.itn = itn(sid, 55000)
    assert await svc.confirm_payment(T, {}, b"", {}) == "paid"
    assert repo.sessions[sid]["state"] == "paid"
    bill = next(iter(repo.bills.values()))
    assert bill["status"] == "paid"
    by_party = {}
    for l in repo.ledger:
        by_party[l["party_id"]] = by_party.get(l["party_id"], 0) + l["cents"]
    assert sum(l["cents"] for l in repo.ledger if l["cents"] > 0) == 55000
    assert sum(by_party.values()) == 55000 - 1650
    assert any("Paid R 550.00" in m[3] for m in msg.to(CUST, "text"))
    alert = msg.to("27800000001", "text")[0][3]
    assert "ending 482" in alert and "R 500.00" in alert and "R 50.00 tip" in alert
    assert "1111" not in alert


async def test_duplicate_itn_has_no_second_effect(env):
    svc, repo, msg, gw, tag, _ = env
    mk_bill(svc, repo, tag)
    await tap_and_pay_msg(svc, tag, CUST)
    sid, _ = await run_to_checkout(svc, msg, repo, CUST, "0")
    gw.itn = itn(sid, 50000)
    assert await svc.confirm_payment(T, {}, b"", {}) == "paid"
    n_lines, n_msgs = len(repo.ledger), len(msg.sent)
    assert await svc.confirm_payment(T, {}, b"", {}) == "duplicate"
    assert (len(repo.ledger), len(msg.sent)) == (n_lines, n_msgs)


async def test_bad_signature_and_amount_mismatch_never_mark_paid(env):
    svc, repo, msg, gw, tag, _ = env
    mk_bill(svc, repo, tag)
    await tap_and_pay_msg(svc, tag, CUST)
    sid, _ = await run_to_checkout(svc, msg, repo, CUST, "10")
    gw.itn = None
    assert await svc.confirm_payment(T, {}, b"", {}) == "rejected"
    gw.itn = itn(sid, 50000)                          # paid the bill but not the tip
    assert await svc.confirm_payment(T, {}, b"", {}) == "amount_mismatch"
    assert repo.sessions[sid]["state"] == "awaiting_payment"
    assert repo.ledger == []
    assert any("NOT marked paid" in m[3] for m in msg.to("27800000001", "text"))


async def test_failed_payment_leaves_the_bill_abandoned_for_the_reminders(env):
    svc, repo, msg, gw, tag, _ = env
    mk_bill(svc, repo, tag)
    await tap_and_pay_msg(svc, tag, CUST)
    sid, _ = await run_to_checkout(svc, msg, repo, CUST, "0")
    gw.itn = itn(sid, 50000, paid=False)
    assert await svc.confirm_payment(T, {}, b"", {}) == "failed"
    bill = next(iter(repo.bills.values()))
    assert bill["status"] == "abandoned" and bill["claimed_by_hash"] and bill["last_session_id"] == sid
    assert "didn't go through" in msg.to(CUST, "text")[-1][3]
    # the same customer can come back and try again straight away
    await tap_and_pay_msg(svc, tag, CUST)
    assert next(iter(repo.bills.values()))["status"] == "claimed"
    assert msg.to(CUST, "list")[-1]


async def test_second_customer_is_locked_out(env):
    svc, repo, msg, gw, tag, _ = env
    mk_bill(svc, repo, tag)
    await tap_and_pay_msg(svc, tag, CUST)
    await tap_and_pay_msg(svc, tag, OTHER_CUST)
    assert "being paid from another phone" in msg.to(OTHER_CUST, "text")[0][3]
    assert not msg.to(OTHER_CUST, "list")


async def test_double_tap_reuses_session(env):
    svc, repo, msg, gw, tag, _ = env
    mk_bill(svc, repo, tag)
    await tap_and_pay_msg(svc, tag, CUST)
    await tap_and_pay_msg(svc, tag, CUST)
    assert len(repo.sessions) == 1
    assert len(msg.to(CUST, "list")) == 2


async def test_claim_token_is_single_use_and_tenant_bound(env):
    svc, repo, msg, gw, tag, _ = env
    mk_bill(svc, repo, tag)
    _, token = await tap_and_pay_msg(svc, tag, CUST)
    await svc.handle_text(T, OTHER_CUST, f"PAY {token}")          # replay
    assert "couldn't verify" in msg.to(OTHER_CUST, "text")[0][3]
    url = svc.tap("coach-sipho")
    tok2 = re.search(r"text=PAY%20(\S+)", url).group(1)
    await svc.handle_text("tenant-b", OTHER_CUST, f"PAY {tok2}")  # wrong tenant's line
    assert "couldn't verify" in msg.to(OTHER_CUST, "text")[1][3]


async def test_expired_token(env):
    svc, repo, msg, gw, tag, clock = env
    url = svc.tap("coach-sipho")
    token = re.search(r"text=PAY%20(\S+)", url).group(1)
    from datetime import timedelta
    clock.t += timedelta(seconds=121)
    await svc.handle_text(T, CUST, f"PAY {token}")
    assert "couldn't verify" in msg.to(CUST, "text")[0][3]


async def test_no_open_bill(env):
    svc, repo, msg, gw, tag, _ = env
    await tap_and_pay_msg(svc, tag, CUST)
    assert "no open bill" in msg.to(CUST, "text")[0][3]


async def test_custom_tip_typed_and_validated(env):
    svc, repo, msg, gw, tag, _ = env
    mk_bill(svc, repo, tag)
    await tap_and_pay_msg(svc, tag, CUST)
    sid = next(iter(repo.sessions))
    await svc.handle_interactive(T, CUST, f"kb:tip:{sid}:custom")
    assert await svc.handle_text(T, CUST, "9999") is True        # over 100% of bill
    assert "doesn't look right" in msg.to(CUST, "text")[-1][3]
    assert await svc.handle_text(T, CUST, "15") is True
    assert repo.sessions[sid]["tip_cents"] == 1500 and repo.sessions[sid]["state"] == "awaiting_confirm"


async def test_unrelated_text_is_not_handled(env):
    svc, repo, msg, gw, tag, _ = env
    assert await svc.handle_text(T, CUST, "what are your opening hours?") is False


async def test_change_tip_returns_to_tip_step(env):
    svc, repo, msg, gw, tag, _ = env
    mk_bill(svc, repo, tag)
    await tap_and_pay_msg(svc, tag, CUST)
    sid = next(iter(repo.sessions))
    await svc.handle_interactive(T, CUST, f"kb:tip:{sid}:20")
    await svc.handle_interactive(T, CUST, f"kb:chg:{sid}")
    assert (repo.sessions[sid]["state"], repo.sessions[sid]["tip_cents"]) == ("awaiting_tip", 0)


async def test_interactive_reply_from_another_phone_is_refused(env):
    svc, repo, msg, gw, tag, _ = env
    mk_bill(svc, repo, tag)
    await tap_and_pay_msg(svc, tag, CUST)
    sid = next(iter(repo.sessions))
    await svc.handle_interactive(T, OTHER_CUST, f"kb:tip:{sid}:10")
    assert repo.sessions[sid]["state"] == "awaiting_tip"
    assert "expired" in msg.to(OTHER_CUST, "text")[0][3]


async def test_not_an_offered_tip_preset_is_ignored(env):
    svc, repo, msg, gw, tag, _ = env
    mk_bill(svc, repo, tag)
    await tap_and_pay_msg(svc, tag, CUST)
    sid = next(iter(repo.sessions))
    await svc.handle_interactive(T, CUST, f"kb:tip:{sid}:99")
    assert repo.sessions[sid]["state"] == "awaiting_tip"


async def test_session_expires_after_ttl_and_frees_the_bill(env):
    svc, repo, msg, gw, tag, clock = env
    mk_bill(svc, repo, tag)
    await tap_and_pay_msg(svc, tag, CUST)
    sid = next(iter(repo.sessions))
    from datetime import timedelta
    clock.t += timedelta(minutes=11)
    await svc.handle_interactive(T, CUST, f"kb:tip:{sid}:10")
    assert repo.sessions[sid]["state"] == "expired"
    assert next(iter(repo.bills.values()))["status"] == "open"


async def test_bill_addressed_to_another_number_needs_code_then_locks(env):
    svc, repo, msg, gw, tag, _ = env
    bill = mk_bill(svc, repo, tag, customer_phone=OTHER_CUST)
    code = bill["bill_code"]
    await tap_and_pay_msg(svc, tag, CUST)
    assert "4-digit code" in msg.to(CUST, "text")[0][3]
    wrong = "0000" if code != "0000" else "1111"
    for _ in range(3):
        await svc.handle_text(T, CUST, wrong)
    assert "Too many wrong codes" in msg.to(CUST, "text")[-1][3]
    await tap_and_pay_msg(svc, tag, CUST)
    assert "Too many wrong codes" in msg.to(CUST, "text")[-1][3]


async def test_correct_code_claims_the_bill(env):
    svc, repo, msg, gw, tag, _ = env
    bill = mk_bill(svc, repo, tag, customer_phone=OTHER_CUST)
    await tap_and_pay_msg(svc, tag, CUST)
    await svc.handle_text(T, CUST, bill["bill_code"])
    assert msg.to(CUST, "list")
    assert repo.bills[bill["id"]]["status"] == "claimed"


async def test_bill_addressed_to_payer_is_claimed_directly(env):
    svc, repo, msg, gw, tag, _ = env
    mk_bill(svc, repo, tag, customer_phone=CUST)
    await tap_and_pay_msg(svc, tag, CUST)
    assert msg.to(CUST, "list")


async def test_release_cancels_the_session(env):
    svc, repo, msg, gw, tag, _ = env
    bill = mk_bill(svc, repo, tag)
    await tap_and_pay_msg(svc, tag, CUST)
    assert svc.release_bill(T, bill["id"]) is True
    assert next(iter(repo.sessions.values()))["state"] == "cancelled"
    await tap_and_pay_msg(svc, tag, OTHER_CUST)      # now someone else can claim
    assert msg.to(OTHER_CUST, "list")


async def test_quick_tip_goes_to_the_person(env):
    svc, repo, msg, gw, _, _ = env
    qt = repo.add_tag(T, "guard-1", mode="quick_tip", bound_id="coach")
    await tap_and_pay_msg(svc, qt, CUST)
    assert "Tip Sipho" in msg.to(CUST, "buttons")[0][3][0]
    await svc.handle_text(T, CUST, "10")
    sid = next(iter(repo.sessions))
    assert (repo.sessions[sid]["tip_cents"], repo.sessions[sid]["state"]) == (1000, "awaiting_confirm")
    await svc.handle_interactive(T, CUST, f"kb:pay:{sid}")
    gw.itn = itn(sid, 1000, fee=0)
    assert await svc.confirm_payment(T, {}, b"", {}) == "paid"
    assert [l for l in repo.ledger] == [
        {"tenant_id": T, "payment_id": repo.payments[("payfast", REF_PREFIX + sid)]["id"],
         "kind": "tip", "party_id": "coach", "cents": 1000}]


async def test_late_payment_on_expired_session_is_flagged_not_booked(env):
    svc, repo, msg, gw, tag, clock = env
    mk_bill(svc, repo, tag)
    await tap_and_pay_msg(svc, tag, CUST)
    sid, _ = await run_to_checkout(svc, msg, repo, CUST, "0")
    repo.sessions[sid]["state"] = "expired"
    gw.itn = itn(sid, 50000)
    assert await svc.confirm_payment(T, {}, b"", {}) == "late_payment"
    assert repo.ledger == []


async def test_non_tap_reference_is_ignored(env):
    svc, repo, msg, gw, tag, _ = env
    gw.itn = {"reference": "some-invoice-uuid", "paid": True, "amount_cents": 100, "pf_payment_id": "x"}
    assert await svc.confirm_payment(T, {}, b"", {}) == "not_tap"
