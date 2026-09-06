"""Decide a merchant's account once, not once per transaction.

2026-09-06, Ian: "why can Vula research crazy store and be able to allocate transaction based on
good bought?" Measured on production, accounting.categorize_batch filed the SAME merchant
differently on different lines — it decides per transaction in batches of 40 (separate model
calls) and is told nothing about the merchant beyond the raw statement string:

    Crazy Store      x8  ->  4 cost_of_sales, 4 owner_drawings
    Dis-Chem         x8  ->  7 owner_drawings, 1 other_expense
    Checkers Sixty60 x7  ->  4 cost_of_sales, 2 owner_drawings, 1 other_expense
    Pick n Pay       x6  ->  5 owner_drawings, 1 bank_charges
    Table Bay        x9  ->  6 cost_of_sales, 1 sales   (a payment OUT filed as income)

Separately, learn_category_rule exploded each correction into up to 8 single-token rules, and
off-the-hook's live table held 'received' -> owner_drawings (highest hit count of any rule, and
'received' is on all 404 of its "Payment Received: ..." credits) alongside a contradictory
'received' -> bank_cash.
"""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from vula.commerce import accounting, merchants

TENANT = "off-the-hook"

CHART = [
    {"code": "sales", "name": "Sales", "type": "income", "vat_treatment": "standard"},
    {"code": "other_income", "name": "Other income", "type": "income", "vat_treatment": "standard"},
    {"code": "cost_of_sales", "name": "Cost of sales", "type": "expense", "vat_treatment": "standard"},
    {"code": "other_expense", "name": "Other expenses", "type": "expense", "vat_treatment": "standard"},
    {"code": "owner_drawings", "name": "Owner drawings", "type": "equity", "vat_treatment": "none"},
]


# ── merchant identity ───────────────────────────────────────────────────────────

@pytest.mark.parametrize("description,expected", [
    ("The Crazy Store Tablevi Cape Town (Card 572)", "crazy store"),
    ("CRAZY STORE TABLE VIEW", "crazy store"),
    ("Crazy Store Milnerton (Card 572)", "crazy store"),
    ("Dis Chem Parklands (Card 572)", "dis chem"),
    ("Immediate External Payment: Atlantis Seafoods IN50135117", "atlantis seafoods"),
])
def test_the_same_shop_gets_the_same_key(description, expected):
    """Branch, city and card noise must not split one merchant into several identities."""
    assert merchants.merchant_key(description) == expected


def test_card_and_reference_noise_is_stripped():
    assert "572" not in merchants.merchant_key("Vida e Caffe Sunridge (Card 572)")
    assert merchants.merchant_key("") == ""


@pytest.mark.parametrize("description,expected", [
    # The bank's channel wording is not a counterparty — the real payee follows it.
    ("FNB App Rtc Pmt To Digg Bricks-Boards", "digg bricks"),
    ("Payshap Account Off-Us Skipp Rubble Remo", "skipp rubble"),
    ("Payshap Account Off-Us Skipp Rubble Removal", "skipp rubble"),
])
def test_a_bank_channel_is_not_mistaken_for_the_merchant(description, expected):
    """Caught by dry-running against digg-demo before anything was applied: treating "FNB App"
    as the merchant produced ONE key covering 26 unrelated payments, and split the same channel
    two ways ("FNB App Transfer To Pay" -> 'fnb pay' vs 'fnb app')."""
    assert merchants.merchant_key(description) == expected


def test_a_line_naming_nobody_gets_no_key_at_all():
    """"FNB App Transfer To Pay" identifies no counterparty. Inventing a key would group
    unrelated payments under one account; "" sends it down the per-transaction path instead."""
    assert merchants.merchant_key("FNB App Transfer To Pay") == ""
    assert merchants.merchant_key("Immediate External Payment") == ""


@pytest.mark.parametrize("description", ["Pick n Pay Table View", "Pick n Pay Milnerton",
                                         "PICK N PAY EDGEMEAD (Card 572)"])
def test_a_merchant_does_not_fragment_by_branch(description):
    """'pay' cannot be stripped as filler: doing so turned "Pick n Pay Table View" into
    'pick table' and gave every branch its own identity. It is rejected only as a WHOLE key."""
    assert merchants.merchant_key(description) == "pick pay"


# ── allocating by merchant ──────────────────────────────────────────────────────

def _profiles_db(rows):
    class _Q:
        def select(self, *a, **k): return self
        def eq(self, *a, **k): return self
        def in_(self, *a, **k): return self
        def limit(self, *a, **k): return self
        def upsert(self, *a, **k): return self
        def execute(self): return MagicMock(data=rows)
    return MagicMock(table=lambda n: _Q())


