"""The 9 Nov 2026 go-live audit's security findings (docs/golive-audit-2026-11-09.md), each as
the attack that worked before the fix.

S1  a business owner made their own login master            (vula/api/users.py)
S2  "create a login" / "reset password" on someone else's login (users.py)
S3  WhatsApp sign-in as whatever email was on a team row     (vula/api/wa_login.py)
S4  ?tenant_id=A + "Application/JSON" body tenant_id=B       (master_auth._request_tenant)
S5  platform-wide routes open to a member of any business    (master_auth.require_platform)
H1  business-typed URLs fetched with no address checks       (web_scraper, projects, storage_links, wa_templates)
H2  OAuth state was the raw business id                      (vula/oauth_state.py)
H12 no API_KEY meant no auth at all, in production too       (master_auth.require_auth)
"""
import json
import re
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from config import settings
from vula.api import master_auth
from vula.api import users as users_api
from vula.api import wa_login as wl


# ── S1: master can't be handed out by a business ───────────────────────────────────────────

def _rows(*pairs):
    return [{"tenant_id": t, "role": r} for t, r in pairs]


@pytest.mark.asyncio
async def test_s1_an_owner_cannot_make_a_login_master():
    db = MagicMock()
    with patch.object(users_api, "_client", return_value=db), \
         patch.object(users_api, "_memberships", return_value=_rows(("shop", "owner"))), \
         patch.object(users_api.merchant_audit, "audit"):
        with pytest.raises(HTTPException) as e:
            await users_api.update_role("shop", "u-owner", users_api.RolePatch(role="master"),
                                        identity={"user_id": "u-owner"})
    assert e.value.status_code == 403
    db.table.assert_not_called()


@pytest.mark.asyncio
async def test_s1_an_owner_cannot_demote_the_masters_row_either():
    db = MagicMock()
    with patch.object(users_api, "_client", return_value=db), \
         patch.object(users_api, "_memberships", return_value=_rows(("shop", "master"))), \
         patch.object(users_api.merchant_audit, "audit"):
        with pytest.raises(HTTPException) as e:
            await users_api.update_role("shop", "u-ian", users_api.RolePatch(role="staff"),
                                        identity={"user_id": "u-owner"})
    assert e.value.status_code == 403
    db.table.assert_not_called()


@pytest.mark.asyncio
async def test_s1_a_login_on_another_business_is_404_here():
    with patch.object(users_api, "_client", return_value=MagicMock()), \
         patch.object(users_api, "_memberships", return_value=_rows(("other", "staff"))):
        with pytest.raises(HTTPException) as e:
            await users_api.update_role("shop", "u-x", users_api.RolePatch(role="owner"), identity={})
    assert e.value.status_code == 404


def test_s1_creating_a_login_as_master_saves_a_staff_login():
    db = MagicMock()
    db.table.return_value.select.return_value.eq.return_value.eq.return_value.limit.return_value \
        .execute.return_value = SimpleNamespace(data=[])
    with patch.object(users_api, "_client", return_value=db), \
         patch("vula.commerce.plan_limits.check_seat_quota", lambda t: None):
        users_api._map_tenant_user("u9", "shop", "master")
    inserted = db.table.return_value.insert.call_args[0][0]
    assert inserted["role"] == "staff"


# ── S2: another person's login keeps its password ──────────────────────────────────────────

def _auth_admin_recorder(existing=True):
    calls = []

    async def auth_admin(method, path, json=None):
        calls.append((method, path, json))
        if method == "POST" and path == "/users":
            if existing:
                return SimpleNamespace(status_code=422, text="already registered", json=lambda: {})
            return SimpleNamespace(status_code=201, text="", json=lambda: {"id": "u-new"})
        return SimpleNamespace(status_code=200, text="", json=lambda: {})
    return auth_admin, calls


def _password_puts(calls):
    return [c for c in calls if c[0] == "PUT" and (c[2] or {}).get("password")]


@pytest.mark.asyncio
async def test_s2_create_login_with_the_masters_email_sets_no_password():
    auth_admin, calls = _auth_admin_recorder()
    with patch.object(users_api, "_auth_admin", auth_admin), \
         patch.object(users_api, "_find_user_id", AsyncMock(return_value="u-ian")), \
         patch.object(users_api, "_memberships", return_value=_rows(("vula-hq", "master"))), \
         patch.object(users_api, "_map_tenant_user") as mapped:
        out = await users_api.create_user("shop", users_api.CreateUserIn(email="ian@vula-ai.com"),
                                          identity={"user_id": "u-owner"})
    assert "error" in out and "temp_password" not in out
    assert not _password_puts(calls)
    mapped.assert_not_called()


