"""Tax for every tenant, worked in code (DIGG, 6 Oct: "HPC project profit and also after tax" got
"I do not have the tools to calculate the profit after tax"; Ian: Vula should help tenants manage
tax). Tables from Budget 2026; every figure from the bank lines; always labelled an estimate."""
from datetime import date
from unittest.mock import AsyncMock, patch

import pytest

from core.skills import commerce_admin as ca
from core.skills.base import looks_like_owner_admin_question
from vula import owner_digest
from vula.commerce import tax

TID = "digg-demo"
OCT6 = date(2026, 10, 6)


# ── the tables (pinned — change only with a new budget) ──────────────────────

def test_tax_year_runs_march_to_february():
    assert tax.tax_year(OCT6) == (2027, date(2026, 3, 1), date(2027, 2, 28))
    assert tax.tax_year(date(2027, 2, 15))[0] == 2027
    assert tax.tax_year(date(2028, 2, 29))[2] == date(2028, 2, 29)


@pytest.mark.parametrize("rands,year,expected", [
    (600_000, 2027, 0), (800_000, 2027, 2_000), (1_200_000, 2027, 8_000),
    (2_000_000, 2027, 29_000), (2_300_000, 2027, 38_000), (2_300_001, 2027, None),
    (400_000, 2026, 650), (1_000_000, 2026, 14_150)])
def test_turnover_tax_bands(rands, year, expected):
    got = tax.turnover_tax_cents(rands * 100, year)
    assert got == (None if expected is None else expected * 100)


@pytest.mark.parametrize("rands,expected", [
    (99_000, 0), (365_000, 18_620), (550_000, 57_470), (1_000_000, 178_970)])
def test_small_business_corporation_bands(rands, expected):
    assert tax.company_tax_cents(rands * 100, 2027, sbc=True) == expected * 100


@pytest.mark.parametrize("rands,expected", [
    (99_000, 0), (245_100, 26_298), (500_000, 98_417), (1_000_000, 288_293)])
def test_personal_tax_less_the_primary_rebate(rands, expected):
    # 500k: 79,998 + 31% × 116,900 = 116,237 − 17,820 rebate = 98,417
    assert tax.personal_tax_cents(rands * 100, 2027) == expected * 100


def test_personal_brackets_chain_exactly():
    bands, rebate = tax.PERSONAL[2027]
    for (a, base_a, r_a), (b, base_b, _) in zip(bands, bands[1:]):
        assert base_a + round((b - a) * r_a) == base_b
    assert rebate == 1_782_000 and tax.personal_tax_cents(9_900_000, 2027) == 0


def test_sbc_brackets_chain_exactly():
    bands = tax.SBC[2027]
    for (a, base_a, r_a), (b, base_b, _) in zip(bands, bands[1:]):
        assert base_a + round((b - a) * r_a) == base_b


def test_vat_thresholds_changed_on_1_april_2026():
    assert tax.vat_threshold(date(2026, 3, 31)) == (100_000_000, 5_000_000)
    assert tax.vat_threshold(date(2026, 4, 1)) == (230_000_000, 12_000_000)


# ── what's known about the business ───────────────────────────────────────────

@pytest.mark.parametrize("answer,kind", [
    ("Turnover Tax", "turnover"), ("we're on turnover tax", "turnover"),
    ("DIGG (Pty) Ltd", "company"), ("small business corporation", "sbc"),
    ("sole proprietor", "sole_prop"), ("", None)])
def test_regime_from_the_owners_words(answer, kind):
    with patch("vula.commerce.business_profile.get_answers", return_value={"tax_regime": answer}):
        assert tax.regime(TID) == kind


def _figs(income, expense, first=date(2026, 6, 25), last=date(2026, 9, 30), uncat_out=0):
    months = round(((last - first).days + 1) / 30.44, 1)
    return {"income_cents": income, "expense_cents": expense, "profit_cents": income - expense,
            "uncategorised_in_cents": 0, "uncategorised_out_cents": uncat_out,
            "first": first, "last": last, "months": months}


