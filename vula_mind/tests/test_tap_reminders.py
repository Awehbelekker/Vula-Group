"""Unpaid-bill reminders end to end against the in-memory fakes, with a controllable clock."""
import re
from datetime import datetime, timedelta, timezone

import pytest

from tests.tap_fakes import Clock, FakeGateway, FakeMessenger, MemoryRepo
from vula.tap.core import reminders as rm
from vula.tap.service import REF_PREFIX, ReminderError, TapConfig, TapService

T, CUST, OTHER = "tenant-a", "+27 82 111 4482", "+27 83 222 9999"
DIGITS = "27821114482"


def at(h, mi=0, day=6):
    return datetime(2026, 10, day, h, mi, tzinfo=rm.SAST).astimezone(timezone.utc)


@pytest.fixture
def env():
    clock = Clock()
    clock.t = at(14, 0)
    repo = MemoryRepo(clock)
    repo.members[T] = [{"id": "m1", "name": "Sipho", "whatsapp": "27800000001", "role": "staff"}]
    repo.settings[T] = {"tenant_id": T, "mode": "live", "reminders_max": 3}
    repo.rules[(T, "m1")] = {"staff_share_bp": 7000, "tip_rule": "direct"}
    repo.team[(T, None)] = ["27800000009"]
    msg, gw = FakeMessenger(), FakeGateway()
    svc = TapService(repo, msg, gw, TapConfig(pepper="p", public_base_url="https://api.test", encrypt=lambda x: "enc:" + x,
                                              decrypt=lambda x: x[4:], clock=clock, reminder_template="tap_payment_reminder", reminder_final_template="tap_payment_reminder_final"))
    tag = repo.add_tag(T, "tag-m1", bound_id="m1")
    return svc, repo, msg, gw, tag, clock


def go(clock, t):
    clock.t = t


async def abandon_at_confirm(svc, repo, msg, tag, tip="10"):
    """Customer taps, picks a tip, reaches the total and then leaves."""
    svc.create_bill(tenant_id=T, tag_id=tag["id"], amount_cents=50000, description="Beginner lesson", staff_id="m1", created_by="m1")
    tok = re.search(r"text=PAY%20(\S+)", svc.tap(tag["code"])).group(1)
    await svc.handle_text(T, CUST, f"PAY {tok}")
    sid = list(repo.sessions)[-1]                                      # the session this tap just created
    await svc.handle_interactive(T, CUST, f"kb:tip:{sid}:{tip}")
    return sid


def only_bill(repo):
    return next(iter(repo.bills.values()))


def link_in(text):
    return re.search(r"https://api\.test/v1/tap/pay/(\S+)/(\S+)", text)


async def test_session_expiry_after_seeing_the_total_abandons_the_bill(env):
    svc, repo, msg, gw, tag, clock = env
    sid = await abandon_at_confirm(svc, repo, msg, tag)
    go(clock, at(14, 11))
    stats = await svc.sweep(T)
    assert stats["expired"] == 1
    b = only_bill(repo)
    assert (b["status"], b["last_session_id"], b["abandoned_at"]) == ("abandoned", sid, at(14, 11).isoformat())


async def test_tapping_and_leaving_before_the_total_just_releases(env):
    svc, repo, msg, gw, tag, clock = env
    svc.create_bill(tenant_id=T, tag_id=tag["id"], amount_cents=50000, description="Lesson", staff_id="m1", created_by="m1")
    tok = re.search(r"text=PAY%20(\S+)", svc.tap(tag["code"])).group(1)
    await svc.handle_text(T, CUST, f"PAY {tok}")                      # sees the tip list only
    go(clock, at(14, 11))
    await svc.sweep(T)
    assert only_bill(repo)["status"] == "open" and not repo.reminders


