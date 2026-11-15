"""AI allowance and top-ups (vula/ai_allowance.py, migration 270, 14 Nov 2026): rand at Vula's
price (USD × rate × 1.30); Starter R100 / Growth R300 / Business R750 a month; paid top-ups carry
over and are used oldest first; only Ask anything and research pause when it's all used; the
owner is told at 80% and 100%, once each a month. Made-up figures."""
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

from config import settings
from core import llm_router
from tests.fake_supabase import FakeSupabase
from vula import ai_allowance as A
from vula.commerce import service

T = "digg-demo"


@pytest.fixture(autouse=True)
def _rates(monkeypatch):
    monkeypatch.setattr(settings, "usd_zar_rate", 18.0)
    monkeypatch.setattr(settings, "ai_markup", 1.30)
    A._cache.clear()
    yield
    A._cache.clear()


@pytest.fixture()
def db(monkeypatch):
    d = FakeSupabase({"vula_ai_usage": [], "vula_ai_credits": []})
    monkeypatch.setattr(service, "_client", lambda: d)
    monkeypatch.setattr("vula.api.tenants.get_config", lambda t, fresh=False: {"plan": "starter"} if t == T else {})
    return d


def _use(db, day, usd, tenant=T, model="google/gemini-2.5-flash"):
    db.rows["vula_ai_usage"].append({"tenant_id": tenant, "day": day, "model": model, "est_cost_usd": usd})


def _credit(db, cents, paid_at, status="paid", tenant=T):
    db.rows["vula_ai_credits"].append({"id": f"c{len(db.rows['vula_ai_credits'])}", "tenant_id": tenant,
                                       "kind": "topup", "amount_cents": cents, "status": status,
                                       "requested_at": paid_at, "paid_at": paid_at if status == "paid" else None})


# ── the unit ────────────────────────────────────────────────────────────────────────────────

def test_usage_is_rand_at_vulas_price_in_cents():
    assert A.to_cents(1.0) == 2340              # $1 × 18 × 1.30 = R23.40
    assert A.to_cents(0.0) == 0
    assert A.rand(10_000) == "R100" and A.rand(2340) == "R23.40" and A.rand(1_234_500) == "R12 345"


def test_the_allowance_follows_the_plan_and_the_override(monkeypatch):
    cfgs = {"a": {"plan": "starter"}, "b": {"plan": "growth"}, "c": {"plan": "business"},
            "d": {"plan": "trial"}, "e": {"plan": "growth", "ai_allowance_cents": 5_000}, "f": {}}
    monkeypatch.setattr("vula.api.tenants.get_config", lambda t, fresh=False: cfgs[t])
    assert [A.allowance_cents(t) for t in "abcdef"] == [10_000, 30_000, 75_000, 10_000, 5_000, 10_000]


# ── the balance ─────────────────────────────────────────────────────────────────────────────

def test_paid_top_ups_carry_over_and_are_used_oldest_first():
    paid = [{"amount_cents": 10_000, "paid_at": "2026-10-05T10:00:00Z"},
            {"amount_cents": 25_000, "paid_at": "2026-11-02T10:00:00Z"}]
    # October: R160 used of R100 → R60 from the October top-up (R40 of it left)
    # November: R150 used → R50 over → from R40 + R250 → R240 left
    st = A.compute(10_000, {"2026-10": 16_000, "2026-11": 15_000}, paid, "2026-11")
    assert st["topups_left"] == 24_000 and st["remaining"] == 24_000 and not st["paused"]


def test_overuse_before_a_top_up_was_bought_is_not_charged_to_it():
    paid = [{"amount_cents": 10_000, "paid_at": "2026-11-03T08:00:00Z"}]
    st = A.compute(10_000, {"2026-09": 90_000, "2026-11": 5_000}, paid, "2026-11")
    assert st["topups_left"] == 10_000 and st["remaining"] == 15_000


def test_used_up_means_paused():
    st = A.compute(10_000, {"2026-11": 10_000}, [], "2026-11")
    assert st["paused"] and st["remaining"] == 0 and st["pct"] == 100


