"""Tax worked out in code (DIGG, 6 Oct: "HPC project profit and also after tax" got "I do not have
the tools to calculate the profit after tax"). Tables from Budget 2026; every figure from the
bank lines; always labelled an estimate."""
from datetime import date
from unittest.mock import AsyncMock, patch

import pytest

from core.skills import commerce_admin as ca
from core.skills.base import looks_like_owner_admin_question
from vula.commerce import tax

TID = "digg-demo"


def test_tax_year_runs_march_to_february():
    assert tax.tax_year(date(2026, 10, 6)) == (2027, date(2026, 3, 1), date(2027, 2, 28))
    assert tax.tax_year(date(2027, 2, 15))[0] == 2027
    assert tax.tax_year(date(2028, 2, 29))[2] == date(2028, 2, 29)


@pytest.mark.parametrize("rands,year,expected", [
    (600_000, 2027, 0), (800_000, 2027, 2_000), (1_200_000, 2027, 8_000),
    (2_000_000, 2027, 29_000), (2_300_000, 2027, 38_000), (2_300_001, 2027, None),
    (400_000, 2026, 650), (1_000_000, 2026, 14_150)])
def test_turnover_tax_bands(rands, year, expected):
    got = tax.turnover_tax_cents(rands * 100, year)
    assert got == (None if expected is None else expected * 100)


@pytest.mark.parametrize("answer,kind", [
    ("Turnover Tax", "turnover"), ("we're on turnover tax", "turnover"),
    ("DIGG (Pty) Ltd", "company"), ("sole proprietor", "sole_prop"), ("", None)])
def test_regime_from_the_owners_words(answer, kind):
    with patch("vula.commerce.business_profile.get_answers", return_value={"tax_regime": answer}):
        assert tax.regime(TID) == kind


def _with(regime, turnover_cents, first="2026-06-25", last="2026-09-30"):
    return (patch("vula.commerce.business_profile.get_answers", return_value={"tax_regime": regime}),
            patch.object(tax, "turnover_so_far", return_value={
                "turnover_cents": turnover_cents, "first": first, "last": last}),
            patch.object(tax, "tax_year", return_value=(2027, date(2026, 3, 1), date(2027, 2, 28))))


def test_over_the_turnover_tax_limit_says_so_and_computes_nothing():
    a, b, c = _with("Turnover Tax", 283_875_064)        # DIGG's money in, 25 Jun – 30 Sep
    with a, b, c:
        out = tax.estimate(TID, 14_220_317, 230_071_014, "HPC Bokaap")
    assert out["over_limit"] and "tax_cents" not in out
    assert "R2,838,750.64" in out["text"] and "R2,300,000.00" in out["text"]
    assert "accountant" in out["text"]


def test_project_share_of_turnover_tax_and_profit_after_it():
    a, b, c = _with("turnover tax", 200_000_000)           # R2m → R29,000
    with a, b, c:
        out = tax.estimate(TID, 14_220_317, 100_000_000, "HPC Bokaap")
    assert out["tax_cents"] == 2_900_000 and out["project_tax_cents"] == 1_450_000
    assert out["profit_after_tax_cents"] == 14_220_317 - 1_450_000
    assert "R127,703.17 after Turnover Tax" in out["text"] and tax.NOTE in out["text"]


def test_company_tax_is_27_percent_of_profit():
    a, b, c = _with("(Pty) Ltd", 0)
    with a, b, c:
        out = tax.estimate(TID, 14_220_317)
    assert out["tax_cents"] == 3_839_486 and "R142,203.17 × 27% = R38,394.86" in out["text"]


def test_unknown_regime_asks_rather_than_guesses():
    with patch("vula.commerce.business_profile.get_answers", return_value={}):
        out = tax.estimate(TID, 14_220_317)
    assert out["status"] == "need_info" and "Turnover Tax" in out["message"]


@pytest.mark.asyncio
async def test_set_tax_regime_saves_reads_back_and_answers():
    saved = {}

    async def save(tid, updates, by=""):
        saved.update(updates)
        return {"saved": True}
    skill = ca.CommerceAdminSkill()
    with patch("vula.commerce.business_profile.save_answers", new=save), \
            patch("vula.commerce.business_profile.get_answers", side_effect=lambda t: dict(saved)), \
            patch.object(tax, "turnover_so_far", return_value={"turnover_cents": 80_000_000,
                                                              "first": None, "last": None}), \
            patch.object(tax, "tax_year", return_value=(2027, date(2026, 3, 1), date(2027, 2, 28))):
        out = await skill._dispatch_tool("set_tax_regime", {"regime": "Turnover tax"}, {"tenant_id": TID})
    assert out["verified"] and out["reply_verbatim"].startswith("✅ Noted: the business is taxed as Turnover Tax.")
    assert "R2,000.00" in out["reply_verbatim"]


@pytest.mark.asyncio
async def test_set_tax_regime_that_does_not_save_says_so():
    skill = ca.CommerceAdminSkill()
    with patch("vula.commerce.business_profile.save_answers", new=AsyncMock(return_value={"saved": False})), \
            patch("vula.commerce.business_profile.get_answers", return_value={}):
        out = await skill._dispatch_tool("set_tax_regime", {"regime": "Turnover tax"}, {"tenant_id": TID})
    assert "error" in out and out["reply_verbatim"].startswith("⚠️")


@pytest.mark.parametrize("text,yes", [
    ("HPC project profit and also after tax", True), ("How much tax must I pay?", True),
    ("Turnover tax", True), ("Send me the tax invoice for HPC", False)])
def test_owner_tax_questions_go_to_the_admin_agent(text, yes):
    assert looks_like_owner_admin_question(text) is yes
