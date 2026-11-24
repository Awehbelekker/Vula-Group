"""Each waiter's numbers and the team's board (vula/tap/scoreboard.py, 24 Nov 2026): counted from
paid bills and table service; tips are a person's own and never on the shared board; the points
rule is the one the app shows. Made-up names."""
from datetime import timedelta

import pytest

from tests.tap_fakes import Clock, FakeGateway, FakeMessenger, MemoryRepo
from vula.tap.scoreboard import ScoreError
from vula.tap.service import TapConfig, TapService

T = "the-yard"
THANDI, PIETER, BOSS = "w-thandi", "w-pieter", "m-boss"


@pytest.fixture
def env():
    clock = Clock()
    repo = MemoryRepo(clock)
    svc = TapService(repo, FakeMessenger(), FakeGateway(), TapConfig(
        pepper="pep", public_base_url="https://api.test", encrypt=lambda s: "enc:" + s, decrypt=lambda s: s[4:], clock=clock))
    repo.members[T] = [{"id": THANDI, "name": "Thandi Mokoena", "role": "staff"},
                       {"id": PIETER, "name": "Pieter Botha", "role": "staff"},
                       {"id": BOSS, "name": "Aisha Davids", "role": "owner"}]
    repo.products = [{"id": "p-coke", "tenant_id": T, "name": "Coke", "price_cents": 2500, "in_stock": True}]
    svc.tables.save_settings(T, calls=True, orders=True)
    return svc, repo, clock


def paid(repo, clock, staff, bill_cents, tip_cents=0, is_test=False, tenant=T):
    """A bill that was paid now: what the payment flow leaves behind."""
    b = repo.create_bill({"tenant_id": tenant, "tag_id": "tag", "bill_type": "fixed", "status": "paid",
                          "description": "x", "subtotal_cents": bill_cents, "staff_id": staff, "is_test": is_test})
    s = repo.create_session({"tenant_id": tenant, "bill_id": b["id"], "payer_hash": "p", "state": "paid",
                             "bill_cents": bill_cents, "tip_cents": tip_cents})
    return s


async def call_answered(svc, clock, table, by, after_seconds):
    c = await svc.tables.call(table["url"].rsplit("/", 1)[1], "browser", "waiter")
    clock.t += timedelta(seconds=after_seconds)
    svc.tables.accept(T, c["id"], by)
    svc.tables.done(T, c["id"], by)


async def test_the_numbers_come_from_paid_bills_and_table_service(env):
    svc, repo, clock = env
    table = svc.tables.create_table(T, "Table 7", THANDI)
    paid(repo, clock, THANDI, 45000, tip_cents=5000)
    paid(repo, clock, THANDI, 12050, tip_cents=1000)
    paid(repo, clock, THANDI, 99900, is_test=True)                       # a test payment never counts
    paid(repo, clock, THANDI, 5000, tenant="someone-else")               # nor another business's
    await call_answered(svc, clock, table, THANDI, 60)                    # quick
    await call_answered(svc, clock, table, THANDI, 300)                   # slow
    o = await svc.tables.order(table["url"].rsplit("/", 1)[1], "browser", [{"product_id": "p-coke", "quantity": 1}])
    svc.tables.confirm(T, o["id"], THANDI)
    me = svc.scores.mine(T, THANDI, "today")
    assert me["sales_cents"] == 57050 and me["bills"] == 2 and me["tips_cents"] == 6000
    assert me["calls"] == 2 and me["avg_wait_seconds"] == 180 and me["orders"] == 1
    assert me["points"] == 5 + 5 + 2 + 3 and me["rank"] == 1 and me["of"] == 1    # R570 → 5, quick 5, slow 2, order 3
    nobody = svc.scores.mine(T, PIETER, "today")
    assert nobody["sales_cents"] == 0 and nobody["rank"] is None and nobody["points"] == 0 and nobody["avg_wait_seconds"] is None


