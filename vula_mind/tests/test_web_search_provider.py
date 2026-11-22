"""16 Nov 2026: web search found nothing in production — DuckDuckGo's HTML page gave Railway's
servers a page with no results and no error, so research, build-ups and market prices never found
a source. With SERPER_API_KEY set, every search goes to Serper (Google results, South Africa),
with DuckDuckGo as the fallback."""
import logging

import pytest

from core.skills import web_search as ws

KEY = "test-serper-key-not-real"


class _Resp:
    def __init__(self, status=200, data=None, text=""):
        self.status_code, self._data, self.text = status, data, text

    def json(self):
        return self._data


class _Client:
    """Stands in for httpx.AsyncClient; records each request."""
    calls: list = []
    serper = _Resp(200, {"organic": [
        {"title": "Clay stock brick | Builders", "link": "https://www.builders.co.za/brick", "snippet": "R5.49 each"},
        {"title": "", "link": "https://no-title.example"},
        {"title": "Not a link", "link": "ftp://x"},
        {"title": "Corobrik brick calculator", "link": "https://www.corobrik.co.za/calc", "snippet": "52 bricks per m2"},
    ]})
    ddg = _Resp(200, text='<a class="result__a" href="https://ddg.example/page">DDG page</a>')

    def __init__(self, *a, **k):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def post(self, url, headers=None, json=None, data=None):
        _Client.calls.append({"url": url, "headers": headers or {}, "json": json, "data": data})
        if url == ws.SERPER_URL:
            if isinstance(_Client.serper, Exception):
                raise _Client.serper
            return _Client.serper
        return _Client.ddg


@pytest.fixture()
def net(monkeypatch):
    _Client.calls = []
    monkeypatch.setattr(ws.httpx, "AsyncClient", _Client)
    from config import settings
    monkeypatch.setattr(settings, "serper_api_key", KEY, raising=False)
    monkeypatch.setattr(settings, "search_country", "za", raising=False)
    yield settings
    _Client.serper = _Resp(200, {"organic": [
        {"title": "Clay stock brick | Builders", "link": "https://www.builders.co.za/brick", "snippet": "R5.49 each"},
        {"title": "", "link": "https://no-title.example"},
        {"title": "Not a link", "link": "ftp://x"},
        {"title": "Corobrik brick calculator", "link": "https://www.corobrik.co.za/calc", "snippet": "52 bricks per m2"},
    ]})


async def test_with_a_key_searches_go_to_serper_for_south_africa(net):
    hits = await ws._ddg_search("clay stock brick price", limit=5)
    assert [h["url"] for h in hits] == ["https://www.builders.co.za/brick", "https://www.corobrik.co.za/calc"]
    assert hits[0]["title"] == "Clay stock brick | Builders" and hits[0]["snippet"] == "R5.49 each"
    call = _Client.calls[0]
    assert call["url"] == "https://google.serper.dev/search"
    assert call["headers"]["X-API-KEY"] == KEY
    assert call["json"] == {"q": "clay stock brick price", "gl": "za", "num": 5}
    assert len(_Client.calls) == 1                      # DuckDuckGo not asked


async def test_the_limit_is_kept(net):
    hits = await ws._ddg_search("brick", limit=1)
    assert len(hits) == 1 and _Client.calls[0]["json"]["num"] == 1


async def test_without_a_key_duckduckgo_as_before(net):
    net.serper_api_key = ""
    hits = await ws._ddg_search("brick", limit=3)
    assert hits == [{"title": "DDG page", "url": "https://ddg.example/page"}]
    assert all(c["url"] != ws.SERPER_URL for c in _Client.calls)


@pytest.mark.parametrize("serper", [_Resp(403, {}), _Resp(200, {"organic": []}), TimeoutError("slow")])
async def test_serper_failing_or_empty_falls_back_to_duckduckgo(net, serper):
    _Client.serper = serper
    hits = await ws._ddg_search("brick", limit=3)
    assert hits == [{"title": "DDG page", "url": "https://ddg.example/page"}]
    assert [c["url"] for c in _Client.calls] == [ws.SERPER_URL, "https://html.duckduckgo.com/html/"]


async def test_logs_name_the_provider_never_the_key_or_the_query(net, caplog):
    caplog.set_level(logging.INFO, logger="core.skills.web_search")
    _Client.serper = _Resp(401, {})
    await ws._ddg_search("Judy's private question about cement", limit=3)
    text = caplog.text
    assert "serper answered 401" in text and "duckduckgo 1 results" in text
    assert KEY not in text and "Judy" not in text and "cement" not in text


async def test_duckduckgos_empty_bot_page_is_logged(net, caplog):
    net.serper_api_key = ""
    _Client.ddg = _Resp(202, text="<html>please verify you are human</html>")
    caplog.set_level(logging.INFO, logger="core.skills.web_search")
    try:
        assert await ws._ddg_search("brick", limit=3) == []
    finally:
        _Client.ddg = _Resp(200, text='<a class="result__a" href="https://ddg.example/page">DDG page</a>')
    assert "duckduckgo gave a page with no results (HTTP 202)" in caplog.text
