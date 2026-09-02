"""A link someone pastes is part of the question, not decoration.

Real Gerflor transcript, 2026-09-02:

    rep:  Please remind me to contact Danielle in two weeks. <maps.app.goo.gl link>
    Vula: ...the link you shared appears to be a Google Maps link, but I didn't use it for
          anything since you didn't ask me to.
    rep:  What the company details
    Vula: The company details are as follows:
          - Distribution centre in Cape Town and Johannesburg
          - Phone: +27 (0) 87 184 3128
          - Email: sales@sportsflooringwarehouse.co.za

Those are Sports Flooring Warehouse's details from the knowledge base. The rep meant the
company at the LINK — Danielle Vermaak Interior — and got a confident, wholly wrong answer.
Confirmed by the rep: "not the same company".

Resolving that real link now yields "Danielle Vermaak Interior, Huguenot Park Unit 11, c/o
Klein Drakenstein road, Cape town, 7646".
"""
from unittest.mock import AsyncMock, patch

import pytest

from vula.commerce import shared_link as sl

REAL_MSG = ("Please remind me to contact Danielle in two weeks."
            "https://maps.app.goo.gl/m7RsCYkkdWiJ5ffE8?g_st=ac")
REAL_RESOLVED = ("https://www.google.com/maps/place/Danielle+Vermaak+Interior,+Huguenot+Park+"
                 "Unit+11,+c%2FKlein+Drakenstein+road,+Cape+town,+7646/data=!4m2!3m1")


# ── finding links in a real message ─────────────────────────────────────────────

def test_the_link_is_found_even_with_no_space_before_it():
    """The real message ran the URL straight onto the sentence with no space."""
    assert sl.find_urls(REAL_MSG) == ["https://maps.app.goo.gl/m7RsCYkkdWiJ5ffE8?g_st=ac"]


def test_a_message_with_no_link_yields_nothing():
    assert sl.find_urls("Please remind me to contact Danielle in two weeks.") == []
    assert sl.find_urls("") == []


@pytest.mark.parametrize("url,expected", [
    ("https://maps.app.goo.gl/abc", True),
    ("https://www.google.com/maps/place/Foo", True),
    ("https://maps.google.com/?q=x", True),
    ("https://example.co.za/about", False),
])
def test_maps_links_are_recognised(url, expected):
    assert sl.is_maps_link(url) is expected


# ── resolving to a real business name ───────────────────────────────────────────

def test_the_place_name_is_read_out_of_a_resolved_maps_url():
    name = sl._place_name_from_url(REAL_RESOLVED)
    assert name and name.startswith("Danielle Vermaak Interior")
    assert "+" not in name and "%2F" not in name


def test_a_non_place_url_yields_no_name():
    assert sl._place_name_from_url("https://www.google.com/maps/@-33.9,18.4,12z") is None


@pytest.mark.asyncio
async def test_an_unreachable_link_reports_no_name_rather_than_guessing():
    with patch("vula.commerce.reference_url.safe_fetch_html",
               AsyncMock(side_effect=RuntimeError("boom"))):
        out = await sl.resolve_shared_link("https://example.com/thing")
    assert out["name"] is None


@pytest.mark.asyncio
async def test_an_unsafe_url_is_refused_before_any_fetch():
    """A pasted link is untrusted input — the SSRF guard must run first."""
    with patch("vula.commerce.reference_url.safe_fetch_html", AsyncMock()) as fetch:
        out = await sl.resolve_shared_link("http://169.254.169.254/latest/meta-data/")
    assert out["name"] is None
    fetch.assert_not_awaited()


# ── the context line the model actually sees ────────────────────────────────────

def test_a_resolved_link_tells_the_model_which_company_is_meant():
    line = sl.describe({"url": "https://maps.app.goo.gl/x", "name": "Danielle Vermaak Interior"})
    assert "Danielle Vermaak Interior" in line
    assert "not any business in the knowledge base" in line
    assert "say so rather than offering another company's" in line


def test_an_unresolved_link_forbids_substituting_another_business():
    """The exact failure: answering with a different company's details."""
    line = sl.describe({"url": "https://maps.app.goo.gl/x", "name": None})
    assert "can't open the link" in line
    assert "never answer with a different business's details" in line


def test_the_context_names_the_link_itself():
    line = sl.describe({"url": "https://maps.app.goo.gl/m7RsCYkkdWiJ5ffE8", "name": "Acme"})
    assert "https://maps.app.goo.gl/m7RsCYkkdWiJ5ffE8" in line