def test_status_reads_the_meter_and_a_request_doesnt_count_until_paid(db):
    month = A.month_of()
    _use(db, f"{month}-02", 1.0)                        # R23.40
    _use(db, f"{month}-03", 2.0)                        # R46.80
    _use(db, f"{month}-03", 9.0, tenant="other")
    _use(db, f"{month}-03", 0.0, model="llama3.1:8b")   # local: free
    _credit(db, 25_000, f"{month}-01T09:00:00+00:00", status="requested")
    st = A.status(T, fresh=True)
    assert st["used"] == 7020 and st["allowance"] == 10_000 and st["topups_left"] == 0
    assert st["remaining"] == 2980 and st["pct"] == 70 and not st["paused"]
    assert [r["amount"] for r in st["requested"]] == [25_000]
    assert A.daily_used(T, month) == [{"day": f"{month}-02", "cents": 2340}, {"day": f"{month}-03", "cents": 4680}]


def test_a_read_error_never_pauses(monkeypatch):
    monkeypatch.setattr(service, "_client", MagicMock(side_effect=RuntimeError("db down")))
    assert A.extras_paused(T) is False
    assert A.extras_paused("_unattributed") is False


# ── only the extras pause ───────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
@pytest.mark.parametrize("task,blocked", [("open_chat", True), ("research", True), ("research_plan", True),
                                          ("commerce_admin", False), ("doc_analysis", False), ("reasoning", False)])
async def test_used_up_pauses_ask_anything_and_research_only(monkeypatch, task, blocked):
    monkeypatch.setattr(settings, "openrouter_api_key", "k")
    monkeypatch.setattr(settings, "prefer_cloud_llm", False)
    monkeypatch.setattr(llm_router, "ollama_available", AsyncMock(return_value=False))   # local down
    monkeypatch.setattr(llm_router, "_spend_capped", lambda t: False)
    monkeypatch.setattr("vula.integrations.metering.get_request_tenant", lambda: T)
    monkeypatch.setattr(A, "extras_paused", lambda t: True)
    if blocked:
        with pytest.raises(llm_router.SpendCapReached):
            await llm_router.resolve_generation_route(task_type=task)
        assert llm_router.escalate_to_cloud("local_unreliable", task_type=task) is None
    else:
        model, _k, _b = await llm_router.resolve_generation_route(task_type=task)
        assert model.startswith("openrouter/")               # customers and the business never stop
        assert llm_router.escalate_to_cloud("local_unreliable", task_type=task) is not None


# ── telling the owner ───────────────────────────────────────────────────────────────────────

def test_the_owner_is_told_at_80_and_100():
    assert A.notice({"paused": False, "pct": 79, "used": 7_900}) is None
    lvl, msg = A.notice({"paused": False, "pct": 85, "used": 8_500})
    assert lvl == 80 and "85%" in msg and "customers" in msg
    lvl, msg = A.notice({"paused": True, "pct": 100, "used": 10_200})
    assert lvl == 100 and "used up" in msg and "Settings › AI usage" in msg


@pytest.mark.asyncio
async def test_each_notice_goes_once_a_month(db):
    month = A.month_of()
    _use(db, f"{month}-04", 3.6)                       # R84.24 of R100
    sent = AsyncMock(return_value=1)
    with patch("vula.integrations.notify.notify_team", sent):
        await A.after_spend(T)
    (args, kwargs), = sent.await_args_list
    assert args[1] == "ai_allowance" and kwargs["idem_key"] == f"ai80:{T}:{month}"


def test_ai_allowance_reaches_the_owner_even_if_nobody_subscribed():
    from vula.integrations import notify
    assert "ai_allowance" in notify.EVENTS and "ai_allowance" in notify.URGENT_EVENTS


# ── top-ups ─────────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_a_top_up_is_requested_by_an_owner_and_counts_once_paid(db):
    with patch("vula.integrations.platform_support.alert_operator", AsyncMock(return_value=True)) as op, \
         patch("vula.api.tenants.display_name", lambda t: "DIGG"):
        with pytest.raises(A.AllowanceError, match="owner or manager"):
            await A.request_topup(T, "m:m2", False, 25_000)
        with pytest.raises(A.AllowanceError, match="R100, R250 or R500"):
            await A.request_topup(T, "m:m1", True, 30_000)
        row = await A.request_topup(T, "m:m1", True, 25_000)
        again = await A.request_topup(T, "m:m1", True, 25_000)
    assert again["already"] and op.await_count == 1 and "R250" in op.await_args.args[0]
    assert A.status(T, fresh=True)["topups_left"] == 0
    paid = A.mark_paid(row["id"], "ian@vula", "EFT 1234")
    assert paid["status"] == "paid" and paid["reference"] == "EFT 1234"
    assert A.mark_paid(row["id"], "ian@vula") is None              # not twice
    assert A.status(T)["topups_left"] == 25_000                     # cache was cleared


