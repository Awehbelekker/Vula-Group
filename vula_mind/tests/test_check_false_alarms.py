"""1 Nov 2026 capability benchmark: two deterministic checks raised false alarms on correct
answers. A slip-resistance class ("R10") was read as an unconfirmed R10.00 price, and a net
figure worked out from two ledger totals got "please confirm these figures". Made-up figures."""
from core.skills.base import unverified_prices
from core.skills.finance_admin import FinanceAdminSkill

DOCS = {"find_product_document"}


def _src(text):
    return [{"name": "find_product_document", "text": text}]


# ── 1. slip ratings are not prices ────────────────────────────────────────────

def test_a_slip_class_is_not_a_price():
    answer = "The Seaview Vinyl has a slip resistance of R10 (DIN 51130 ramp test)."
    assert unverified_prices(answer, _src("Seaview Vinyl data sheet, 2 mm wear layer"), DOCS) == []


def test_slip_classes_r9_to_r13_with_rating_wording():
    for r in ("R9", "R11", "R12", "R13"):
        answer = f"Its anti-slip rating is {r}."
        assert unverified_prices(answer, _src("data sheet"), DOCS) == []


def test_a_real_price_next_to_slip_wording_is_still_checked():
    answer = "Seaview Vinyl is slip rated R10 and costs R189.50/m²."
    assert unverified_prices(answer, _src("Seaview Vinyl R10, 2 mm"), DOCS) == ["R189.50"]


def test_r10_without_slip_wording_is_still_a_price():
    assert unverified_prices("Delivery is R10 extra.", _src("delivery R25"), DOCS) == ["R10"]


def test_a_bigger_amount_near_slip_wording_is_still_a_price():
    assert unverified_prices("The anti-slip tape is R45.", _src("anti-slip tape"), DOCS) == ["R45"]


def test_strict_mode_ignores_a_lone_slip_class():
    assert unverified_prices("It's slip rated R11.", [], DOCS, strict=True) == []


# ── 2. a worked-out net is anchored, a wrong one is not ───────────────────────

def _skill(*figures):
    s = FinanceAdminSkill()
    s._verified = list(figures)
    return s


def test_net_of_two_returned_totals_is_anchored():
    s = _skill(41250.50, 98760.25, 37)
    answer = ("Last month R41,250.50 came in and R98,760.25 went out — a net outflow of "
              "R57,509.75 across 37 transactions.")
    assert s._verify_answer(answer) == (True, [])


def test_a_sum_of_two_returned_figures_is_anchored():
    s = _skill(12000.00, 8500.00)
    assert s._verify_answer("Together that's R20,500.00 (R12,000.00 + R8,500.00).")[0] is True


def test_a_wrong_net_is_still_flagged():
    s = _skill(41250.50, 98760.25)
    anchored, unmatched = s._verify_answer("A net outflow of R57,900.00.")
    assert anchored is False and unmatched == [57900.0]


def test_derived_figures_must_match_to_the_cent():
    s = _skill(41250.50, 98760.25)
    # within 1% of the true net, but not the net — not anchored
    assert s._verify_answer("A net outflow of R57,600.00.")[0] is False