def test_every_transaction_from_one_merchant_gets_one_account():
    """The Crazy Store scatter, reproduced: 8 rows, one verdict."""
    txns = [{"description": "The Crazy Store Tablevi Cape Town (Card 572)",
             "direction": "out", "amount_cents": 9990 + i} for i in range(8)]
    db = _profiles_db([{"merchant_key": "crazy store", "account_code": "cost_of_sales",
                        "confidence": "confident", "decided_by": "owner"}])
    with patch.object(merchants, "_client", lambda: db):
        out = merchants.apply_profiles(TENANT, txns)
    assert len(out) == 8
    assert {v["account_code"] for v in out.values()} == {"cost_of_sales"}
    assert {v["source"] for v in out.values()} == {"merchant"}


def test_an_undecided_ambiguous_merchant_allocates_nothing():
    """Pick n Pay could be stock or groceries — the point is to ask, not to guess."""
    txns = [{"description": "Pick n Pay Table View", "direction": "out", "amount_cents": 5000}]
    db = _profiles_db([{"merchant_key": "pick pay", "account_code": "cost_of_sales",
                        "confidence": "ambiguous", "decided_by": "research"}])
    with patch.object(merchants, "_client", lambda: db):
        assert merchants.apply_profiles(TENANT, txns) == {}


def test_an_ambiguous_merchant_the_owner_answered_is_applied():
    txns = [{"description": "Pick n Pay Table View", "direction": "out", "amount_cents": 5000}]
    db = _profiles_db([{"merchant_key": "pick pay", "account_code": "owner_drawings",
                        "confidence": "ambiguous", "decided_by": "owner"}])
    with patch.object(merchants, "_client", lambda: db):
        out = merchants.apply_profiles(TENANT, txns)
    assert out[0]["account_code"] == "owner_drawings"


def test_money_in_is_never_allocated_by_merchant():
    """A credit is a customer paying; merchant identity says nothing about which income account."""
    txns = [{"description": "Payment Received: R MC KENZIE", "direction": "in",
             "amount_cents": 11070}]
    db = _profiles_db([{"merchant_key": "received mckenzie", "account_code": "cost_of_sales",
                        "confidence": "confident", "decided_by": "owner"}])
    with patch.object(merchants, "_client", lambda: db):
        assert merchants.apply_profiles(TENANT, txns) == {}


def test_unknown_merchants_are_listed_busiest_first():
    txns = ([{"description": "Engen Winelands North", "direction": "out", "amount_cents": 1}] * 3
            + [{"description": "Dis Chem Parklands", "direction": "out", "amount_cents": 1}]
            + [{"description": "Payment Received: X", "direction": "in", "amount_cents": 1}])
    with patch.object(merchants, "_client", lambda: _profiles_db([])):
        out = merchants.unknown_merchants(TENANT, txns)
    assert [k for k, _s, _n in out] == ["engen winelands", "dis chem"]
    assert out[0][2] == 3
    assert all("received" not in k for k, _, _ in out), "money-in is not researched"


# ── the learned-rule fix ────────────────────────────────────────────────────────

def test_a_correction_teaches_the_merchant_not_every_word():
    written = []

    class _Q:
        def select(self, *a, **k): return self
        def eq(self, *a, **k): return self
        def limit(self, *a, **k): return self
        def update(self, *a, **k): return self
        def insert(self, row):
            written.append(row)
            return self
        def execute(self): return MagicMock(data=[])

    with patch.object(accounting, "_client", lambda: MagicMock(table=lambda n: _Q())):
        accounting.learn_category_rule(
            TENANT, {"description": "The Crazy Store Tablevi Cape Town (Card 572)"},
            "cost_of_sales")
    signals = [(w["signal_type"], w["signal"]) for w in written]
    assert signals == [("merchant", "crazy store")], \
        "one merchant rule, not 8 tokens including 'store' and 'tablevi'"


def test_a_generic_token_rule_no_longer_outranks_a_merchant_rule():
    """off-the-hook's real failure: 'received' -> owner_drawings had the most hits, and
    'received' is on all 404 "Payment Received" credits."""
    queried = {}

    class _Q:
        def __init__(self): self.f = {}
        def select(self, *a, **k): return self
        def eq(self, c, v): return self
        def in_(self, col, vals):
            self.f[col] = vals
            return self
        def order(self, *a, **k): return self
        def limit(self, *a, **k): return self
        def execute(self):
            queried.setdefault("calls", []).append(dict(self.f))
            # Only the strong-signal query returns a hit.
            if self.f.get("signal_type"):
                return MagicMock(data=[{"account_code": "cost_of_sales", "hits": 1}])
            return MagicMock(data=[{"account_code": "owner_drawings", "hits": 99}])

    with patch.object(accounting, "_client", lambda: MagicMock(table=lambda n: _Q())):
        got = accounting.lookup_learned_category(
            TENANT, {"description": "The Crazy Store Tablevi", "reference": ""})
    assert got == "cost_of_sales"
    assert queried["calls"][0].get("signal_type") == ["merchant", "reference"], \
        "strong signals are consulted first, not ranked purely on hits"