def _as(regime, figs):
    return (patch("vula.commerce.business_profile.get_answers", return_value={"tax_regime": regime}),
            patch.object(tax, "year_figures", return_value=figs))


# ── income tax ────────────────────────────────────────────────────────────────

def test_over_the_turnover_tax_limit_says_so_and_computes_nothing():
    a, b = _as("Turnover Tax", _figs(283_875_064, 0))     # DIGG's money in, 25 Jun – 30 Sep
    with a, b:
        out = tax.estimate(TID, 14_220_317, 230_071_014, "HPC Bokaap", today=OCT6)
    assert out["over_limit"] and "tax_cents" not in out
    assert "R2,838,750.64" in out["text"] and "R2,300,000.00" in out["text"]
    assert "accountant" in out["text"]


def test_project_share_of_turnover_tax_and_profit_after_it():
    a, b = _as("turnover tax", _figs(200_000_000, 0, first=date(2026, 3, 1)))
    with a, b:
        out = tax.estimate(TID, 14_220_317, 100_000_000, "HPC Bokaap", today=OCT6)
    assert out["tax_cents"] == 2_900_000 and out["project_tax_cents"] == 1_450_000
    assert "R127,703.17 after Turnover Tax" in out["text"] and tax.NOTE in out["text"]


def test_company_tax_on_the_year_with_provisional_payments_and_project_after_tax():
    # DIGG as a company: R2.84m in, R2.2m deductible costs over 3.2 months
    a, b = _as("(Pty) Ltd", _figs(283_875_064, 220_000_000))
    with a, b:
        out = tax.estimate(TID, 14_220_317, 230_071_014, "HPC Bokaap", today=OCT6)
    f = out["figures"]
    yearly = round(63_875_064 * 12 / f["months"])
    assert out["annual_profit_cents"] == yearly
    assert out["tax_cents"] == round(yearly * 0.27)
    p1, p2 = out["provisional"]
    assert p1["due"] == "2026-08-31" and p2["due"] == "2027-02-26"      # 28 Feb 2027 is a Sunday
    assert p1["cents"] + p2["cents"] == out["tax_cents"]
    assert out["project_tax_cents"] == 3_839_486
    assert "R142,203.17 × 27% = R38,394.86 tax → R103,808.31 after tax" in out["text"]
    assert "small business corporation" in out["text"]


def test_sole_proprietor_gets_the_personal_tables():
    a, b = _as("sole proprietor", _figs(50_000_000, 10_000_000, first=date(2026, 3, 1),
                                        last=date(2027, 2, 28)))
    with a, b:
        out = tax.estimate(TID, today=OCT6)
    assert out["tax_cents"] == tax.personal_tax_cents(40_000_000, 2027)
    assert "primary rebate" in out["text"] and "Other income" in out["text"]


def test_uncategorised_payments_are_pointed_out():
    a, b = _as("company", _figs(100_000_000, 50_000_000, uncat_out=7_500_000))
    with a, b:
        out = tax.estimate(TID, today=OCT6)
    assert "R75,000.00 of payments aren't categorised" in out["text"]


def test_unknown_regime_asks_rather_than_guesses():
    with patch("vula.commerce.business_profile.get_answers", return_value={}):
        out = tax.estimate(TID, 14_220_317)
    assert out["status"] == "need_info" and "Turnover Tax" in out["message"]


# ── VAT ───────────────────────────────────────────────────────────────────────

def test_vat_periods_and_due_dates():
    cur, last = tax.vat_periods("B", OCT6)
    assert cur == (date(2026, 9, 1), date(2026, 10, 31)) and last == (date(2026, 7, 1), date(2026, 8, 31))
    cur, last = tax.vat_periods("A", OCT6)
    assert cur == (date(2026, 10, 1), date(2026, 11, 30)) and last == (date(2026, 8, 1), date(2026, 9, 30))
    assert tax.vat_periods("C", OCT6)[0] == (date(2026, 10, 1), date(2026, 10, 31))
    assert tax.vat_periods("B", date(2027, 1, 10))[0] == (date(2027, 1, 1), date(2027, 2, 28))
    assert tax.vat_periods("A", date(2027, 1, 10))[0] == (date(2026, 12, 1), date(2027, 1, 31))
    assert tax.vat_due(date(2026, 8, 31)) == date(2026, 9, 30)
    assert tax.vat_due(date(2026, 9, 30)) == date(2026, 10, 30)          # 31 Oct is a Saturday


