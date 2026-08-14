"""Tests for the SSRF-safe reference-URL fetcher and feature analysis
(vula/commerce/reference_url.py).

The SSRF tests are the important ones here — they must cover the actual bypass risk (a hostname
that DNS-RESOLVES to an internal/metadata address, and a redirect chain that LANDS on one), not
just an obviously-private literal URL, since checking only the URL string and not what it
resolves to is exactly the gap found in the existing (unrelated, admin-only) web_scraper.py.
"""
import pytest

import vula.commerce.reference_url as ru


# --- _is_safe_ip ---

@pytest.mark.parametrize("ip", [
    "127.0.0.1", "10.0.0.5", "172.16.0.1", "192.168.1.1",
    "169.254.169.254",  # AWS/GCP/Azure cloud-metadata endpoint — the classic SSRF target
    "0.0.0.0", "::1", "fe80::1",
])
def test_is_safe_ip_rejects_private_and_special_ranges(ip):
    assert ru._is_safe_ip(ip) is False


@pytest.mark.parametrize("ip", ["8.8.8.8", "1.1.1.1", "93.184.216.34"])
def test_is_safe_ip_accepts_public_ips(ip):
    assert ru._is_safe_ip(ip) is True


# --- _validate_scheme_and_hostname ---

def test_rejects_non_http_scheme():
    with pytest.raises(ru.UnsafeUrlError):
        ru._validate_scheme_and_hostname("file:///etc/passwd")


def test_rejects_ftp_scheme():
    with pytest.raises(ru.UnsafeUrlError):
        ru._validate_scheme_and_hostname("ftp://example.com/x")


def test_accepts_https():
    assert ru._validate_scheme_and_hostname("https://example.com/page") == "example.com"


# --- _resolve_and_validate — the critical "DNS resolves to internal" case ---

def _mock_getaddrinfo(monkeypatch, mapping):
    """mapping: {hostname: ip_str}. Any hostname not in mapping raises (simulates NXDOMAIN)."""
    def _fake(hostname, *a, **kw):
        if hostname not in mapping:
            raise OSError(f"no such host {hostname}")
        return [(2, 1, 6, "", (mapping[hostname], 0))]
    monkeypatch.setattr("socket.getaddrinfo", _fake)


@pytest.mark.asyncio
async def test_resolve_and_validate_rejects_hostname_that_resolves_to_metadata_ip(monkeypatch):
    # A hostname that LOOKS public but a malicious/compromised DNS record points at the cloud
    # metadata endpoint — the actual bypass a URL-string-only check would miss.
    _mock_getaddrinfo(monkeypatch, {"evil-but-public-looking.example.com": "169.254.169.254"})
    with pytest.raises(ru.UnsafeUrlError):
        await ru._resolve_and_validate("evil-but-public-looking.example.com")


@pytest.mark.asyncio
async def test_resolve_and_validate_rejects_hostname_that_resolves_to_private_ip(monkeypatch):
    _mock_getaddrinfo(monkeypatch, {"internal.example.com": "10.1.2.3"})
    with pytest.raises(ru.UnsafeUrlError):
        await ru._resolve_and_validate("internal.example.com")


@pytest.mark.asyncio
async def test_resolve_and_validate_allows_public_resolving_hostname(monkeypatch):
    _mock_getaddrinfo(monkeypatch, {"example.com": "93.184.216.34"})
    await ru._resolve_and_validate("example.com")  # must not raise


# --- safe_fetch_html — redirect re-validation, size cap, hop cap ---

class _FakeStreamResponse:
    def __init__(self, status_code, headers=None, body=b""):
        self.status_code = status_code
        self.headers = headers or {}
        self._body = body

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    async def aiter_bytes(self):
        # Split into chunks so the size-cap check has more than one iteration to work with.
        chunk_size = 1024
        for i in range(0, len(self._body), chunk_size):
            yield self._body[i:i + chunk_size]

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False


class _FakeStreamCM:
    def __init__(self, response):
        self._response = response

    async def __aenter__(self):
        return self._response

    async def __aexit__(self, *a):
        return False


class _FakeAsyncClient:
    def __init__(self, response):
        self._response = response

    def stream(self, method, url):
        return _FakeStreamCM(self._response)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False


def _mock_httpx_sequence(monkeypatch, responses):
    """Each successive httpx.AsyncClient(...) construction (one per redirect hop in
    safe_fetch_html's loop) returns the next response in order."""
    state = {"n": 0}

    def _make_client(*a, **kw):
        resp = responses[min(state["n"], len(responses) - 1)]
        state["n"] += 1
        return _FakeAsyncClient(resp)
    monkeypatch.setattr("vula.commerce.reference_url.httpx.AsyncClient", _make_client)
    return state


@pytest.mark.asyncio
async def test_safe_fetch_html_happy_path(monkeypatch):
    _mock_getaddrinfo(monkeypatch, {"example.com": "93.184.216.34"})
    _mock_httpx_sequence(monkeypatch, [
        _FakeStreamResponse(200, body=b"<html><title>Hi</title><body>hello</body></html>"),
    ])
    html = await ru.safe_fetch_html("https://example.com/")
    assert "hello" in html


@pytest.mark.asyncio
async def test_safe_fetch_html_follows_redirect_to_safe_target(monkeypatch):
    _mock_getaddrinfo(monkeypatch, {"example.com": "93.184.216.34", "example.net": "93.184.216.35"})
    _mock_httpx_sequence(monkeypatch, [
        _FakeStreamResponse(302, headers={"location": "https://example.net/final"}),
        _FakeStreamResponse(200, body=b"<html>final page</html>"),
    ])
    html = await ru.safe_fetch_html("https://example.com/")
    assert "final page" in html


