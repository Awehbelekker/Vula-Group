"""The deals pipeline (vula/pipeline/service.py, vula/api/pipeline.py, core/skills/pipeline_tools.py,
migration 255): stages come from the business type, values are integer cents parsed server-side,
won/lost stamp closed_at, the board's numbers are computed server-side, the API and the WhatsApp
tools need the `pipeline` module, and WhatsApp changes go preview → confirm → read back."""
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from tests.test_listings import _Q   # the same in-memory Supabase stand-in
from vula.api import pipeline as api
from vula.listings import service as ls
from vula.pipeline import service as ps
from core.skills import pipeline_tools as pt


@pytest.fixture
def env(monkeypatch):
    db = SimpleNamespace(rows={})
    db.table = lambda name: _Q(db, name)
    monkeypatch.setattr(ls, "_client", lambda: db)
    monkeypatch.setattr(ps, "_client", lambda: db)
    from vula.api import tenants
    types = {"estate": "real_estate", "builder": "trades"}
    mods = {"estate": ["listings", "pipeline"], "builder": ["pipeline", "invoices"], "shop": ["products"]}
    monkeypatch.setattr(tenants, "get_config", lambda tid, **k: {"business_type": types.get(tid, "food")})
    monkeypatch.setattr(tenants, "enabled_modules", lambda tid: mods.get(tid, []))
    return db


def test_stages_follow_the_business_type(env):
    assert [s["key"] for s in ps.stages_for("estate")][:2] == ["viewing", "offer"]
    assert [s["key"] for s in ps.stages_for("builder")][:2] == ["lead", "quoted"]
    assert ps.stages_for("shop") == ps.STAGE_SETS["default"]
    for stages in ps.STAGE_SETS.values():
        assert [s["key"] for s in stages][-2:] == ["won", "lost"]


def test_bad_deals_are_refused_with_a_reason(env):
    with pytest.raises(ValueError, match="name"):
        ps.clean_deal("estate", {"title": " "}, creating=True)
    with pytest.raises(ValueError, match="Stage must be"):
        ps.clean_deal("builder", {"title": "x", "stage": "offer"}, creating=True)   # an estate stage
    with pytest.raises(ValueError, match="amount"):
        ps.clean_deal("estate", {"title": "x", "value": "lots"}, creating=True)
    with pytest.raises(ValueError, match="date"):
        ps.clean_deal("estate", {"title": "x", "expected_close": "next week"}, creating=True)
    row = ps.clean_deal("estate", {"title": "Priya", "value": "R1.24m", "contact_phone": "082 111 2222"}, creating=True)
    assert row["stage"] == "viewing" and row["value_cents"] == 124_000_000 and row["contact_phone"] == "27821112222"


@pytest.mark.asyncio
async def test_closing_stamps_and_reopening_clears(env):
    d = await ps.save_deal("estate", {"title": "Priya — Hollis", "value": "1.24m"})
    assert d["stage"] == "viewing" and not d.get("closed_at")
    d = await ps.save_deal("estate", {"id": d["id"], "stage": "won"})
    assert d["closed_at"]
    d = await ps.save_deal("estate", {"id": d["id"], "stage": "negotiation"})
    assert d["closed_at"] is None
    with pytest.raises(LookupError):
        await ps.save_deal("estate", {"id": "nope", "stage": "won"})


@pytest.mark.asyncio
async def test_a_deal_cant_point_at_another_businesss_listing(env):
    theirs = await ls.upsert_property("other", {"title": "Not ours"})
    with pytest.raises(ValueError, match="listing"):
        await ps.save_deal("estate", {"title": "x", "ref_type": "property", "ref_id": theirs["id"]})


def test_summary_numbers(env):
    deals = [
        {"stage": "viewing", "value_cents": 100_000_00},
        {"stage": "offer", "value_cents": 200_000_00},
        {"stage": "under_contract", "value_cents": 300_000_00},
        {"stage": "won", "value_cents": 50_000_00, "closed_at": ps._now()},
        {"stage": "lost", "value_cents": 70_000_00, "closed_at": ps._now()},
    ]
    s = ps.summary("estate", deals)
    assert s["open_count"] == 3 and s["open_value_cents"] == 600_000_00
    # 15% + 40% + 90%
    assert s["weighted_value_cents"] == 15_000_00 + 80_000_00 + 270_000_00
    assert s["won_this_month_count"] == 1 and s["won_this_month_cents"] == 50_000_00
    assert s["win_rate"] == 50
    assert s["by_stage"]["offer"] == {"count": 1, "value_cents": 200_000_00}