async def test_the_shared_board_has_no_tips_and_ranks_by_points(env):
    svc, repo, clock = env
    table = svc.tables.create_table(T, "Table 7", THANDI)
    paid(repo, clock, PIETER, 60000, tip_cents=9000)                      # sells more: 6 points
    paid(repo, clock, THANDI, 30000, tip_cents=2000)                      # sells less: 3 points...
    for _ in range(3):
        await call_answered(svc, clock, table, THANDI, 30)                # ...but is quick for tables: +15
    board = svc.scores.board(T, "today")
    assert [(r["name"], r["rank"], r["points"]) for r in board] == [("Thandi", 1, 18), ("Pieter", 2, 6)]
    assert all("tips_cents" not in r for r in board)
    assert board[0]["badges"] == ["Fastest to a table"] and board[1]["badges"] == ["Top seller"]
    owner = svc.scores.board(T, "today", with_tips=True)
    assert {r["name"]: r["tips_cents"] for r in owner} == {"Thandi": 2000, "Pieter": 9000}
    assert "member_id" in board[0] and [r["name"] for r in board] == ["Thandi", "Pieter"]     # first names only


async def test_a_tie_gets_no_badge_and_someone_who_left_isnt_listed(env):
    svc, repo, clock = env
    paid(repo, clock, THANDI, 10000)
    paid(repo, clock, PIETER, 10000)
    paid(repo, clock, "w-gone", 90000)                                    # not on the team any more
    board = svc.scores.board(T, "today")
    assert [r["name"] for r in board] == ["Pieter", "Thandi"] and all(r["badges"] == [] for r in board)


async def test_periods_and_the_streak(env):
    svc, repo, clock = env
    for days_ago in (9, 2, 1, 0):
        clock.t -= timedelta(days=days_ago)
        paid(repo, clock, THANDI, 10000)
        clock.t += timedelta(days=days_ago)
    assert svc.scores.mine(T, THANDI, "today")["bills"] == 1
    assert svc.scores.mine(T, THANDI, "week")["bills"] == 3
    assert svc.scores.mine(T, THANDI, "month")["bills"] == 4
    assert svc.scores.mine(T, THANDI, "today")["streak_days"] == 3        # today, yesterday, the day before
    clock.t += timedelta(days=1)                                          # tomorrow, before the first bill
    assert svc.scores.mine(T, THANDI, "today")["streak_days"] == 3 and svc.scores.mine(T, THANDI, "today")["bills"] == 0
    clock.t += timedelta(days=1)
    assert svc.scores.mine(T, THANDI, "today")["streak_days"] == 0        # a day missed
    with pytest.raises(ScoreError):
        svc.scores.board(T, "year")


async def test_income_is_the_persons_own_share_day_by_day(env):
    svc, repo, clock = env

    def earned(member, cents, kind="bill"):
        repo.insert_ledger_lines([{"tenant_id": T, "payment_id": "pay", "kind": kind, "party_id": member, "cents": cents}])

    clock.t -= timedelta(days=3)
    paid(repo, clock, THANDI, 40000, tip_cents=4000)
    earned(THANDI, 12000)
    earned(THANDI, 4000, "tip")
    clock.t += timedelta(days=3)
    paid(repo, clock, THANDI, 20000, tip_cents=1000)
    earned(THANDI, 6000)
    earned(THANDI, 1000, "tip")
    earned(THANDI, -150, "provider_fee")                                  # a fee they carry comes off
    earned(PIETER, 99900)                                                 # someone else's is never theirs
    inc = svc.scores.income(T, THANDI, 7)
    assert len(inc["days"]) == 7 and inc["days"][-1]["earned_cents"] == 6850 and inc["days"][-4]["earned_cents"] == 16000
    assert inc["today"] == {"earned_cents": 6850, "sales_cents": 20000, "tips_cents": 1000, "bills": 1}
    assert inc["last_7"] == {"earned_cents": 22850, "sales_cents": 60000, "tips_cents": 5000, "bills": 2}
    assert sum(d["earned_cents"] for d in inc["days"]) == inc["last_7"]["earned_cents"]
    assert svc.scores.income(T, "w-nobody", 7)["last_7"]["earned_cents"] == 0
    for bad in (0, 61, 1.5, True):
        with pytest.raises(ScoreError):
            svc.scores.income(T, THANDI, bad)