async def test_full_sequence_10_minutes_next_morning_day_three(env):
    svc, repo, msg, gw, tag, clock = env
    await abandon_at_confirm(svc, repo, msg, tag)
    go(clock, at(14, 11)); await svc.sweep(T)                          # session expires; bill abandoned at 14:11
    await svc.sweep(T)
    assert not repo.reminders                                          # not due yet
    go(clock, at(14, 22)); await svc.sweep(T)                          # +10 minutes
    first = msg.to(CUST, "text")[-1][3]
    assert "You haven't finished paying Bean and Brew Coffee R 550.00" in first and "We'll send up to 2 more" in first and "STOP" in first
    assert len(repo.reminders) == 1 and repo.reminders[0]["status"] == "sent" and repo.reminders[0]["channel"] == "text"
    go(clock, at(20, 30)); await svc.sweep(T)                          # same evening: nothing
    assert len(repo.reminders) == 1
    go(clock, at(8, 5, day=7)); await svc.sweep(T)                     # next morning
    assert len(repo.reminders) == 2 and "Reminder: R 550.00 to Bean and Brew Coffee" in msg.to(CUST, "text")[-1][3]
    go(clock, at(8, 5, day=8)); await svc.sweep(T)                     # day 2: nothing yet
    assert len(repo.reminders) == 2
    go(clock, at(14, 30, day=9)); await svc.sweep(T)                   # day 3
    assert len(repo.reminders) == 3
    # day 3 is outside the 24 h window, so it goes as the approved "final" template (its wording says last)
    assert msg.to(CUST, "template")[-1][3][0] == "tap_payment_reminder_final"
    assert only_bill(repo)["status"] == "needs_follow_up"              # then it's the owner's
    await svc.sweep(T)
    assert len(repo.reminders) == 3                                    # never a fourth


async def test_never_more_than_one_per_day_even_if_the_sweeper_was_asleep(env):
    svc, repo, msg, gw, tag, clock = env
    await abandon_at_confirm(svc, repo, msg, tag)
    go(clock, at(14, 11)); await svc.sweep(T)
    go(clock, at(10, 0, day=12)); await svc.sweep(T)                   # sweeper was down for days: everything is overdue
    assert len(repo.reminders) == 1
    for _ in range(5):
        await svc.sweep(T)                                             # same minute, same day: nothing more
    assert len(repo.reminders) == 1


async def test_nothing_goes_out_in_the_evening_or_at_night(env):
    svc, repo, msg, gw, tag, clock = env
    await abandon_at_confirm(svc, repo, msg, tag)
    go(clock, at(19, 55)); await svc.sweep(T)                          # abandoned at 19:55 + ... session expires
    go(clock, at(20, 6)); await svc.sweep(T)
    go(clock, at(23, 0)); await svc.sweep(T)
    go(clock, at(7, 59, day=7)); await svc.sweep(T)
    assert not repo.reminders
    go(clock, at(8, 0, day=7)); await svc.sweep(T)
    assert len(repo.reminders) == 1


async def test_pay_link_in_a_reminder_works_once_and_older_links_die(env):
    svc, repo, msg, gw, tag, clock = env
    await abandon_at_confirm(svc, repo, msg, tag)
    go(clock, at(14, 11)); await svc.sweep(T)
    go(clock, at(14, 22)); await svc.sweep(T)
    sid1, nonce1 = link_in(msg.to(CUST, "text")[-1][3]).groups()
    go(clock, at(8, 5, day=7)); await svc.sweep(T)
    sid2, nonce2 = link_in(msg.to(CUST, "text")[-1][3]).groups()
    assert sid1 != sid2
    assert await svc.open_pay_link(sid1, nonce1) is None               # the first link was replaced
    assert await svc.open_pay_link(sid2, "wrong") is None
    assert await svc.open_pay_link(sid2, nonce2) == f"https://pay.example/checkout/{sid2}"


