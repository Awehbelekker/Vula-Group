"""Tests for the conversational page-building tools added to commerce_admin
(list_storefront_pages / draft_storefront_page / add_storefront_section).

Mirrors test_commerce_admin_gate.py's style: instantiate CommerceAdminSkill directly, monkeypatch
the modules it calls into, call the private tool methods directly. Focus is on the trust
guarantees these tools were specifically designed around: no write happens without confirm=true,
a confirmed save ALWAYS lands as status="draft" (never auto-publishes, even if the page was
already published), sales reps never get these tools regardless of tenant modules, and the
`pages` module gate actually hides them for a tenant that doesn't have it enabled.
"""
import pytest

import core.skills.commerce_admin as ca
from core.skills.commerce_admin import CommerceAdminSkill

TID = "test-tenant"


@pytest.fixture
def skill():
    return CommerceAdminSkill()


SAMPLE_CONTENT = [
    {"type": "Hero", "props": {"id": "t-hero", "title": "Fresh, local, delivered", "subtitle": "Order before 10am."}},
]


def _mock_page_read(monkeypatch, page):
    import vula.api.commerce as api_commerce

    async def _admin_get_page(tid, slug):
        return page
    monkeypatch.setattr(api_commerce, "admin_get_page", _admin_get_page)


def _mock_page_write(monkeypatch):
    import vula.api.commerce as api_commerce
    calls = []

    async def _upsert_page(tid, slug, body):
        calls.append({"tid": tid, "slug": slug, "body": body})
        return {"ok": True}
    monkeypatch.setattr(api_commerce, "upsert_page", _upsert_page)
    return calls


def _mock_refine(monkeypatch, returned_content):
    import vula.commerce.page_copy as page_copy

    async def _refine(tid, content, instruction, description=""):
        return {"content": returned_content}
    monkeypatch.setattr(page_copy, "refine_page_copy", _refine)


# ── list_storefront_pages ──────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_list_storefront_pages_returns_pages(skill, monkeypatch):
    import vula.api.commerce as api_commerce

    async def _admin_list_pages(tid):
        return {"pages": [{"slug": "home", "title": "Home", "status": "published"}]}
    monkeypatch.setattr(api_commerce, "admin_list_pages", _admin_list_pages)

    res = await skill._list_storefront_pages(TID)
    assert res["pages"] == [{"slug": "home", "title": "Home", "status": "published"}]


@pytest.mark.asyncio
async def test_list_storefront_pages_empty(skill, monkeypatch):
    import vula.api.commerce as api_commerce

    async def _admin_list_pages(tid):
        return {"pages": []}
    monkeypatch.setattr(api_commerce, "admin_list_pages", _admin_list_pages)

    res = await skill._list_storefront_pages(TID)
    assert "message" in res


# ── draft_storefront_page ──────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_draft_storefront_page_without_confirm_makes_no_write(skill, monkeypatch):
    _mock_page_read(monkeypatch, {"title": "Home", "status": "published",
                                   "puck_data": {"content": SAMPLE_CONTENT}, "seo": {}})
    _mock_refine(monkeypatch, [{"type": "Hero", "props": {"id": "t-hero", "title": "We now deliver weekends", "subtitle": "Order before 10am."}}])
    write_calls = _mock_page_write(monkeypatch)

    res = await skill._draft_storefront_page(TID, "home", "mention weekend delivery", confirm=False)
    assert res.get("preview") is True
    assert write_calls == []  # no write happened
    assert any(c["field"] == "title" for c in res["changes"])


@pytest.mark.asyncio
async def test_draft_storefront_page_confirm_saves_as_draft_even_if_page_was_published(skill, monkeypatch):
    _mock_page_read(monkeypatch, {"title": "Home", "status": "published",
                                   "puck_data": {"content": SAMPLE_CONTENT}, "seo": {}})
    new_content = [{"type": "Hero", "props": {"id": "t-hero", "title": "We now deliver weekends", "subtitle": "Order before 10am."}}]
    _mock_refine(monkeypatch, new_content)
    write_calls = _mock_page_write(monkeypatch)

    res = await skill._draft_storefront_page(TID, "home", "mention weekend delivery", confirm=True)
    assert res.get("saved") is True
    assert len(write_calls) == 1
    assert write_calls[0]["body"].status == "draft"  # never auto-published, even though page was published
    assert write_calls[0]["body"].puck_data["content"] == new_content


