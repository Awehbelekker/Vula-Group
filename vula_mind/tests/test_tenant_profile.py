"""One answer to "what kind of business is this?" (2026-09-29 architecture review).

Six signals used to decide tenant behaviour separately and contradicted each other: Gerflor (a
flooring sales rep) was typed "trades" and got architecture routing and a shop Home; DIGG's
owner was offered order/stock tools and its "stock" question went to the customer shop
assistant. Real production shapes (vula_tenant_config, 29 Sep).
"""
import pytest

from vula.api import tenants

CFG = {
    "digg-demo": {"tenant_id": "digg-demo", "display_name": "DIGG Architecture", "business_type": "services",
                  "modules": ["invoices", "projects", "documents", "finances", "fieldops", "followups",
                              "reports", "estimating", "ai_draft", "training", "workspace"],
                  "aliases": ["Aweh Be Lekker t/a DIGG Collection"]},
    "off-the-hook": {"tenant_id": "off-the-hook", "display_name": "Off the Hook", "business_type": "food",
                     "modules": ["products", "orders", "payments", "invoices", "delivery", "crm"]},
    "gerflor": {"tenant_id": "gerflor", "display_name": "Gerflor — Western Cape Sales",
                "business_type": "rep", "modules": ["crm", "followups", "documents", "team", "reports"]},
}


@pytest.fixture(autouse=True)
def cfg(monkeypatch):
    monkeypatch.setattr(tenants, "get_config", lambda tid, fresh=False: dict(CFG.get(tid, {})))


def test_each_business_is_seen_for_what_it_is():
    digg, oth, ger = (tenants.tenant_profile(t) for t in ("digg-demo", "off-the-hook", "gerflor"))
    assert digg["uses_projects"] and not digg["sells_products"] and not digg["is_rep_business"]
    assert oth["sells_products"] and not oth["uses_projects"]
    assert ger["is_rep_business"] and not ger["sells_products"] and not ger["uses_projects"]
    unknown = tenants.tenant_profile("nobody")
    assert not unknown["known"] and unknown["sells_products"]          # unknown → old behaviour


def test_what_vula_says_it_does_follows_the_business():
    assert "projects" in tenants.what_i_do("digg-demo")
    assert "orders" in tenants.what_i_do("off-the-hook")
    assert "samples" in tenants.what_i_do("gerflor")
    assert "construction" not in tenants.what_i_do("off-the-hook")


def test_only_known_business_types_are_stored():
    assert tenants.valid_business_type("Rep") == "rep"
    assert tenants.valid_business_type("flooring stuff") == "other"
    assert tenants.valid_modules(["crm", "bogus", 3, "projects"]) == ["crm", "projects"]
    from vula.api.onboarding import _map_business_type
    assert _map_business_type("Flooring sales rep for Gerflor") == "rep"
    assert _map_business_type("Seafood retail") == "food"


def test_order_and_stock_tools_only_for_a_business_that_sells():
    from core.skills.commerce_admin import _tools_for
    names = lambda tid: {t["function"]["name"] for t in _tools_for(tid, role=None)}
    assert {"recent_orders", "update_stock", "create_manual_order"} <= names("off-the-hook")
    assert not {"recent_orders", "update_stock", "create_manual_order"} & names("digg-demo")
    assert "project_profit" in names("digg-demo") and "project_profit" not in names("off-the-hook")
    assert "find_document" in names("digg-demo")                      # its own documents, always


def test_a_project_business_stock_question_is_not_sent_to_the_shop_assistant():
    from core.hrm.orchestrator import HRMOrchestrator
    o = HRMOrchestrator.__new__(HRMOrchestrator)
    assert o._keyword_skill("how much stock do we have of the wall panels", "off-the-hook") == "commerce_assistant"
    assert o._keyword_skill("how much stock do we have of the wall panels", "digg-demo") != "commerce_assistant"
    assert o._keyword_skill("do we have stock of Creation 30", "gerflor") != "commerce_assistant"
    # a rep is no longer "trades": no architecture weak-keyword routing
    assert not o._architecture_weak_ok("gerflor") and o._architecture_weak_ok("digg-demo")


def test_the_businesss_other_names_are_never_a_filing_clue():
    from vula.integrations import doc_filing
    own = doc_filing._own_names("digg-demo")
    assert doc_filing._is_own("AWEH BE LEKKER T/A DIGG COLLECTION", own)
    assert doc_filing._is_own("DIGG", own)
    assert not doc_filing._is_own("Solid Cape (Pty) Ltd", own)
    from vula.commerce import allocation
    # "digg" is DIGG's own name (from its profile), not a hardcoded word, and names no project
    assert ("prefix", "digg") not in allocation.signals("DIGG SAMPLES", tenant_id="digg-demo")
    assert ("prefix", "hpc") in allocation.signals("HPC DOORS", tenant_id="digg-demo")


def test_a_failed_config_read_is_not_cached_as_unconfigured(monkeypatch):
    monkeypatch.undo()                                   # the real get_config
    calls = {"n": 0}

    class Boom:
        def table(self, name):
            calls["n"] += 1
            raise RuntimeError("supabase blip")
    tenants._CACHE.pop("blip-co", None)
    monkeypatch.setattr(tenants, "_client", lambda: Boom())
    assert tenants.get_config("blip-co") == {}
    assert "blip-co" not in tenants._CACHE              # next read tries again
    tenants.get_config("blip-co")
    assert calls["n"] == 2