async def test_paying_through_a_reminder_link_pays_the_bill_and_stops_everything(env):
    svc, repo, msg, gw, tag, clock = env
    await abandon_at_confirm(svc, repo, msg, tag, tip="10")
    go(clock, at(14, 11)); await svc.sweep(T)
    go(clock, at(14, 22)); await svc.sweep(T)
    sid, _ = link_in(msg.to(CUST, "text")[-1][3]).groups()
    assert (repo.sessions[sid]["bill_cents"], repo.sessions[sid]["tip_cents"]) == (50000, 5000)   # same tip as before
    gw.itn = {"reference": REF_PREFIX + sid, "paid": True, "amount_cents": 55000, "pf_payment_id": "R1", "fee_cents": 0}
    assert await svc.confirm_payment(T, {}, b"", {}) == "paid"
    assert only_bill(repo)["status"] == "paid"
    assert any(x["kind"] == "tip" and x["cents"] == 5000 for x in repo.ledger)
    n = len(msg.sent)
    go(clock, at(8, 5, day=7)); await svc.sweep(T); go(clock, at(9, 0, day=10)); await svc.sweep(T)
    assert len(msg.sent) == n and len(repo.reminders) == 1             # no reminders after paying


async def test_customer_tapping_again_reclaims_and_old_reminder_links_die(env):
    svc, repo, msg, gw, tag, clock = env
    await abandon_at_confirm(svc, repo, msg, tag)
    go(clock, at(14, 11)); await svc.sweep(T)
    go(clock, at(14, 22)); await svc.sweep(T)
    sid, nonce = link_in(msg.to(CUST, "text")[-1][3]).groups()
    tok = re.search(r"text=PAY%20(\S+)", svc.tap(tag["code"])).group(1)
    await svc.handle_text(T, CUST, f"PAY {tok}")
    b = only_bill(repo)
    assert b["status"] == "claimed" and b["abandoned_at"] is None
    assert await svc.open_pay_link(sid, nonce) is None
    await svc.sweep(T)
    assert len(repo.reminders) == 1                                    # claimed again -> no more reminders


async def test_someone_else_cannot_take_an_abandoned_bill(env):
    svc, repo, msg, gw, tag, clock = env
    await abandon_at_confirm(svc, repo, msg, tag)
    go(clock, at(14, 11)); await svc.sweep(T)
    tok = re.search(r"text=PAY%20(\S+)", svc.tap(tag["code"])).group(1)
    await svc.handle_text(T, OTHER, f"PAY {tok}")
    assert "being paid from another phone" in msg.to(OTHER, "text")[-1][3]


async def test_stop_ends_reminders_and_hands_the_bill_to_the_owner(env):
    svc, repo, msg, gw, tag, clock = env
    await abandon_at_confirm(svc, repo, msg, tag)
    go(clock, at(14, 11)); await svc.sweep(T)
    go(clock, at(14, 22)); await svc.sweep(T)
    assert len(repo.reminders) == 1
    repo.opted_out.add(DIGITS)                                         # customer replied STOP
    n = len(msg.sent)
    go(clock, at(8, 5, day=7)); stats = await svc.sweep(T)
    assert len(msg.sent) == n and len(repo.reminders) == 1 and stats["followups"] == 1
    assert only_bill(repo)["status"] == "needs_follow_up"
    with pytest.raises(ReminderError, match="asked not to receive"):
        await svc.send_reminder_now(T, only_bill(repo)["id"])


async def test_reminders_off_goes_straight_to_the_owner(env):
    svc, repo, msg, gw, tag, clock = env
    repo.settings[T]["reminders_max"] = 0
    await abandon_at_confirm(svc, repo, msg, tag)
    go(clock, at(14, 11)); await svc.sweep(T)
    assert only_bill(repo)["status"] == "needs_follow_up"
    go(clock, at(9, 0, day=8)); await svc.sweep(T)
    assert not repo.reminders and len(msg.to(CUST, "text")) < 99


@pytest.mark.parametrize("cap", [1, 2])
async def test_lower_caps_stop_early_and_the_last_one_says_so(env, cap):
    svc, repo, msg, gw, tag, clock = env
    repo.settings[T]["reminders_max"] = cap
    await abandon_at_confirm(svc, repo, msg, tag)
    go(clock, at(14, 11)); await svc.sweep(T)
    go(clock, at(14, 22)); await svc.sweep(T)
    if cap == 1:
        assert "only reminder" in msg.to(CUST, "text")[-1][3] and only_bill(repo)["status"] == "needs_follow_up"
    else:
        go(clock, at(8, 5, day=7)); await svc.sweep(T)
        assert msg.to(CUST, "text")[-1][3].startswith("Last reminder:") and only_bill(repo)["status"] == "needs_follow_up"
    go(clock, at(10, 0, day=12)); await svc.sweep(T)
    assert len(repo.reminders) == cap