@pytest.mark.asyncio
async def test_s2_another_businesss_login_is_added_but_keeps_its_password():
    auth_admin, calls = _auth_admin_recorder()
    with patch.object(users_api, "_auth_admin", auth_admin), \
         patch.object(users_api, "_find_user_id", AsyncMock(return_value="u-acc")), \
         patch.object(users_api, "_memberships", return_value=_rows(("other-shop", "owner"))), \
         patch.object(users_api, "_map_tenant_user") as mapped, \
         patch.object(users_api.merchant_audit, "audit"):
        out = await users_api.create_user("shop", users_api.CreateUserIn(email="acc@books.co.za"),
                                          identity={"user_id": "u-owner"})
    assert out["temp_password"] is None and "own password" in out["note"]
    assert not _password_puts(calls)
    mapped.assert_called_once_with("u-acc", "shop", "staff")


@pytest.mark.asyncio
async def test_s2_this_businesss_own_login_still_gets_a_new_password():
    auth_admin, calls = _auth_admin_recorder()
    with patch.object(users_api, "_auth_admin", auth_admin), \
         patch.object(users_api, "_find_user_id", AsyncMock(return_value="u-rio")), \
         patch.object(users_api, "_memberships", return_value=_rows(("shop", "staff"))), \
         patch.object(users_api, "_map_tenant_user"), \
         patch.object(users_api.merchant_audit, "audit"):
        out = await users_api.create_user("shop", users_api.CreateUserIn(email="rio@shop.co.za"),
                                          identity={"user_id": "u-owner"})
    assert out["temp_password"] and len(_password_puts(calls)) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("rows", [_rows(("other", "staff")), _rows(("shop", "staff"), ("other", "owner")),
                                  _rows(("shop", "master")), []])
async def test_s2_reset_password_refuses_a_login_this_business_doesnt_own_alone(rows):
    auth_admin, calls = _auth_admin_recorder()
    with patch.object(users_api, "_auth_admin", auth_admin), \
         patch.object(users_api, "_memberships", return_value=rows):
        with pytest.raises(HTTPException) as e:
            await users_api.reset_password("shop", "u-x", identity={"user_id": "u-owner"})
    assert e.value.status_code == 403 and not calls


@pytest.mark.asyncio
async def test_s2_reset_password_fails_closed_when_the_lookup_fails():
    auth_admin, calls = _auth_admin_recorder()
    with patch.object(users_api, "_auth_admin", auth_admin), \
         patch.object(users_api, "_memberships", side_effect=RuntimeError("db down")):
        with pytest.raises(HTTPException):
            await users_api.reset_password("shop", "u-x", identity={"user_id": "u-owner"})
    assert not calls


@pytest.mark.asyncio
async def test_s2_reset_password_works_for_this_businesss_own_login_and_for_master():
    for rows, identity in ((_rows(("shop", "staff")), {"user_id": "u-owner"}),
                           (_rows(("other", "owner")), {"user_id": "u-ian", "role": "master"})):
        auth_admin, calls = _auth_admin_recorder()
        with patch.object(users_api, "_auth_admin", auth_admin), \
             patch.object(users_api, "_memberships", return_value=rows), \
             patch.object(users_api.merchant_audit, "audit"):
            out = await users_api.reset_password("shop", "u-x", identity=identity)
        assert out["temp_password"] and len(_password_puts(calls)) == 1


# ── S3: a phone only opens a login it has a claim to ───────────────────────────────────────

def _wa_env(monkeypatch, memberships, user_meta=None):
    users = {"ian@vula-ai.com": "u-ian", "judy@digg-ct.co.za": "u-judy"}
    mapped, links = [], []

    async def find_user(email):
        return users.get(email)

    async def auth_admin(method, path, json=None):
        if method == "POST" and path == "/users":
            users[json["email"]] = f"u-{len(users)}"
            return SimpleNamespace(status_code=200, json=lambda: {"id": users[json["email"]]}, text="")
        if method == "POST" and path == "/generate_link":
            links.append(json["email"])
            return SimpleNamespace(status_code=200, json=lambda: {"hashed_token": "tok"}, text="")
        if method == "GET" and path.startswith("/users/"):
            return SimpleNamespace(status_code=200, json=lambda: user_meta or {}, text="")
        return SimpleNamespace(status_code=404, json=lambda: {}, text="")

    monkeypatch.setattr(users_api, "_find_user_id", find_user)
    monkeypatch.setattr(users_api, "_auth_admin", auth_admin)
    monkeypatch.setattr(users_api, "_memberships", lambda uid: memberships.get(uid, []))
    monkeypatch.setattr(wl, "_ensure_mapping", lambda uid, tid, role: mapped.append((uid, tid)))
    return mapped, links


