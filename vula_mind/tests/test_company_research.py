"""Company research for the brief: companies only, the engine's own words with links, fail-open."""
import asyncio

import pytest

from vula import company_research as cr

PAGE = """
<div class="result results_links results_links_deep web-result">
  <a rel="nofollow" class="result__a" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Facme.co.za%2F&amp;rut=x">Acme <b>Builders</b> | Commercial construction</a>
  <a class="result__snippet" href="x">Acme Builders is a Cape Town contractor specialising in retail fit-outs &amp; warehouses.</a>
</div>
<div class="result"><a class="result__a" href="https://duckduckgo.com/y.js?ad_domain=ads.example">Ad</a>
  <a class="result__snippet" href="x">An advert</a></div>
<div class="result">
  <a rel="nofollow" class="result__a" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fwww.linkedin.com%2Fcompany%2Facme&amp;rut=y">Acme Builders - LinkedIn</a>
  <a class="result__snippet" href="x">51-200 employees. Construction.</a>
</div>
"""


def test_parse_keeps_title_description_and_real_link():
    out = cr.parse(PAGE)
    assert out == [
        {"title": "Acme Builders | Commercial construction", "url": "https://acme.co.za/",
         "snippet": "Acme Builders is a Cape Town contractor specialising in retail fit-outs & warehouses."},
        {"title": "Acme Builders - LinkedIn", "url": "https://www.linkedin.com/company/acme",
         "snippet": "51-200 employees. Construction."}]


@pytest.mark.parametrize("name,domain,company", [
    ("Acme Builders (Pty) Ltd", None, True), ("Bongi Plumbing", None, True), ("Smith & Sons", None, True),
    ("Sam Naidoo", None, False), ("Thandi", None, False),
    ("Sam Naidoo", "naidoo-architects.co.za", True), ("Sam Naidoo", "gmail.com", False),
])
def test_only_companies_are_looked_up(name, domain, company):
    assert cr.looks_like_company(name, domain) is company


@pytest.fixture
def fake_web(monkeypatch):
    calls = []

    class Client:
        def __init__(self, *a, **k): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False

        async def post(self, url, data=None):
            calls.append(data["q"])
            return type("R", (), {"status_code": 200, "text": PAGE})()
    monkeypatch.setattr(cr.httpx, "AsyncClient", Client)
    cr._CACHE.clear()
    return calls


def test_a_person_is_never_searched(fake_web):
    assert asyncio.run(cr.lookup("t1", "Sam Naidoo")) == [] and fake_web == []


def test_search_sends_only_the_name_and_is_cached(fake_web):
    out = asyncio.run(cr.lookup("t1", "Acme Builders"))
    assert len(out) == 2 and fake_web == ['"Acme Builders" South Africa']
    asyncio.run(cr.lookup("t1", "acme builders"))
    assert len(fake_web) == 1                                          # a week's cache


def test_their_own_domain_narrows_the_search(fake_web):
    asyncio.run(cr.lookup("t1", "Acme Builders", "acme.co.za"))
    assert fake_web == ['"Acme Builders" site:acme.co.za']


def test_no_answer_means_no_section(monkeypatch):
    class Boom:
        def __init__(self, *a, **k): pass
        async def __aenter__(self): raise RuntimeError("timeout")
        async def __aexit__(self, *a): return False
    monkeypatch.setattr(cr.httpx, "AsyncClient", Boom)
    monkeypatch.setattr(cr, "domain_for", lambda t, n: None)
    cr._CACHE.clear()
    assert asyncio.run(cr.section("t1", "Acme Builders")) == ""


def test_render_marks_it_as_unchecked(fake_web, monkeypatch):
    monkeypatch.setattr(cr, "domain_for", lambda t, n: None)
    text = asyncio.run(cr.section("t1", "Acme Builders"))
    assert text.startswith("🌐 On the web (public results, not checked by Vula):")
    assert "• Acme Builders | Commercial construction — Acme Builders is a Cape Town contractor" in text
    assert "  https://acme.co.za/" in text


def test_brief_me_adds_the_web_section(monkeypatch):
    from core.skills.commerce_admin import CommerceAdminSkill
    skill = CommerceAdminSkill()
    monkeypatch.setattr(CommerceAdminSkill, "_meeting",
                        lambda self, tid, args, ctx: {"reply_verbatim": "brief", "brief_for": "Acme Builders"})

    async def web(tid, name):
        return "🌐 On the web: Acme"
    monkeypatch.setattr(cr, "section", web)
    out = asyncio.run(skill._dispatch_tool("meeting", {"action": "brief"}, {"tenant_id": "t1", "phone": "2782"}))
    assert out["reply_verbatim"] == "brief\n🌐 On the web: Acme" and "brief_for" not in out
