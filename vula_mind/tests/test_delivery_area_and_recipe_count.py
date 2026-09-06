"""Two client-facing gaps found by reading real off-the-hook WhatsApp transcripts, 2026-09-06.

1. DELIVERY COVERAGE. The storefront prompt told the model to escalate EVERY out-of-area
   delivery question — an over-correction from the 2026-07-16 "Ja, ons lewer na Timbuktu"
   hallucination. Measured against production: "Do you deliver to timbucktu" (2026-07-17) and
   "Do you deliver to Bloemfontein?" (2026-09-02) both became handoffs, both expired unanswered,
   both left the customer with 48h of silence and then an apology. off-the-hook has 13 named
   delivery areas configured and no origin pin, so coverage() (radius-based) decided nothing.
   A Table View fish shop can answer Bloemfontein itself.

2. RECIPE COUNT. "Do you have a 3 hake recipes" returned one recipe (fixed 2026-09-01 with a
   count arg capped at 3). The cap then reintroduced the same silence one notch up: a real
   customer asked for "4 hake recipes" on 2026-09-02 and got three, with nothing said about the
   fourth. Capping is right; capping silently is not.
"""
from unittest.mock import AsyncMock, patch

import pytest

from vula.commerce import geo

# off-the-hook's real configured areas (read from production 2026-09-06).
OTH_AREAS = ['Table View', 'Sunningdale', 'West Beach', 'Parklands', 'Flamingo Vlei',
             'Sunset Beach', 'Big Bay', 'Bloubergstrand', 'Atlantic Beach', 'Melkbosstrand',
             'Milnerton', 'Edgemead', 'Southern Suburbs']


def _with_areas(areas):
    return patch("vula.commerce.order_workflow.get_order_settings",
                 lambda t: {"delivery_areas": areas})


# ── delivery coverage ───────────────────────────────────────────────────────────

@pytest.mark.parametrize("place", ["Bloemfontein", "Timbuktu", "Durban", "Johannesburg",
                                   "Century City", "Cape Town"])
def test_a_place_sharing_nothing_with_the_area_list_is_a_confident_no(place):
    """The tenant's own setting says ONLY these areas — so an unrelated name is answerable."""
    with _with_areas(OTH_AREAS):
        assert geo.area_verdict("off-the-hook", place)["verdict"] == "not_covered"


@pytest.mark.parametrize("place,matched", [
    ("Table View", "Table View"),
    ("Tableview", "Table View"),              # same name, no space
    ("table view north", "Table View"),       # more specific than the configured area
    ("Milnerton Ridge", "Milnerton"),
    ("Blouberg", "Bloubergstrand"),           # shortened form — prefix
    ("Melkbos", "Melkbosstrand"),
    ("Sunningdale Estate", "Sunningdale"),
])
def test_real_name_variants_are_still_covered(place, matched):
    with _with_areas(OTH_AREAS):
        v = geo.area_verdict("off-the-hook", place)
    assert v["verdict"] == "covered"
    assert v["matched"] == matched


@pytest.mark.parametrize("place", ["Beach", "Bay"])
def test_a_bare_generic_word_escalates_rather_than_guessing(place):
    """'Beach' is inside 'West Beach' and 'Bay' inside 'Big Bay', but neither is a name variant.
    A plain substring rule called these covered; a strict rule would call them a confident NO
    for areas that ARE covered. Both are wrong — this is what the unsure band is for."""
    with _with_areas(OTH_AREAS):
        assert geo.area_verdict("off-the-hook", place)["verdict"] == "unsure"


def test_no_configured_areas_means_no_verdict_so_the_caller_keeps_escalating():
    with _with_areas([]):
        assert geo.area_verdict("off-the-hook", "anywhere") is None


def test_unreadable_settings_do_not_raise():
    def _boom(_):
        raise RuntimeError("db down")

    with patch("vula.commerce.order_workflow.get_order_settings", _boom):
        assert geo.area_verdict("off-the-hook", "Table View") is None