MEMBER = {"id": "m9", "tenant_id": "shop", "name": "Mallory", "whatsapp": "27820001111",
          "role": "staff", "email": "ian@vula-ai.com"}


@pytest.mark.asyncio
async def test_s3_a_team_row_with_the_masters_email_doesnt_open_the_masters_login(monkeypatch):
    mapped, links = _wa_env(monkeypatch, {"u-ian": _rows(("vula-hq", "master"))})
    out = await wl._login_for(dict(MEMBER), "shop")
    assert out["email"] == "wa-27820001111@" + wl.SYNTHETIC_EMAIL_DOMAIN
    assert links == [out["email"]] and ("u-ian", "shop") not in mapped


@pytest.mark.asyncio
async def test_s3_another_businesss_login_needs_its_own_number_on_record(monkeypatch):
    member = dict(MEMBER, email="judy@digg-ct.co.za")
    _, links = _wa_env(monkeypatch, {"u-judy": _rows(("digg-demo", "owner"))},
                       user_meta={"user_metadata": {"whatsapp": "27829999999"}})
    out = await wl._login_for(member, "shop")
    assert out["email"].startswith("wa-27820001111@")

    _, links = _wa_env(monkeypatch, {"u-judy": _rows(("digg-demo", "owner"))},
                       user_meta={"user_metadata": {"whatsapp": "+27 82 000 1111"}})
    out = await wl._login_for(member, "shop")
    assert out["email"] == "judy@digg-ct.co.za"


@pytest.mark.asyncio
async def test_s3_this_businesss_own_login_opens_as_before(monkeypatch):
    member = dict(MEMBER, email="judy@digg-ct.co.za")
    _, links = _wa_env(monkeypatch, {"u-judy": _rows(("shop", "staff"))})
    out = await wl._login_for(member, "shop")
    assert out["email"] == "judy@digg-ct.co.za" and links == ["judy@digg-ct.co.za"]


# ── S4: the body names the tenant whatever the content type says ───────────────────────────

def _request(query: str, ctype: str | None, body: bytes, path_params=None) -> Request:
    headers = [(b"content-type", ctype.encode())] if ctype else []
    scope = {"type": "http", "method": "POST", "path": "/query", "query_string": query.encode(),
             "headers": headers, "path_params": path_params or {}}
    sent = {"done": False}

    async def receive():
        if sent["done"]:
            return {"type": "http.disconnect"}
        sent["done"] = True
        return {"type": "http.request", "body": body, "more_body": False}
    return Request(scope, receive)


B_BODY = json.dumps({"tenant_id": "tenant-b", "query": "x"}).encode()


@pytest.mark.asyncio
@pytest.mark.parametrize("ctype", ["application/json", "Application/JSON", "APPLICATION/JSON; charset=utf-8",
                                   "application/vnd.api+json", "text/plain", None])
async def test_s4_a_body_tenant_that_disagrees_with_the_query_is_refused(ctype):
    assert await master_auth._request_tenant(_request("tenant_id=tenant-a", ctype, B_BODY)) == ""


@pytest.mark.asyncio
async def test_s4_agreeing_tenants_and_forms_still_work():
    same = json.dumps({"tenant_id": "tenant-a"}).encode()
    assert await master_auth._request_tenant(_request("tenant_id=tenant-a", "Application/JSON", same)) == "tenant-a"
    assert await master_auth._request_tenant(_request("", "application/json", same)) == "tenant-a"
    form = _request("", "application/x-www-form-urlencoded", b"tenant_id=tenant-a")
    assert await master_auth._request_tenant(form) == "tenant-a"
    assert await master_auth._request_tenant(_request("tenant_id=tenant-a", None, b"")) == "tenant-a"


@pytest.mark.asyncio
async def test_s4_require_auth_refuses_a_member_of_a_acting_on_b(monkeypatch):
    monkeypatch.setattr(settings, "api_key", "server-key")
    req = _request("tenant_id=tenant-a", "Application/JSON", B_BODY)
    req.scope["headers"].append((b"authorization", b"Bearer member-of-a"))

    async def not_master(h):
        raise HTTPException(status_code=403)
    member = AsyncMock(side_effect=lambda h, t: t == "tenant-a")
    with patch.object(master_auth, "require_master", not_master), \
         patch("vula.api.tenant_auth.is_tenant_member", member):
        with pytest.raises(HTTPException) as e:
            await master_auth.require_auth(api_key=None, request=req)
    assert e.value.status_code == 401