@pytest.mark.asyncio
async def test_safe_fetch_html_rejects_redirect_landing_on_internal_ip(monkeypatch):
    # The initial URL looks entirely public; only the REDIRECT TARGET resolves internally.
    # If redirects weren't re-validated, this would be a live SSRF bypass.
    _mock_getaddrinfo(monkeypatch, {
        "public-looking.example.com": "93.184.216.34",
        "internal-target.example.com": "127.0.0.1",
    })
    _mock_httpx_sequence(monkeypatch, [
        _FakeStreamResponse(302, headers={"location": "https://internal-target.example.com/secret"}),
    ])
    with pytest.raises(ru.UnsafeUrlError):
        await ru.safe_fetch_html("https://public-looking.example.com/")


@pytest.mark.asyncio
async def test_safe_fetch_html_enforces_redirect_hop_cap(monkeypatch):
    _mock_getaddrinfo(monkeypatch, {f"hop{i}.example.com": "93.184.216.34" for i in range(10)})
    # Every hop redirects to the next — more hops than MAX_REDIRECTS allows.
    responses = [
        _FakeStreamResponse(302, headers={"location": f"https://hop{i+1}.example.com/"})
        for i in range(ru.MAX_REDIRECTS + 2)
    ]
    _mock_httpx_sequence(monkeypatch, responses)
    with pytest.raises(ru.UnsafeUrlError):
        await ru.safe_fetch_html("https://hop0.example.com/")


@pytest.mark.asyncio
async def test_safe_fetch_html_enforces_size_cap(monkeypatch):
    _mock_getaddrinfo(monkeypatch, {"example.com": "93.184.216.34"})
    huge_body = b"x" * (ru.MAX_RESPONSE_BYTES + 1024)
    _mock_httpx_sequence(monkeypatch, [_FakeStreamResponse(200, body=huge_body)])
    with pytest.raises(ru.UnsafeUrlError):
        await ru.safe_fetch_html("https://example.com/")


# --- analyze_reference_urls ---

@pytest.mark.asyncio
async def test_analyze_reference_urls_caps_at_max_urls(monkeypatch):
    calls = []

    async def _fake_fetch(url):
        calls.append(url)
        return "<html><title>t</title>body</html>"

    monkeypatch.setattr(ru, "safe_fetch_html", _fake_fetch)

    async def _fake_route(*a, **kw):
        return ("fake-model", None, None)
    monkeypatch.setattr("core.llm_router.resolve_generation_route", _fake_route)

    class _Msg:
        content = '{"features_found": [], "notes": "n/a"}'
    class _Choice:
        message = _Msg()
    class _Resp:
        choices = [_Choice()]
    async def _fake_completion(*a, **kw):
        return _Resp()
    monkeypatch.setattr("litellm.acompletion", _fake_completion)

    urls = [f"https://example{i}.com/" for i in range(6)]
    await ru.analyze_reference_urls(urls)
    assert len(calls) == ru.MAX_URLS


@pytest.mark.asyncio
async def test_analyze_reference_urls_whitelists_features(monkeypatch):
    async def _fake_fetch(url):
        return "<html><title>t</title>body</html>"
    monkeypatch.setattr(ru, "safe_fetch_html", _fake_fetch)

    async def _fake_route(*a, **kw):
        return ("fake-model", None, None)
    monkeypatch.setattr("core.llm_router.resolve_generation_route", _fake_route)

    class _Msg:
        content = '{"features_found": ["booking", "faq", "made_up_feature"], "notes": "clean and modern"}'
    class _Choice:
        message = _Msg()
    class _Resp:
        choices = [_Choice()]
    async def _fake_completion(*a, **kw):
        return _Resp()
    monkeypatch.setattr("litellm.acompletion", _fake_completion)

    result = await ru.analyze_reference_urls(["https://example.com/"])
    assert set(result["features_found"]) == {"booking", "faq"}
    assert "made_up_feature" not in result["features_found"]


@pytest.mark.asyncio
async def test_analyze_reference_urls_one_failure_does_not_block_others(monkeypatch):
    async def _fake_fetch(url):
        if "bad" in url:
            raise ru.UnsafeUrlError("nope")
        return "<html><title>t</title>body</html>"
    monkeypatch.setattr(ru, "safe_fetch_html", _fake_fetch)

    async def _fake_route(*a, **kw):
        return ("fake-model", None, None)
    monkeypatch.setattr("core.llm_router.resolve_generation_route", _fake_route)

    class _Msg:
        content = '{"features_found": ["gallery"], "notes": ""}'
    class _Choice:
        message = _Msg()
    class _Resp:
        choices = [_Choice()]
    async def _fake_completion(*a, **kw):
        return _Resp()
    monkeypatch.setattr("litellm.acompletion", _fake_completion)

    result = await ru.analyze_reference_urls(["https://bad.example.com/", "https://good.example.com/"])
    assert "error" not in result
    assert result["fetched"] == ["https://good.example.com/"]


@pytest.mark.asyncio
async def test_analyze_reference_urls_all_failing_is_an_error(monkeypatch):
    async def _fake_fetch(url):
        raise ru.UnsafeUrlError("nope")
    monkeypatch.setattr(ru, "safe_fetch_html", _fake_fetch)

    result = await ru.analyze_reference_urls(["https://bad.example.com/"])
    assert "error" in result


@pytest.mark.asyncio
async def test_analyze_reference_urls_no_urls_is_an_error():
    result = await ru.analyze_reference_urls([])
    assert "error" in result
