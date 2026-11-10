"""Client links on the business's own address (10 Nov 2026, Ian: "can the URL look like it's from DIGG?").

projects.digg.co.za/track/… instead of vula-group-production.up.railway.app/track/… — the same set-up
as a shop's own address, the same token opening on either, and each business's address opening only
its own clients' links."""
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from vula.api import project_track
from vula.commerce import project_tracker as pt
from vula.commerce import shop_domain as sd
from vula.commerce import web_basket as wb


@pytest.mark.parametrize("raw,ok", [
    ("projects.digg.co.za", "projects.digg.co.za"), ("https://Clients.digg.co.za/", "clients.digg.co.za"),
    ("portal.digg.co.za", "portal.digg.co.za"),
])
def test_a_client_links_address(raw, ok):
    assert sd.normalise(raw, "links") == ok


@pytest.mark.parametrize("raw", ["digg.co.za", "shop.digg.co.za", "www.digg.co.za", "projects.vula-group-production.up.railway.app"])
def test_a_client_links_address_must_be_its_own_subdomain(raw):
    with pytest.raises(sd.DomainError):
        sd.normalise(raw, "links")


def test_the_shop_address_rules_are_unchanged():
    assert sd.normalise("shop.digg.co.za") == "shop.digg.co.za"
    with pytest.raises(sd.DomainError):
        sd.normalise("projects.digg.co.za")


def test_each_kind_keeps_its_own_address():
    cfg = {"domains": ["shop.digg.co.za", "admin.digg.co.za", "projects.old.co.za"]}
    saved = []
    db = SimpleNamespace(table=lambda n: SimpleNamespace(update=lambda row: saved.append(row) or SimpleNamespace(
        eq=lambda *a: SimpleNamespace(execute=lambda: None))))
    with patch("vula.api.tenants.get_config", return_value=cfg), patch("vula.commerce.service._client", return_value=db):
        sd._save("digg-demo", "projects.digg.co.za", "links")
    assert saved[0]["domains"] == ["shop.digg.co.za", "admin.digg.co.za", "projects.digg.co.za"]


def test_new_links_use_the_businesss_address_only_once_it_answers():
    with patch.object(wb, "configured_link_host", return_value="projects.digg.co.za"):
        wb._READY["projects.digg.co.za"] = (__import__("time").monotonic(), True)
        assert pt.public_url("tok", "digg-demo") == "https://projects.digg.co.za/track/tok"
        wb._READY["projects.digg.co.za"] = (__import__("time").monotonic(), False)
        assert pt.public_url("tok", "digg-demo").endswith("/track/tok")
        assert "projects.digg.co.za" not in pt.public_url("tok", "digg-demo")
    wb._READY.pop("projects.digg.co.za", None)
    with patch.object(wb, "configured_link_host", return_value=""):
        assert "projects." not in pt.public_url("tok", "digg-demo")


def _req(host):
    return SimpleNamespace(headers={"host": host})


def test_a_businesss_address_opens_only_its_own_clients_links():
    share = {"tenant_id": "digg-demo"}
    owner = {"projects.digg.co.za": "digg-demo", "projects.other.co.za": "other", "shop.other.co.za": "other"}
    with patch("vula.api.tenants.tenant_for_host", side_effect=lambda h: owner.get(h)):
        assert not project_track._wrong_address(share, _req("projects.digg.co.za"))
        assert project_track._wrong_address(share, _req("projects.other.co.za"))
        assert project_track._wrong_address(share, _req("shop.other.co.za"))
        assert not project_track._wrong_address(share, _req("vula-group-production.up.railway.app"))
        assert not project_track._wrong_address(share, None)