@pytest.mark.asyncio
async def test_draft_storefront_page_nonexistent_page_is_a_clean_error(skill, monkeypatch):
    _mock_page_read(monkeypatch, {"puck_data": {}, "status": "draft"})  # admin_get_page's own default for a missing page
    write_calls = _mock_page_write(monkeypatch)

    res = await skill._draft_storefront_page(TID, "does-not-exist", "add something", confirm=True)
    assert "error" in res
    assert write_calls == []


@pytest.mark.asyncio
async def test_draft_storefront_page_requires_slug_and_instruction(skill):
    assert "error" in await skill._draft_storefront_page(TID, "", "do something", confirm=False)
    assert "error" in await skill._draft_storefront_page(TID, "home", "", confirm=False)


# ── add_storefront_section ─────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_add_storefront_section_rejects_unknown_feature(skill, monkeypatch):
    write_calls = _mock_page_write(monkeypatch)
    res = await skill._add_storefront_section(TID, "home", "not_a_real_feature", confirm=True)
    assert "error" in res
    assert write_calls == []


@pytest.mark.asyncio
async def test_add_storefront_section_without_confirm_makes_no_write(skill, monkeypatch):
    _mock_page_read(monkeypatch, {"title": "Home", "status": "draft",
                                   "puck_data": {"content": SAMPLE_CONTENT}, "seo": {}})
    write_calls = _mock_page_write(monkeypatch)

    res = await skill._add_storefront_section(TID, "home", "booking", confirm=False)
    assert res.get("preview") is True
    assert res.get("adding") == "Booking"
    assert write_calls == []


@pytest.mark.asyncio
async def test_add_storefront_section_confirm_saves_as_draft(skill, monkeypatch):
    _mock_page_read(monkeypatch, {"title": "Home", "status": "published",
                                   "puck_data": {"content": SAMPLE_CONTENT}, "seo": {}})
    import vula.commerce.page_copy as page_copy

    async def _refine(tid, content, instruction, description=""):
        return {"content": content}  # pass through — just proves the flow completes and saves
    monkeypatch.setattr(page_copy, "refine_page_copy", _refine)
    write_calls = _mock_page_write(monkeypatch)

    res = await skill._add_storefront_section(TID, "home", "faq", confirm=True)
    assert res.get("saved") is True
    assert res.get("added") == "FAQ"
    assert len(write_calls) == 1
    assert write_calls[0]["body"].status == "draft"
    saved_content = write_calls[0]["body"].puck_data["content"]
    assert len(saved_content) == len(SAMPLE_CONTENT) + 1
    assert saved_content[-1]["type"] == "FAQ"


# ── role/module gating ─────────────────────────────────────────────────────────

def test_sales_rep_never_gets_page_tools(monkeypatch):
    tools = ca._tools_for(TID, role="sales_rep")
    names = {t["function"]["name"] for t in tools}
    assert not names & {"list_storefront_pages", "draft_storefront_page", "add_storefront_section"}


def test_owner_gets_page_tools_when_pages_module_enabled(monkeypatch):
    monkeypatch.setattr("vula.api.tenants.enabled_modules", lambda tid: ["pages", "orders"])
    tools = ca._tools_for(TID, role=None)
    names = {t["function"]["name"] for t in tools}
    assert {"list_storefront_pages", "draft_storefront_page", "add_storefront_section"} <= names


def test_owner_does_not_get_page_tools_without_pages_module(monkeypatch):
    monkeypatch.setattr("vula.api.tenants.enabled_modules", lambda tid: ["orders", "invoices"])
    tools = ca._tools_for(TID, role=None)
    names = {t["function"]["name"] for t in tools}
    assert not names & {"list_storefront_pages", "draft_storefront_page", "add_storefront_section"}


def test_owner_gets_page_tools_when_no_modules_configured_yet(monkeypatch):
    # _tools_for's own documented behavior: no config yet -> show everything.
    monkeypatch.setattr("vula.api.tenants.enabled_modules", lambda tid: [])
    tools = ca._tools_for(TID, role=None)
    names = {t["function"]["name"] for t in tools}
    assert {"list_storefront_pages", "draft_storefront_page", "add_storefront_section"} <= names