def test_a_grant_counts_at_once_and_a_request_can_be_cancelled(db):
    g = A.grant(T, 5_000, "ian@vula", "Sorry for the outage")
    assert g["status"] == "paid" and A.status(T, fresh=True)["topups_left"] == 5_000
    with pytest.raises(A.AllowanceError):
        A.grant(T, 0, "ian@vula")
    _credit(db, 10_000, "2026-11-10T00:00:00Z", status="requested")
    assert A.cancel("c1")["status"] == "cancelled" and A.cancel("c1") is None


# ── routes ──────────────────────────────────────────────────────────────────────────────────

def _req():
    r = MagicMock()
    r.headers = {"authorization": "Bearer tok"}
    return r


@pytest.mark.asyncio
async def test_the_usage_route_shows_packs_and_requests_to_the_owner_only(db):
    from vula.api import me
    with patch("vula.api.tenant_auth.member_scope", AsyncMock(return_value={"full": False, "access": ["orders"]})), \
         patch("vula.api.tenant_auth.scope_allows", lambda s, need: False):
        staff = await me.my_ai_usage(T, _req())
    assert "packs" not in staff and "requested" not in staff and staff["can_top_up"] is False
    with patch("vula.api.tenant_auth.member_scope", AsyncMock(return_value={"full": True})), \
         patch("vula.api.tenant_auth.scope_allows", lambda s, need: True):
        owner = await me.my_ai_usage(T, _req())
    assert owner["packs"] == [10_000, 25_000, 50_000] and owner["allowance"] == 10_000


@pytest.mark.asyncio
async def test_the_top_up_route(db):
    from vula import identity
    from vula.api import me
    with patch("vula.api.tenant_auth.member_scope", AsyncMock(return_value={"full": False})), \
         patch("vula.api.tenant_auth.scope_allows", lambda s, need: False), \
         patch("vula.identity.person_for_login", AsyncMock(return_value=identity.Person(T, member_id="m2"))), \
         pytest.raises(HTTPException) as e:
        await me.request_ai_topup(T, me.TopUpIn(amount_cents=10_000), _req())
    assert e.value.status_code == 403
    with patch("vula.api.tenant_auth.member_scope", AsyncMock(return_value={"full": True})), \
         patch("vula.api.tenant_auth.scope_allows", lambda s, need: True), \
         patch("vula.identity.person_for_login", AsyncMock(return_value=identity.Person(T, member_id="m1"))), \
         patch("vula.integrations.platform_support.alert_operator", AsyncMock(return_value=True)), \
         patch("vula.api.tenants.display_name", lambda t: "DIGG"):
        out = await me.request_ai_topup(T, me.TopUpIn(amount_cents=10_000), _req())
    assert "R100" in out["message"] and db.rows["vula_ai_credits"][0]["requested_by"] == "m:m1"


@pytest.mark.asyncio
async def test_master_marks_paid_cancels_and_grants(db):
    from vula.api import master
    who = {"email": "ian@vula", "user_id": "u1"}
    _credit(db, 25_000, "2026-11-10T00:00:00Z", status="requested")
    with patch.object(master, "audit", MagicMock()) as audited:
        waiting = await master.master_ai_credits()
        assert [c["id"] for c in waiting["waiting"]] == ["c0"]
        out = await master.master_ai_credit_paid("c0", {"reference": "EFT"}, who)
        assert out["credit"]["status"] == "paid"
        with pytest.raises(HTTPException):
            await master.master_ai_credit_paid("c0", {}, who)
        g = await master.master_ai_credit_grant(T, {"amount_cents": 5_000, "note": "goodwill"}, who)
        assert g["credit"]["kind"] == "grant"
        one = await master.master_ai_credits(tenant_id=T)
    assert one["status"]["topups_left"] == 30_000 and len(one["credits"]) == 2
    assert [c.args[1] for c in audited.call_args_list] == ["ai_topup_paid", "ai_credit_granted"]


def test_the_home_card_carries_the_ai_line(db, monkeypatch):
    from vula.commerce import owner_setup
    monkeypatch.setattr(owner_setup, "_count", lambda *a, **k: 0)
    out = owner_setup.plan_view(T)
    assert out["ai"]["allowance"] == 10_000 and out["ai"]["paused"] is False