def _vat(**kw):
    base = {"output_cents": 0, "input_claimable_cents": 0, "net_cents": 0,
            "input_no_vat_number_cents": 0, "sales_12m_cents": 0, "months": []}
    return {**base, **kw}


def test_registered_vat_position_with_claims_lost_to_missing_numbers():
    with patch.object(tax, "vat_status", return_value=True), \
            patch("vula.commerce.business_profile.get_answers", return_value={"vat_category": "B"}), \
            patch("vula.commerce.cross_check.vat", return_value=_vat(
                output_cents=1_500_000, input_claimable_cents=400_000, net_cents=1_100_000,
                input_no_vat_number_cents=90_000)):
        out = tax.vat_position("off-the-hook", today=OCT6)
    assert "VAT201 due 2026-09-30" in out["text"] and "R11,000.00 to pay" in out["text"]
    assert "R1,800.00 of VAT is on suppliers' bills without their VAT number" in out["text"]
    assert "assumed category B" not in out["text"]


def test_unregistered_over_the_threshold_must_register():
    with patch.object(tax, "vat_status", return_value=False), \
            patch("vula.commerce.cross_check.vat", return_value=_vat(
                sales_12m_cents=283_664_064, input_claimable_cents=12_000_000,
                months=[{"month": "2026-07"}, {"month": "2026-09"}])):
        out = tax.vat_position(TID, today=OCT6)
    assert out["over_threshold"] and "must register for VAT" in out["text"]
    assert "R120,000.00" in out["text"] and "R2,300,000.00" in out["text"]


def test_unregistered_under_the_threshold_gets_months_to_go():
    with patch.object(tax, "vat_status", return_value=False), \
            patch("vula.commerce.cross_check.vat", return_value=_vat(
                sales_12m_cents=150_000_000, months=[{"month": "2026-01"}, {"month": "2026-10"}])):
        out = tax.vat_position("gerflor", today=OCT6)
    assert "you'd pass it in roughly 5 months" in out["text"]


def test_unknown_vat_status_asks():
    with patch.object(tax, "vat_status", return_value=None):
        assert tax.vat_position(TID)["status"] == "need_info"


# ── calendar and the morning digest ───────────────────────────────────────────

def test_calendar_for_a_vat_registered_company_with_workers():
    with patch.object(tax, "vat_status", return_value=True), \
            patch.object(tax, "_pays_workers", return_value=True), \
            patch("vula.commerce.business_profile.get_answers",
                  return_value={"tax_regime": "company", "vat_category": "B"}):
        items = tax.deadlines(TID, date(2026, 8, 1))
    whats = [(d["due"].isoformat(), d["kind"]) for d in items]
    assert ("2026-08-07", "payroll") in whats
    assert ("2026-08-31", "provisional") in whats
    assert ("2026-09-30", "vat") in whats          # category B: Jul–Aug period, due end Sep
    assert not [w for w in whats if w[1] == "vat" and w[0] != "2026-09-30"]
    assert all(date(2026, 8, 1) <= d["due"] <= date(2026, 9, 30) for d in items)


def test_digest_mentions_a_deadline_seven_and_two_days_out_only():
    with patch.object(tax, "vat_status", return_value=False), \
            patch.object(tax, "_pays_workers", return_value=False), \
            patch("vula.commerce.business_profile.get_answers", return_value={"tax_regime": "company"}):
        days = {d: tax.digest_lines(TID, d) for d in
                (date(2026, 8, 24), date(2026, 8, 25), date(2026, 8, 27), date(2026, 8, 28),
                 date(2026, 8, 29))}
    # 31 Aug 2026 is a Monday: 7 days before is Mon 24 Aug; 2 days before is Sat 29 → Fri 28.
    assert days[date(2026, 8, 24)] and days[date(2026, 8, 28)]
    assert not days[date(2026, 8, 25)] and not days[date(2026, 8, 27)] and not days[date(2026, 8, 29)]
    assert "Provisional tax (IRP6) 1st payment — due 2026-08-31" in days[date(2026, 8, 24)][0]