# ── S5: platform routes are API key or master only ─────────────────────────────────────────

PLATFORM_ROUTES = {("GET", "/ready"), ("GET", "/metrics"), ("POST", "/takeoff/rates/update"),
                   ("POST", "/v1/commerce/cron/campaigns"), ("POST", "/v1/commerce/cron/recurring-invoices"),
                   *{("POST", f"/v1/oth/briefing/{n}") for n in
                     ("morning", "evening", "low-stock", "friday-catch", "chase-unpaid")}}


def _routes(routes=None):
    from vula.api import server
    for r in (server.app.routes if routes is None else routes):
        if type(r).__name__ == "_IncludedRouter":
            yield from _routes(r.effective_candidates())
        elif hasattr(r, "dependant") and getattr(r, "methods", None):
            yield r


def _dep_calls(dependant):
    for d in dependant.dependencies:
        yield d.call
        yield from _dep_calls(d)


def test_s5_every_platform_route_needs_the_api_key_or_master():
    found = {}
    for r in _routes():
        for m in r.methods:
            if (m, r.path) in PLATFORM_ROUTES:
                found[(m, r.path)] = master_auth.require_platform in set(_dep_calls(r.dependant))
    assert set(found) == PLATFORM_ROUTES
    assert all(found.values()), [k for k, ok in found.items() if not ok]


@pytest.mark.asyncio
async def test_s5_require_platform_refuses_a_business_member(monkeypatch):
    monkeypatch.setattr(settings, "api_key", "server-key")
    req = _request("tenant_id=my-signup", None, b"")
    req.scope["headers"].append((b"authorization", b"Bearer member"))

    async def not_master(h):
        raise HTTPException(status_code=403, detail="Master access required.")
    with patch.object(master_auth, "require_master", not_master):
        with pytest.raises(HTTPException) as e:
            await master_auth.require_platform(api_key=None, request=req)
    assert e.value.status_code == 403
    with pytest.raises(HTTPException) as e:
        await master_auth.require_platform(api_key="wrong", request=_request("", None, b""))
    assert e.value.status_code == 401
    assert await master_auth.require_platform(api_key="server-key", request=_request("", None, b"")) is None


# ── H12: no API key fails closed in production ─────────────────────────────────────────────

@pytest.mark.asyncio
async def test_h12_no_api_key_in_production_is_closed_not_open(monkeypatch):
    monkeypatch.setattr(settings, "api_key", "")
    monkeypatch.setattr(settings, "debug", False)
    monkeypatch.setattr(settings, "vula_env", "production")
    for dep in (master_auth.require_auth, master_auth.require_platform):
        with pytest.raises(HTTPException):
            await dep(api_key=None, request=_request("", None, b""))
    monkeypatch.setattr(settings, "vula_env", "staging")
    assert await master_auth.require_auth(api_key=None, request=_request("", None, b"")) is None


# ── H1: business-typed URLs never reach an internal address ────────────────────────────────

INTERNAL = ["http://169.254.169.254/latest/meta-data/", "http://127.0.0.1:6333/collections",
            "http://10.0.0.5/", "file:///etc/passwd"]


def _no_network(*a, **k):
    raise AssertionError("an unchecked request was made")


@pytest.mark.asyncio
@pytest.mark.parametrize("url", INTERNAL)
async def test_h1_the_scraper_refuses_internal_addresses(url, monkeypatch):
    from vula.commerce.reference_url import UnsafeUrlError
    from vula.skills.web_scraper import WebFetcher
    monkeypatch.setattr("httpx.AsyncClient.get", _no_network)
    with patch.object(WebFetcher, "_crawl4ai_fetch", AsyncMock(side_effect=AssertionError("browser used"))):
        with pytest.raises(UnsafeUrlError):
            await WebFetcher().fetch_markdown(url)