# ── research ────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_a_supermarket_is_ambiguous_however_confident_the_model_sounds():
    """Research learns what a shop SELLS; it cannot know whose money it was. A deterministic
    override, because a prompt instruction alone has repeatedly proved insufficient here."""
    saved = {}
    with patch.object(merchants, "_search_pages", AsyncMock(return_value="page text")), \
         patch.object(merchants, "_classify", AsyncMock(return_value={
             "display_name": "Pick n Pay", "what_they_sell": "a supermarket chain",
             "suggested_account_code": "cost_of_sales", "confidence": "confident"})), \
         patch.object(merchants, "save_profile", lambda t, k, **kw: saved.update(kw)), \
         patch.object(merchants, "get_profile", lambda t, k: dict(saved)), \
         patch("vula.commerce.accounting.ensure_chart", lambda t: CHART):
        out = await merchants.research_merchant(TENANT, "pick pay", "Pick n Pay Table View")
    assert out["confidence"] == "ambiguous"


@pytest.mark.asyncio
async def test_an_unmistakable_trade_is_confident():
    saved = {}
    with patch.object(merchants, "_search_pages", AsyncMock(return_value="page text")), \
         patch.object(merchants, "_classify", AsyncMock(return_value={
             "display_name": "Atlantis Seafood Distributors",
             "what_they_sell": "a wholesale seafood distributor",
             "suggested_account_code": "cost_of_sales", "confidence": "confident"})), \
         patch.object(merchants, "save_profile", lambda t, k, **kw: saved.update(kw)), \
         patch.object(merchants, "get_profile", lambda t, k: dict(saved)), \
         patch("vula.commerce.accounting.ensure_chart", lambda t: CHART):
        out = await merchants.research_merchant(TENANT, "atlantis seafoods", "Atlantis Seafoods")
    assert out["confidence"] == "confident"
    assert out["account_code"] == "cost_of_sales"


@pytest.mark.asyncio
async def test_an_account_outside_the_chart_is_not_accepted():
    saved = {}
    with patch.object(merchants, "_search_pages", AsyncMock(return_value="")), \
         patch.object(merchants, "_classify", AsyncMock(return_value={
             "display_name": "X", "what_they_sell": "a seafood wholesaler",
             "suggested_account_code": "invented_code", "confidence": "confident"})), \
         patch.object(merchants, "save_profile", lambda t, k, **kw: saved.update(kw)), \
         patch.object(merchants, "get_profile", lambda t, k: dict(saved)), \
         patch("vula.commerce.accounting.ensure_chart", lambda t: CHART):
        out = await merchants.research_merchant(TENANT, "x", "X")
    assert out["account_code"] is None
    assert out["confidence"] == "ambiguous"


@pytest.mark.asyncio
async def test_a_transient_failure_is_not_cached_as_a_miss():
    """commerce_geo_cache's rule: cache a real miss so it is never re-researched, but never
    poison the cache with a network blip."""
    with patch.object(merchants, "_search_pages", AsyncMock(return_value="")), \
         patch.object(merchants, "_classify", AsyncMock(return_value=None)), \
         patch.object(merchants, "save_profile") as save, \
         patch("vula.commerce.accounting.ensure_chart", lambda t: CHART):
        out = await merchants.research_merchant(TENANT, "x", "X")
    assert out is None
    save.assert_not_called()


@pytest.mark.asyncio
async def test_research_fetches_through_the_ssrf_hardened_fetcher():
    """web_search._fetch_text accepts any URL and follows redirects with no private-IP guard.
    This path fetches search-result URLs automatically, so it must use reference_url's."""
    with patch("core.skills.web_search._ddg_search",
               AsyncMock(return_value=[{"url": "http://x.test/a", "title": "T"}])), \
         patch("vula.commerce.reference_url.safe_fetch_html",
               AsyncMock(return_value="<p>hello</p>")) as safe:
        out = await merchants._search_pages("crazy store")
    safe.assert_awaited_once()
    assert "hello" in out


@pytest.mark.asyncio
async def test_an_unsafe_url_is_skipped_not_fatal():
    with patch("core.skills.web_search._ddg_search",
               AsyncMock(return_value=[{"url": "http://169.254.169.254/", "title": "T"},
                                       {"url": "http://ok.test/", "title": "OK"}])), \
         patch("vula.commerce.reference_url.safe_fetch_html",
               AsyncMock(side_effect=[Exception("unsafe"), "<p>fine</p>"])):
        out = await merchants._search_pages("x")
    assert "fine" in out


def test_the_research_prompt_fences_untrusted_text():
    """Bank descriptions and fetched pages are both attacker-influencable; neither
    categorisation prompt fenced them before."""
    import inspect
    src = inspect.getsource(merchants._classify)
    assert "UNTRUSTED_CONTENT_RULE" in src
    assert 'fence("WEB PAGES"' in src
    assert 'fence("MERCHANT NAME"' in src