def test_digest_renders_a_tax_section_even_with_no_invoices():
    text = owner_digest.render({"paid": [], "overdue": [], "upcoming_cents": 0, "upcoming_n": 0,
                                "tax": ["• VAT201 for the period ending 2026-08-31 — due 2026-09-30"]})
    assert "Tax coming up" in text and "VAT201" in text


# ── the admin agent ───────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_set_tax_regime_saves_reads_back_and_answers():
    saved = {}

    async def save(tid, updates, by=""):
        saved.update(updates)
        return {"saved": True}
    skill = ca.CommerceAdminSkill()
    with patch("vula.commerce.business_profile.save_answers", new=save), \
            patch("vula.commerce.business_profile.get_answers", side_effect=lambda t: dict(saved)), \
            patch.object(tax, "year_figures", return_value=_figs(80_000_000, 0, first=date(2026, 3, 1))):
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


class _Settings:
    """A stand-in commerce_invoice_settings table."""

    def __init__(self, row=None):
        self.row = row

    def table(self, _name):
        return self

    def select(self, *_a):
        self._op = "select"
        return self

    def eq(self, *_a):
        return self

    def limit(self, *_a):
        return self

    def update(self, row):
        self._op, self._row = "update", row
        return self

    def insert(self, row):
        self._op, self._row = "insert", row
        return self

    def execute(self):
        from types import SimpleNamespace
        if self._op == "select":
            return SimpleNamespace(data=[self.row] if self.row else [])
        self.row = {**(self.row or {}), **self._row}
        return SimpleNamespace(data=[self.row])


@pytest.mark.asyncio
async def test_set_vat_registration_previews_then_saves_and_reads_back():
    db = _Settings({"tenant_id": TID, "vat_registered": False})
    skill = ca.CommerceAdminSkill()
    args = {"registered": True, "vat_number": "4123 456 789", "category": "B"}
    answers = {}

    async def save(tid, updates, by=""):
        answers.update(updates)
        return {"saved": True}
    with patch.object(ca.service, "_client", return_value=db), \
            patch("vula.commerce.job_costing._client", return_value=db), \
            patch("vula.commerce.business_profile.save_answers", new=save), \
            patch("vula.commerce.business_profile.get_answers", side_effect=lambda t: dict(answers)), \
            patch.object(tax, "vat_position", return_value={"text": "VAT position…"}):
        preview = await skill._set_vat_registration(TID, args, {})
        assert preview["preview"] and db.row["vat_registered"] is False
        out = await skill._set_vat_registration(TID, {**args, "confirm": True}, {})
    assert db.row["vat_registered"] is True and db.row["vat_number"] == "4123456789"
    assert out["reply_verbatim"].startswith("✅ Saved: the business is VAT registered, number 4123456789, category B.")


@pytest.mark.asyncio
async def test_a_wrong_vat_number_is_queried_not_saved():
    skill = ca.CommerceAdminSkill()
    out = await skill._set_vat_registration(TID, {"registered": True, "vat_number": "12345",
                                                  "confirm": True}, {})
    assert out["status"] == "need_info" and "starting with 4" in out["message"]


@pytest.mark.parametrize("text,yes", [
    ("HPC project profit and also after tax", True), ("How much tax must I pay?", True),
    ("Turnover tax", True), ("How much VAT do I owe?", True), ("Should we register for VAT?", True),
    ("We are VAT registered, number 4123456789", True), ("tax", True),
    ("Send me the tax invoice for HPC", False), ("Add VAT to the invoice for Judy", False),
    ("Is the price including VAT?", False)])
def test_owner_tax_questions_go_to_the_admin_agent(text, yes):
    assert looks_like_owner_admin_question(text) is yes
