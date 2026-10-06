"""Tap-to-pay pure core: money/splits, state machines, bill matching."""
from datetime import datetime, timedelta

import pytest
from hypothesis import given, strategies as st

from vula.tap.core import matching as m
from vula.tap.core import money as mo
from vula.tap.core import states as s

# ── money ─────────────────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("text,cents", [("15", 1500), ("R15,50", 1550), ("15.5", 1550),
                                         (" r 7.05 ", 705), ("0", 0)])
def test_parse_rands(text, cents):
    assert mo.parse_rands(text) == cents


@pytest.mark.parametrize("bad", ["", "abc", "-5", "1.234", "1e3", "15 rand", "R", "1,2,3"])
def test_parse_rands_rejects(bad):
    with pytest.raises(mo.MoneyError):
        mo.parse_rands(bad)


def test_format_rands():
    assert mo.format_rands(55000) == "R 550.00"
    assert mo.format_rands(123456) == "R 1 234.56"


def test_pct_tip_half_up():
    assert mo.pct_tip(50000, 1000) == 5000
    assert mo.pct_tip(105, 1000) == 11       # 10.5 -> 11
    assert mo.pct_tip(104, 1000) == 10       # 10.4 -> 10


def test_custom_tip_limits():
    assert mo.validate_custom_tip("0", 50000) == 0
    assert mo.validate_custom_tip("15", 50000) == 1500
    with pytest.raises(mo.MoneyError):
        mo.validate_custom_tip("0.50", 50000)       # below R1
    with pytest.raises(mo.MoneyError):
        mo.validate_custom_tip("501", 50000)        # above 100% of bill


def test_worked_example_from_spec():
    """R500 lesson + R50 tip: shop 30% of lesson, coach 70% + tip."""
    a = mo.allocate_payment(bill_cents=50000, tip_cents=5000, merchant="shop", staff="coach",
                            staff_share_bp=7000)
    n = a.net_by_party()
    assert n == {"shop": 15000, "coach": 40000}
    assert [l for l in a.lines if l.kind == "tip"] == [mo.Line("tip", "coach", 5000)]


def test_tip_pool_and_house_cut():
    a = mo.allocate_payment(bill_cents=10000, tip_cents=1001, merchant="shop", staff=None,
                            tip_rule=mo.TIP_POOL, pool_members=["b", "a", "c"])
    tips = {l.party: l.cents for l in a.lines if l.kind == "tip"}
    assert tips == {"a": 334, "b": 334, "c": 333}
    b = mo.allocate_payment(bill_cents=10000, tip_cents=1000, merchant="shop", staff="w",
                            tip_rule=mo.TIP_HOUSE_CUT, house_cut_bp=2000)
    tips = {l.party: l.cents for l in b.lines if l.kind == "tip"}
    assert tips == {"shop": 200, "w": 800}


def test_unassignable_tip_is_held_not_dropped():
    a = mo.allocate_payment(bill_cents=1000, tip_cents=100, merchant="shop", staff=None)
    assert a.credits() == 1100
    assert mo.Line("tip", "shop", 100) in a.lines


@st.composite
def payments(draw):
    bill = draw(st.integers(0, 5_000_000))
    tip = draw(st.integers(0, 2_000_000))
    gross = bill + tip
    fee = draw(st.integers(0, min(gross, 300_000)))
    plat = draw(st.integers(0, min(gross - fee, 300_000)))
    members = draw(st.lists(st.sampled_from(["a", "b", "c", "d"]), max_size=4, unique=True))
    return dict(
        bill_cents=bill, tip_cents=tip, merchant="shop",
        staff=draw(st.sampled_from([None, "a", "b"])),
        staff_share_bp=draw(st.integers(0, 10_000)),
        tip_rule=draw(st.sampled_from([mo.TIP_DIRECT, mo.TIP_POOL, mo.TIP_HOUSE_CUT])),
        house_cut_bp=draw(st.integers(0, 10_000)),
        house_cut_then=draw(st.sampled_from([mo.TIP_DIRECT, mo.TIP_POOL])),
        pool_members=members,
        pool_hours={x: draw(st.integers(0, 40)) for x in members} if draw(st.booleans()) else None,
        provider_fee_cents=fee, platform_fee_cents=plat,
        merchant_absorbs_provider_fee=draw(st.booleans()),
    )


@given(payments())
def test_split_lines_sum_to_payment_to_the_cent(p):
    a = mo.allocate_payment(**p)
    gross = p["bill_cents"] + p["tip_cents"]
    assert a.credits() == gross
    assert a.deductions() == p["provider_fee_cents"] + p["platform_fee_cents"]
    assert sum(a.net_by_party().values()) + a.deductions() == gross
    assert all(l.cents != 0 for l in a.lines)


@given(payments())
def test_no_party_is_paid_negative_unless_merchant_bears_fee(p):
    a = mo.allocate_payment(**p)
    for party, net in a.net_by_party().items():
        if party != "shop":
            assert net >= 0 or p["provider_fee_cents"] > 0


