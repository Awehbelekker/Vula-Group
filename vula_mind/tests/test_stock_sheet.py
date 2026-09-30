"""A distributor stock sheet must be readable as a SET of rows, not as prose.

gerflor, 2026-09-07: a rep uploaded "DT SOH and Planning 07.09.26.pdf" and asked "how's
Creation stock". Creation is not on that sheet at all, and similarity search can never say so —
it can rank what a document contains, never report what it omits. So the sheet is parsed to
rows and answered deterministically (vula/commerce/stock_sheet.py, migration 156).
"""
from unittest.mock import patch

import pytest

from vula.commerce import stock_sheet as ss

# A trimmed real-shape DT SOH sheet.
SHEET = """DT STOCK ON HAND (SOH m²) - 07.09.26

VIRTUO 30 COL: SUNNY WHITE 2.00MM 532
VIRTUO 30 COL: BAITA MEDIUM 2.00MM 581.4 534 Est. Mid Nov - TBC
MAC TILES-COL: 612 ST/GREY 1.60MM 6504.3
MAC TILES-COL: 656 BASIL 2.00MM 378 164m2 Reserved TVET College
AMBIANCE ULTRA TERRA COL: 0203 STEAM GREY 2.00W 540 2000 Est. Mid Oct - TBC
EL7 SD ROBUST COL: 0098 CONCRETE 2.00MM 0
"""


def test_recognises_a_real_stock_sheet():
    assert ss.looks_like_stock_sheet(SHEET) is True


def test_ignores_an_unrelated_document():
    assert ss.looks_like_stock_sheet("Tax Invoice\nTotal due R2.00mm\n" * 6) is False


def test_parses_rows_with_product_colour_soh_and_note():
    rows = ss.parse_stock_lines(SHEET)
    assert len(rows) == 6
    virtuo = next(r for r in rows if "SUNNY WHITE" in r["colour"])
    assert virtuo["product"] == "VIRTUO 30"
    assert virtuo["thickness"] == "2.00MM"
    assert virtuo["soh"] == 532.0
    baita = next(r for r in rows if "BAITA" in r["colour"])
    assert baita["note"] and "Nov" in baita["note"]


def test_as_at_date_from_header():
    assert ss.as_at_date(SHEET) == "07.09.26"


def test_absent_product_reports_what_IS_stocked():
    rows = ss.parse_stock_lines(SHEET)
    out = ss.summarise(rows, "Creation", as_at="07.09.26")
    assert out["found"] is False
    assert "not on this stock list" in out["answer"]
    assert "VIRTUO 30" in out["available_products"]


def test_present_product_totals_soh_and_singular_plural():
    rows = ss.parse_stock_lines(SHEET)
    out = ss.summarise(rows, "mac tiles", as_at="07.09.26")
    assert out["found"] is True
    assert out["line_count"] == 2
    # 6504.3 + 378
    assert out["total_soh"] == pytest.approx(6882.3)


def _sheet_row():
    return {"filename": "DT SOH 07.09.26.pdf", "as_at": "07.09.26",
            "rows": ss.parse_stock_lines(SHEET),
            "product_ranges": ss.product_names(ss.parse_stock_lines(SHEET))}


def test_answer_stock_query_returns_deterministic_hit():
    with patch.object(ss, "latest_rows_for_tenant", return_value=_sheet_row()):
        out = ss.answer_stock_query("gerflor", "how's virtuo stock")
    assert out["found"] is True
    assert out["source_file"] == "DT SOH 07.09.26.pdf"


def test_answer_stock_query_absent_product_still_answers_when_stock_intent():
    with patch.object(ss, "latest_rows_for_tenant", return_value=_sheet_row()):
        out = ss.answer_stock_query("gerflor", "do we have any Creation in stock")
    assert out is not None and out["found"] is False


def test_answer_stock_query_does_not_hijack_unrelated_lookups():
    # A stock sheet on file must not turn "what's our VAT number" into "VAT is not on the list".
    with patch.object(ss, "latest_rows_for_tenant", return_value=_sheet_row()):
        assert ss.answer_stock_query("gerflor", "what is our VAT registration number") is None


def test_answer_stock_query_none_when_no_sheet():
    with patch.object(ss, "latest_rows_for_tenant", return_value=None):
        assert ss.answer_stock_query("someone-else", "virtuo stock") is None