@pytest.mark.asyncio
async def test_api_needs_the_module_and_reads_back_moves(env):
    with pytest.raises(HTTPException) as off:
        await api.board("shop")
    assert off.value.status_code == 404
    d = (await api.save("builder", {"title": "Kitchen reno — Sam", "value": "85 000"}))["deal"]
    moved = await api.move("builder", d["id"], api.MoveIn(stage="quoted", position=2))
    assert moved["deal"]["stage"] == "quoted" and moved["deal"]["position"] == 2
    board = await api.board("builder")
    assert board["summary"]["by_stage"]["quoted"]["count"] == 1 and board["stages"][0]["key"] == "lead"
    with pytest.raises(HTTPException) as bad:
        await api.move("builder", d["id"], api.MoveIn(stage="under_contract"))
    assert bad.value.status_code == 422


@pytest.mark.asyncio
async def test_enquiry_becomes_one_deal(env, monkeypatch):
    async def quiet(*a, **k):
        return None
    monkeypatch.setattr(ls, "_tell_people", quiet)
    prop = await ls.upsert_property("estate", {"title": "Family home", "status": "active"})
    enq = await ls.create_enquiry("estate", {"name": "Thandi", "whatsapp": "27835551234",
                                             "property_slug": prop["slug"], "message": "Saturday?"})
    first = await api.from_enquiry("estate", enq["id"])
    again = await api.from_enquiry("estate", enq["id"])
    assert first["existing"] is False and again["existing"] is True
    assert again["deal"]["id"] == first["deal"]["id"]
    deal = first["deal"]
    assert deal["title"] == "Thandi — Family home" and deal["ref_id"] == prop["id"] and deal["stage"] == "viewing"
    assert env.rows["vula_property_enquiries"][0]["status"] == "contacted"


@pytest.mark.asyncio
async def test_whatsapp_save_deal_preview_then_confirm(env):
    ctx = {"tenant_id": "estate"}
    await ps.save_deal("estate", {"title": "Priya — Hollis", "value": "1.2m", "contact_name": "Priya"})
    prev = await pt.run_admin_tool("save_deal", {"deal": "priya", "stage": "Offer", "value": "R1.24m"}, ctx)
    assert prev["preview"] is True and prev["stage"] == "Viewing → Offer" and prev["value"] == "R1 200 000 → R1 240 000"
    assert env.rows["vula_deals"][0]["stage"] == "viewing"                  # nothing changed yet
    done = await pt.run_admin_tool("save_deal", {"deal": "priya", "stage": "Offer", "value": "R1.24m",
                                                 "confirm": True}, ctx)
    assert done["verified"] and "Offer" in done["reply_verbatim"] and "R1 240 000" in done["reply_verbatim"]
    assert env.rows["vula_deals"][0]["value_cents"] == 124_000_000
    assert "No stage called" in (await pt.run_admin_tool("save_deal", {"deal": "priya", "stage": "quoted"}, ctx))["error"]
    s = await pt.run_admin_tool("pipeline_summary", {"stage": "offer"}, ctx)
    assert s["open_deals"] == 1 and s["deals_in_stage"][0]["deal"] == "Priya — Hollis"


@pytest.mark.asyncio
async def test_pipeline_tools_only_with_the_module(env, monkeypatch):
    from core.skills import commerce_admin as ca
    from vula.api import tenants
    monkeypatch.setattr(tenants, "tenant_profile", lambda tid: {"known": True, "sells_products": True})
    monkeypatch.setattr(tenants, "uses_projects", lambda tid: False)
    names = lambda tid: {t["function"]["name"] for t in ca._tools_for(tid)}
    assert not pt.ADMIN_TOOL_NAMES & names("shop")
    assert pt.ADMIN_TOOL_NAMES <= names("builder")
    r = await ca.CommerceAdminSkill()._dispatch_tool("save_deal", {"title": "x"}, {"tenant_id": "shop"})
    assert "isn't available" in r["error"]