async def test_two_sweepers_at_once_send_each_reminder_exactly_once(env):
    import asyncio
    svc, repo, msg, gw, tag, clock = env
    await abandon_at_confirm(svc, repo, msg, tag)
    go(clock, at(14, 11)); await svc.sweep(T)
    go(clock, at(14, 22))
    await asyncio.gather(svc.sweep(T), svc.sweep(T), svc.sweep(T))
    assert len(repo.reminders) == 1
    assert len([m for m in msg.to(CUST, "text") if "You haven't finished paying" in m[3]]) == 1


async def test_outside_the_24h_window_a_template_is_used_not_free_text(env):
    svc, repo, msg, gw, tag, clock = env
    await abandon_at_confirm(svc, repo, msg, tag)
    go(clock, at(14, 11)); await svc.sweep(T)
    go(clock, at(14, 22)); await svc.sweep(T)                          # inside the window: free text
    assert msg.to(CUST, "text")[-1][3].startswith("You haven't") and not msg.to(CUST, "template")
    go(clock, at(14, 30, day=9)); await svc.sweep(T)                   # day 3: well outside it
    name, params = msg.to(CUST, "template")[-1][3]
    assert name == "tap_payment_reminder" and params[0] == "Bean and Brew Coffee" and params[1] == "R 550.00" and params[2].startswith("https://api.test/v1/tap/pay/")
    assert repo.reminders[-1]["channel"] == "template" and repo.reminders[-1]["status"] == "sent"


async def test_no_template_configured_skips_cleanly_and_never_sends_free_text(env):
    svc, repo, msg, gw, tag, clock = env
    svc.cfg.reminder_template = ""
    await abandon_at_confirm(svc, repo, msg, tag)
    go(clock, at(14, 11)); await svc.sweep(T)
    go(clock, at(14, 30, day=9)); await svc.sweep(T)                   # outside the window, no template
    assert not any("Reminder" in m[3] or "Last reminder" in m[3] for m in msg.to(CUST, "text"))
    assert repo.reminders[-1]["status"] == "skipped_no_template"
    assert not any(x["state"] == "awaiting_payment" and x.get("bill_id") for x in repo.sessions.values())   # no dangling link
    b = [u for u in svc.unpaid(T)][0]
    assert b["skipped"] >= 1


async def test_a_failed_send_is_retried_on_the_next_pass_not_lost(env):
    svc, repo, msg, gw, tag, clock = env
    await abandon_at_confirm(svc, repo, msg, tag)
    go(clock, at(14, 11)); await svc.sweep(T)
    go(clock, at(14, 22))
    real = msg.text
    async def failing(*a, **k):
        return False
    msg.text = failing
    await svc.sweep(T)
    assert not repo.reminders                                          # claim released
    msg.text = real
    await svc.sweep(T)
    assert len(repo.reminders) == 1


async def test_manual_resend_rules(env):
    svc, repo, msg, gw, tag, clock = env
    await abandon_at_confirm(svc, repo, msg, tag)
    go(clock, at(14, 11)); await svc.sweep(T)
    bid = only_bill(repo)["id"]
    go(clock, at(21, 0))
    with pytest.raises(ReminderError, match="08:00 and 20:00"):
        await svc.send_reminder_now(T, bid)
    go(clock, at(15, 0))
    assert await svc.send_reminder_now(T, bid) == "sent"
    assert msg.to(CUST, "text")[-1][3].startswith("Bean and Brew Coffee is still waiting for R 550.00")
    assert repo.reminders[-1]["kind"] == "manual"
    with pytest.raises(ReminderError, match="already went out"):
        await svc.send_reminder_now(T, bid)
    paid_bill = svc.create_bill(tenant_id=T, tag_id=tag["id"], amount_cents=100, description="x", staff_id="m1", created_by="m")
    with pytest.raises(ReminderError, match="Only unpaid"):
        await svc.send_reminder_now(T, paid_bill["id"])