# ── 2026-09-30: DT_Weekly_SOH_Stock_Availability_29-09-2026.pdf — one field per line ─────────
# The line-per-row parser read 6 of 132 lines; "1,949.4" (thousands comma) was lost; "as at 29
# September 2026" wasn't read; and "Virtuo 55 Daintree Brown" matched all 33 VIRTUO lines.
DT_WEEKLY = """PRODUCT / COLOUR / DESCRIPTION
SOH m²
STOCK STATUS
INBOUND QTY
ETA
NOTES / RESERVATIONS
VIRTUO 30 COL: BAITA BLOND 2.00MM
1,949.4
● IN STOCK
VIRTUO 30 COL: BAITA MEDIUM 2.00MM
581.4
→ INBOUND
534.0
Est. End October
VIRTUO 55 COL: SUNNY BLACK 2.50MM
0.0
● OUT OF STOCK
VIRTUO 55 COL: DAINTREE BROWN 2.50MM
99.2
◆ PROJECT ALLOCATED
300.0
❗Est. End October
150m² Reserved for Tstisikama
ATLAS COL: 6006 SPRING GREEN 2.00W
0.0
● OUT OF STOCK
TROPLAN PLUS COL: 1006 LT-BLUE 2.00W
6,280.0
● IN STOCK
TROPLAN PLUS COL: 1056 DK-BLUE 2.00W
1,860.0
● IN STOCK
VINYL SHEETING-ROBUST COL: 0002/2014 PLATINUM GREY 
2.00W
140.0
● IN STOCK
MAC TILES-COL: 612 ST/GREY 1.60MM
6,504.3
● IN STOCK
DECOR TRADER – WEEKLY STOCK AVAILABILITY & INBOUND PLANNING
Stock position as at 29 September 2026  |  Internal Sales & Project Planning Tool
"""


def test_the_weekly_layout_is_read_line_by_line():
    from vula.commerce import stock_sheet as ss
    rows = ss.parse_stock_lines(DT_WEEKLY)
    by = {(r["product"], r["colour"]): r for r in rows}
    assert len(rows) == 9
    assert by[("VIRTUO 30", "BAITA BLOND")]["soh"] == 1949.4
    db = by[("VIRTUO 55", "DAINTREE BROWN")]
    assert (db["soh"], db["status"], db["inbound"]) == (99.2, "project allocated", 300.0)
    assert db["note"] == "Est. End October; 150m² Reserved for Tstisikama"
    assert by[("VINYL SHEETING-ROBUST", "0002/2014 PLATINUM GREY")]["soh"] == 140.0   # wrapped name
    assert ss.as_at_date(DT_WEEKLY) == "29 September 2026"


@pytest.mark.parametrize("q,want", [
    ("Is there stock of Virtuo 55 Daintree Brown?",
     "VIRTUO 55 DAINTREE BROWN: 99.2 m² (project allocated), 300 m² inbound, Est. End October; "
     "150m² Reserved for Tstisikama — as at 29 September 2026."),
    ("Do we have Atlas 6006 spring green?", "ATLAS 6006 SPRING GREEN: 0 m² (out of stock) — as at 29 September 2026."),
    ("How much Troplan Plus LT-Blue do we have", "TROPLAN PLUS 1006 LT-BLUE: 6280 m² (in stock) — as at 29 September 2026."),
    ("I need INDIANA 8837 SONGO 635X635 7 square metres",
     "INDIANA 8837 SONGO 635X635 is not on this stock list (as at 29 September 2026)."),
])
def test_a_rep_gets_the_line_he_asked_about(q, want):
    from vula.commerce import stock_sheet as ss
    assert ss.summarise(ss.parse_stock_lines(DT_WEEKLY), q, ss.as_at_date(DT_WEEKLY))["answer"] == want


def test_a_colour_not_on_the_sheet_is_said_first():
    from vula.commerce import stock_sheet as ss
    out = ss.summarise(ss.parse_stock_lines(DT_WEEKLY), "Is there stock of Mac tiles basil", "")
    assert out["answer"].startswith("BASIL isn't on this stock list. What is: MAC TILES 612 ST/GREY")


@pytest.mark.asyncio
async def test_richard_gets_the_stock_line_without_a_model(monkeypatch):
    """Ian, 30 Sep: "if Richard asks is there stock of a certain item, it should tell him"."""
    from vula.commerce import stock_sheet as ss
    from core.skills.base import SkillInput
    from core.skills.commerce_admin import CommerceAdminSkill
    monkeypatch.setattr(ss, "latest_rows_for_tenant", lambda t: {
        "filename": "DT_Weekly_SOH_Stock_Availability_29-09-2026.pdf",
        "as_at": "29 September 2026", "rows": ss.parse_stock_lines(DT_WEEKLY)})
    monkeypatch.setattr("vula.api.tenants.tenant_profile", lambda t: {"sells_products": False})
    monkeypatch.setattr("core.skills.commerce_admin.CommerceAdminSkill._agent_loop",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError("no model")))
    out = await CommerceAdminSkill().run(SkillInput(
        question="Is there stock of Virtuo 55 Daintree Brown?", tenant_id="gerflor",
        metadata={"caller_role": "sales_rep", "customer_phone": "27000"}))
    assert out.answer.startswith("📦 VIRTUO 55 DAINTREE BROWN: 99.2 m² (project allocated)")
    assert "DT_Weekly_SOH_Stock_Availability_29-09-2026.pdf" in out.answer


def test_a_shop_s_stock_question_is_left_to_its_own_stock(monkeypatch):
    from core.skills.commerce_admin import _stock_sheet_answer
    monkeypatch.setattr("vula.api.tenants.tenant_profile", lambda t: {"sells_products": True})
    assert _stock_sheet_answer("off-the-hook", "Is there stock of hake?") is None