@given(payments(), st.data())
def test_refund_reversal(p, data):
    a = mo.allocate_payment(**p)
    gross = a.gross_cents
    if gross == 0:
        return
    # full refund reverses every line exactly
    full = mo.reverse_lines(a.lines, gross, gross)
    tot: dict = {}
    for l in list(a.lines) + list(full):
        tot[(l.kind, l.party)] = tot.get((l.kind, l.party), 0) + l.cents
    assert all(v == 0 for v in tot.values())
    # a partial refund reverses exactly that much credit
    r = data.draw(st.integers(1, gross))
    rev = mo.reverse_lines(a.lines, r, gross)
    assert -sum(l.cents for l in rev if l.cents < 0 and l.kind in ("bill", "tip")) == r


def test_split_floor_rejects_over_100pct():
    with pytest.raises(mo.MoneyError):
        mo.split_floor(100, {"a": 6000, "b": 6000}, "shop")

# ── state machines ───────────────────────────────────────────────────────────────────────────

def test_bill_transitions_exhaustive():
    legal = s.legal_bill_pairs()
    for state, event in s.all_bill_pairs():
        if (state, event) in legal:
            assert s.bill_next(state, event) in s.BILL_STATES
        else:
            with pytest.raises(s.IllegalTransition):
                s.bill_next(state, event)


def test_session_transitions_exhaustive():
    legal = s.legal_session_pairs()
    for state, event in s.all_session_pairs():
        if (state, event) in legal:
            assert s.session_next(state, event) in s.SESSION_STATES
        else:
            with pytest.raises(s.IllegalTransition):
                s.session_next(state, event)


def test_terminal_states_have_no_way_out_except_refund():
    for st_ in ("failed", "expired", "cancelled", "refunded"):
        assert not [e for e in s.SESSION_EVENTS if (st_, e) in s.legal_session_pairs()]
    for st_ in ("paid", "cancelled", "expired", "written_off", "paid_other"):
        assert not [e for e in s.BILL_EVENTS if (st_, e) in s.legal_bill_pairs()]


def test_happy_path():
    st_ = "claimed"
    for ev in ("amount_set", "tip_chosen", "pay_now", "payment_succeeded", "full_refund"):
        st_ = s.session_next(st_, ev)
    assert st_ == "refunded"
    assert s.bill_next(s.bill_next("open", "claim"), "pay") == "paid"


def test_a_paid_session_can_never_expire_or_cancel():
    for ev in ("expire", "cancel", "pay_now", "payment_failed"):
        with pytest.raises(s.IllegalTransition):
            s.session_next("paid", ev)

# ── matching ─────────────────────────────────────────────────────────────────────────────────

N, OTHER = "h-n", "h-other"


def B(id, status="open", cust=None, by=None):
    return m.BillView(id, status, cust, by)


def test_quick_tip_needs_no_bill():
    assert m.resolve_tap(tag_mode="quick_tip", payer_hash=N, bills=[]).kind == m.QUICK_TIP


def test_bill_addressed_to_payer():
    d = m.resolve_tap(tag_mode="appointment", payer_hash=N, bills=[B("1", cust=N), B("2", cust=OTHER)])
    assert (d.kind, d.bill_id) == (m.CLAIM, "1")


def test_several_bills_for_payer_means_choose():
    d = m.resolve_tap(tag_mode="appointment", payer_hash=N, bills=[B("2", cust=N), B("1", cust=N)])
    assert (d.kind, d.bill_ids) == (m.CHOOSE, ("1", "2"))


def test_first_tap_claims_unaddressed_bill():
    d = m.resolve_tap(tag_mode="counter", payer_hash=N, bills=[B("1")])
    assert (d.kind, d.bill_id) == (m.CLAIM, "1")


def test_second_customer_sees_locked():
    d = m.resolve_tap(tag_mode="counter", payer_hash=OTHER, bills=[B("1", "claimed", by=N)])
    assert d.kind == m.LOCKED


def test_double_tap_reuses_own_claim():
    d = m.resolve_tap(tag_mode="counter", payer_hash=N, bills=[B("1", "claimed", by=N)])
    assert (d.kind, d.bill_id) == (m.CLAIM, "1")


def test_other_number_needs_code_then_lock():
    bills = [B("1", cust=OTHER)]
    assert m.resolve_tap(tag_mode="appointment", payer_hash=N, bills=bills).kind == m.CODE_NEEDED
    assert m.resolve_tap(tag_mode="appointment", payer_hash=N, bills=bills,
                         code_locked=True).kind == m.CODE_LOCKED


def test_open_amount_and_no_bill():
    assert m.resolve_tap(tag_mode="counter", payer_hash=N, bills=[],
                         tag_allows_open_amount=True).kind == m.ASK_AMOUNT
    assert m.resolve_tap(tag_mode="counter", payer_hash=N, bills=[]).kind == m.NO_OPEN_BILL
    assert m.resolve_tap(tag_mode="counter", payer_hash=N,
                         bills=[B("1", "paid")]).kind == m.NO_OPEN_BILL


def test_bill_code_three_attempts_then_15_min_lock():
    t0 = datetime(2026, 10, 6, 10, 0)
    gate = m.CodeGate()
    for i in range(3):
        ok, gate = m.check_bill_code("1234", "0000", gate, t0)
        assert not ok
    assert gate.locked_until == t0 + m.CODE_LOCK
    ok, gate = m.check_bill_code("1234", "1234", gate, t0 + timedelta(minutes=5))
    assert not ok                                         # right code, still locked
    ok, gate = m.check_bill_code("1234", "1234", gate, t0 + timedelta(minutes=16))
    assert ok and gate == m.CodeGate()