async def test_manual_resend_does_not_use_up_the_automatic_three(env):
    svc, repo, msg, gw, tag, clock = env
    await abandon_at_confirm(svc, repo, msg, tag)
    go(clock, at(14, 11)); await svc.sweep(T)
    await svc.send_reminder_now(T, only_bill(repo)["id"])              # 14:11 manual
    go(clock, at(8, 5, day=7)); await svc.sweep(T)
    go(clock, at(8, 5, day=8)); await svc.sweep(T)
    go(clock, at(15, 0, day=9)); await svc.sweep(T)
    autos = [r for r in repo.reminders if r["kind"] == "auto"]
    assert [r["seq"] for r in autos] == [1, 2, 3]


async def test_owner_closing_actions(env):
    svc, repo, msg, gw, tag, clock = env
    await abandon_at_confirm(svc, repo, msg, tag)
    go(clock, at(14, 11)); await svc.sweep(T)
    bid = only_bill(repo)["id"]
    with pytest.raises(ReminderError):
        svc.close_unpaid(T, bid, "paid_other", "barter")
    assert svc.close_unpaid(T, bid, "paid_other", "cash") is True
    b = only_bill(repo)
    assert (b["status"], b["closed_reason"]) == ("paid_other", "cash")
    assert svc.close_unpaid(T, bid, "write_off") is False             # already closed
    go(clock, at(9, 0, day=8)); await svc.sweep(T)
    assert not repo.reminders                                          # nothing after closing


async def test_write_off_release_and_cancel_kill_pay_links(env):
    svc, repo, msg, gw, tag, clock = env
    await abandon_at_confirm(svc, repo, msg, tag)
    go(clock, at(14, 11)); await svc.sweep(T)
    go(clock, at(14, 22)); await svc.sweep(T)
    sid, nonce = link_in(msg.to(CUST, "text")[-1][3]).groups()
    bid = only_bill(repo)["id"]
    assert svc.close_unpaid(T, bid, "write_off") is True
    assert only_bill(repo)["status"] == "written_off" and await svc.open_pay_link(sid, nonce) is None
    # release: back to open, claim cleared
    await abandon_at_confirm(svc, repo, msg, tag)     # (second bill on the same tag)
    go(clock, at(15, 0)); await svc.sweep(T)
    b2 = [b for b in repo.bills.values() if b["status"] == "abandoned"][0]
    assert svc.close_unpaid(T, b2["id"], "release") is True
    assert (repo.bills[b2["id"]]["status"], repo.bills[b2["id"]]["claimed_by_hash"]) == ("open", None)


async def test_unpaid_list_for_the_owner(env):
    svc, repo, msg, gw, tag, clock = env
    await abandon_at_confirm(svc, repo, msg, tag)
    go(clock, at(14, 11)); await svc.sweep(T)
    go(clock, at(14, 22)); await svc.sweep(T)
    u = svc.unpaid(T)[0]
    assert u["status"] == "abandoned" and u["total_cents"] == 55000 and u["customer"] == "ending 482"
    assert (u["reminders_sent"], u["reminders_max"], u["opted_out"]) == (1, 3, False)
    assert u["next_reminder_at"].startswith("2026-10-07T06:00")        # 08:00 SAST next morning
    assert "27821114482" not in str(u)                                 # never the full number


async def test_test_bills_never_enter_the_reminder_track(env):
    svc, repo, msg, gw, tag, clock = env
    svc.create_bill(tenant_id=T, tag_id=tag["id"], amount_cents=500, description="Test payment", staff_id="m1", created_by="setup", is_test=True)
    tok = re.search(r"text=PAY%20(\S+)", svc.tap(tag["code"])).group(1)
    await svc.handle_text(T, CUST, f"PAY {tok}")
    sid = next(iter(repo.sessions))
    await svc.handle_interactive(T, CUST, f"kb:tip:{sid}:0")
    go(clock, at(14, 11)); await svc.sweep(T)
    assert only_bill(repo)["status"] == "open" and not repo.reminders