# ── the tool the assistant actually calls ───────────────────────────────────────

def _skill():
    from core.skills.commerce_assistant import CommerceAssistantSkill
    return CommerceAssistantSkill()


def test_not_covered_tells_the_model_to_answer_not_to_escalate():
    """The whole point: Bloemfontein must produce an answer, not another dead handoff."""
    with _with_areas(OTH_AREAS):
        out = _skill()._exec_check_delivery_area("off-the-hook", {"place": "Bloemfontein"})
    assert out["verdict"] == "not_covered"
    assert "Do NOT call ask_team" in out["instruction"]
    assert out["areas"] == OTH_AREAS, "the reply must be able to name where we DO deliver"


def test_covered_names_the_matching_area():
    with _with_areas(OTH_AREAS):
        out = _skill()._exec_check_delivery_area("off-the-hook", {"place": "Blouberg"})
    assert out["verdict"] == "covered"
    assert out["matched_area"] == "Bloubergstrand"


def test_unsure_sends_it_to_a_human():
    with _with_areas(OTH_AREAS):
        out = _skill()._exec_check_delivery_area("off-the-hook", {"place": "Beach"})
    assert out["verdict"] == "unsure"
    assert "ask_team" in out["instruction"]


def test_unconfigured_tenant_still_escalates_and_never_guesses():
    with _with_areas([]):
        out = _skill()._exec_check_delivery_area("off-the-hook", {"place": "Bloemfontein"})
    assert out["verdict"] == "unsure"
    assert "ask_team" in out["instruction"]


def test_the_tool_is_declared_and_routed():
    import inspect
    from core.skills import commerce_assistant as ca
    names = [t["function"]["name"] for t in ca.STOREFRONT_TOOLS] \
        if hasattr(ca, "STOREFRONT_TOOLS") else []
    src = inspect.getsource(ca)
    assert '"name": "check_delivery_area"' in src
    assert 'if name == "check_delivery_area"' in src
    if names:
        assert "check_delivery_area" in names


def test_the_prompt_no_longer_orders_a_blanket_escalation():
    import inspect
    from core.skills import commerce_assistant as ca
    src = inspect.getsource(ca)
    assert "delivery ANYWHERE else, do NOT say yes or no" not in src, \
        "the blanket-escalation instruction is what produced the dead Bloemfontein handoff"


# ── recipe count cap ────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_asking_for_more_than_the_cap_is_admitted_not_hidden():
    skill = _skill()
    with patch("vula.commerce.service.list_products", AsyncMock(return_value=[])), \
         patch("core.skills.commerce_assistant.resolve_generation_route",
               AsyncMock(return_value=("m", "k", None))), \
         patch("litellm.acompletion", AsyncMock(return_value=type("R", (), {
             "choices": [type("C", (), {"message": type("M", (), {"content": "1. A\n2. B\n3. C"})()})()]
         })())):
        out = await skill._exec_suggest_recipe("off-the-hook", {"dish": "hake", "count": 4})
    assert out["recipes_returned"] == 3
    assert out["customer_asked_for"] == 4
    assert "must_tell_customer" in out
    assert "do not pretend" in out["must_tell_customer"].lower()


@pytest.mark.asyncio
async def test_a_count_within_the_cap_says_nothing_extra():
    skill = _skill()
    with patch("vula.commerce.service.list_products", AsyncMock(return_value=[])), \
         patch("core.skills.commerce_assistant.resolve_generation_route",
               AsyncMock(return_value=("m", "k", None))), \
         patch("litellm.acompletion", AsyncMock(return_value=type("R", (), {
             "choices": [type("C", (), {"message": type("M", (), {"content": "1. A\n2. B\n3. C"})()})()]
         })())):
        out = await skill._exec_suggest_recipe("off-the-hook", {"dish": "hake", "count": 3})
    assert "must_tell_customer" not in out
    assert "customer_asked_for" not in out