@pytest.mark.asyncio
async def test_h1_a_redirect_to_an_internal_address_is_refused(monkeypatch):
    """The check runs again on every hop: a public page that redirects inward is refused."""
    import httpx
    from vula.commerce import reference_url as ru
    from vula.skills.web_scraper import WebFetcher

    async def resolve(host):
        if host != "public.example":
            raise ru.UnsafeUrlError(f"{host} is internal")

    def handler(request):
        return httpx.Response(302, headers={"location": "http://127.0.0.1:6333/"})
    real = httpx.AsyncClient

    def client(*a, **k):
        k.pop("follow_redirects", None)
        return real(*a, transport=httpx.MockTransport(handler), follow_redirects=False, **k)
    monkeypatch.setattr(ru, "_resolve_and_validate", resolve)
    monkeypatch.setattr(ru.httpx, "AsyncClient", client)
    with pytest.raises(ru.UnsafeUrlError):
        await WebFetcher().fetch_markdown("http://public.example/")


@pytest.mark.asyncio
async def test_h1_link_titles_and_template_media_and_stored_links_are_checked(monkeypatch):
    from vula.api.projects import FetchTitleIn, fetch_link_title
    from vula import storage_links
    from vula.commerce import wa_templates
    from vula.commerce.reference_url import UnsafeUrlError
    monkeypatch.setattr("httpx.AsyncClient.get", _no_network)
    monkeypatch.setattr("httpx.get", _no_network)
    monkeypatch.setattr(settings, "vula_fb_app_id", "app")

    out = await fetch_link_title("shop", FetchTitleIn(url="http://169.254.169.254/"))
    assert "error" in out
    handle, err = await wa_templates._upload_media_handle("http://127.0.0.1:8080/x.png", "tok")
    assert handle is None and err
    with pytest.raises(UnsafeUrlError):
        storage_links.fetch_sync("http://10.0.0.5/secret.pdf")
    with pytest.raises(UnsafeUrlError):
        await storage_links.fetch("http://10.0.0.5/secret.pdf")


# ── H2: OAuth state is signed, per provider, and short-lived ───────────────────────────────

def test_h2_state_round_trip_and_every_forgery():
    from vula import oauth_state
    s = oauth_state.sign("google", "shop")
    assert oauth_state.verify("google", s) == {"tenant_id": "shop", "extra": {}}
    assert oauth_state.verify("google", "shop") is None                      # the old raw id
    assert oauth_state.verify("microsoft", s) is None                        # made for Google
    body, sig = s.split(".")
    assert oauth_state.verify("google", body + "." + sig[:-2] + "AA") is None  # altered
    old = oauth_state.sign("google", "shop", now=time.time() - 16 * 60)
    assert oauth_state.verify("google", old) is None                         # expired


@pytest.mark.asyncio
@pytest.mark.parametrize("provider", ["google", "microsoft", "clickup"])
async def test_h2_a_callback_with_a_raw_business_id_stores_nothing(provider):
    import importlib
    mod = importlib.import_module(f"vula.api.{provider}")
    exchange = AsyncMock(return_value={"access_token": "attacker", "refresh_token": "r", "email": "x@evil"})
    with patch.object(mod.service, "exchange_code", exchange):
        resp = await mod.oauth_callback(code="c", state="off-the-hook")
    exchange.assert_not_called()
    assert "expired" in resp.body.decode()


@pytest.mark.asyncio
async def test_h2_google_callback_with_a_signed_state_stores_for_that_business():
    from vula import oauth_state
    from vula.api import google
    tok = {"access_token": "a", "refresh_token": "r", "email": "owner@shop", "expires_in": 3600}
    with patch.object(google.service, "exchange_code", AsyncMock(return_value=tok)), \
         patch.object(google, "store_connection") as store:
        await google.oauth_callback(code="c", state=oauth_state.sign("google", "shop"))
    assert store.call_args[0][0] == "shop"


def test_h2_dynamics_refuses_the_old_unsigned_state():
    import base64
    from vula.api import dynamics365
    old = base64.urlsafe_b64encode(json.dumps({"t": "off-the-hook", "org": "https://x.crm4.dynamics.com"}).encode()).decode()
    with pytest.raises(ValueError):
        dynamics365._decode_state(old)
    assert dynamics365._decode_state(dynamics365._encode_state("shop", "https://o.crm4.dynamics.com")) == \
        ("shop", "https://o.crm4.dynamics.com")


def test_h2_handing_out_a_state_needs_a_member_of_that_business():
    hits = [r for r in _routes() if r.path.endswith("/authorize-url")]
    assert {re.sub(r"^/v1/", "", r.path) for r in hits} == {
        "google/authorize-url", "microsoft/authorize-url", "clickup/authorize-url", "dynamics365/authorize-url"}
    assert all(master_auth.require_auth in set(_dep_calls(r.dependant)) for r in hits)
