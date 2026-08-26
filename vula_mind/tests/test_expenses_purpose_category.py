"""Tests for vula/commerce/expenses.py's purpose-category classification (migrations 140/141):
deterministic vendor matching first, LLM fallback only when that's inconclusive, and the
freeform-reply resolver used when a rep answers "what was this for?" in their own words."""
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from vula.commerce import expenses

TID = "gerflor"


# ── classify_purpose_category_deterministic ─────────────────────────────────────

@pytest.mark.parametrize("vendor,expected", [
    ("Engen Sea Point", "petrol"),
    ("Shell Ultra City", "petrol"),
    ("Tony Beato Service Station", "petrol"),
    ("City Lodge Hotel", "accommodation"),
    ("Premier Hotel OR Tambo", "accommodation"),
    ("Sarah's Guest House", "accommodation"),
])
def test_deterministic_match(vendor, expected):
    assert expenses.classify_purpose_category_deterministic(vendor) == expected


@pytest.mark.parametrize("vendor", ["Woolworths", "AVIS", "Vic Procter Motors", "", None])
def test_deterministic_no_match(vendor):
    assert expenses.classify_purpose_category_deterministic(vendor) is None


# ── classify_purpose_category (deterministic short-circuit + LLM fallback) ──────

@pytest.mark.asyncio
async def test_classify_short_circuits_on_deterministic_match_no_llm_call():
    with patch("core.llm_router.resolve_generation_route") as mock_route:
        cat = await expenses.classify_purpose_category(TID, "Engen Sea Point", 55000)
    assert cat == "petrol"
    mock_route.assert_not_called()


def _mock_llm_response(content: str):
    resp = SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=content))])
    return AsyncMock(return_value=resp)


@pytest.mark.asyncio
async def test_classify_falls_back_to_llm_and_accepts_a_valid_category():
    with (
        patch("core.llm_router.resolve_generation_route",
              new=AsyncMock(return_value=("local-model", None, "http://local"))),
        patch("litellm.acompletion", new=_mock_llm_response('{"category": "other"}')),
    ):
        cat = await expenses.classify_purpose_category(TID, "AVIS", 157600)
    assert cat == "other"


@pytest.mark.asyncio
async def test_classify_llm_uncertain_stays_uncertain():
    with (
        patch("core.llm_router.resolve_generation_route",
              new=AsyncMock(return_value=("local-model", None, "http://local"))),
        patch("litellm.acompletion", new=_mock_llm_response('{"category": "uncertain"}')),
    ):
        cat = await expenses.classify_purpose_category(TID, "Woolworths", 12000)
    assert cat == "uncertain"


@pytest.mark.asyncio
async def test_classify_llm_invalid_category_degrades_to_uncertain():
    with (
        patch("core.llm_router.resolve_generation_route",
              new=AsyncMock(return_value=("local-model", None, "http://local"))),
        patch("litellm.acompletion", new=_mock_llm_response('{"category": "groceries"}')),
    ):
        cat = await expenses.classify_purpose_category(TID, "Pick n Pay", 8000)
    assert cat == "uncertain"


@pytest.mark.asyncio
async def test_classify_malformed_json_degrades_to_uncertain():
    with (
        patch("core.llm_router.resolve_generation_route",
              new=AsyncMock(return_value=("local-model", None, "http://local"))),
        patch("litellm.acompletion", new=_mock_llm_response("not json at all")),
    ):
        cat = await expenses.classify_purpose_category(TID, "Random Shop", 5000)
    assert cat == "uncertain"


@pytest.mark.asyncio
async def test_classify_llm_exception_degrades_to_uncertain():
    with (
        patch("core.llm_router.resolve_generation_route",
              new=AsyncMock(side_effect=RuntimeError("boom"))),
    ):
        cat = await expenses.classify_purpose_category(TID, "Random Shop", 5000)
    assert cat == "uncertain"


# ── match_purpose_category (freeform reply resolver) ─────────────────────────────

@pytest.mark.parametrize("text,expected", [
    ("fuel", "petrol"),
    ("diesel for the bakkie", "petrol"),
    ("coffee with a client", "clients"),
    ("lunch with the customer", "clients"),
    ("hotel stay in Cape Town", "accommodation"),
    ("stayed over for the site visit", "accommodation"),
])
def test_match_purpose_category_matches_keywords(text, expected):
    assert expenses.match_purpose_category(text) == expected


@pytest.mark.parametrize("text", ["office supplies", "car wash", "", None])
def test_match_purpose_category_no_match_returns_none(text):
    assert expenses.match_purpose_category(text) is None


# ── set_purpose_category ─────────────────────────────────────────────────────────

def test_set_purpose_category_writes_category_and_detail():
    mock_client = MagicMock()
    mock_client.table.return_value.update.return_value.eq.return_value.eq.return_value.execute.return_value = \
        MagicMock(data=[{"id": "e1", "purpose_category": "clients", "purpose_detail": "coffee with a client"}])
    with patch("vula.commerce.expenses._client", return_value=mock_client):
        out = expenses.set_purpose_category(TID, "e1", "clients", detail="coffee with a client")
    mock_client.table.assert_called_with("commerce_expenses")
    update_call = mock_client.table.return_value.update
    update_call.assert_called_once()
    patch_arg = update_call.call_args[0][0]
    assert patch_arg["purpose_category"] == "clients"
    assert patch_arg["purpose_detail"] == "coffee with a client"
    assert out["purpose_category"] == "clients"


def test_set_purpose_category_without_detail_omits_it():
    mock_client = MagicMock()
    mock_client.table.return_value.update.return_value.eq.return_value.eq.return_value.execute.return_value = \
        MagicMock(data=[{"id": "e1", "purpose_category": "petrol"}])
    with patch("vula.commerce.expenses._client", return_value=mock_client):
        expenses.set_purpose_category(TID, "e1", "petrol")
    patch_arg = mock_client.table.return_value.update.call_args[0][0]
    assert "purpose_detail" not in patch_arg
