"""Installed rates from finished work, and price advice that never swaps units (2026-10-12, DIGG).

Ian: "what each phase of the project cost, so we can cost a new project" — then "what are the
price advice rates vs what we have?". Price advice only knew item prices (a Rhino board, a tin of
paint): "ceiling" per m² had no answer, and "drywall" per m² came back as "CX25 drywall screws
R28 per m2". HPC's Interior BOQ measured 1,305 m² of drywall walls, 276 m² of plasterboard
ceilings and 76 m² of bathroom ceilings; what the job spent on each trade over that is the
installed rate a new quote can start from. Real DIGG shapes.
"""
import pytest

from tests.test_job_costing import FakeDB
from vula.commerce import job_costing, price_book, service

TID = "digg-demo"

INTERIOR_BOQ = {"tenant_id": TID, "project": "HPC Bokaap", "category": "Bill of Quantities (BOQ)",
                "fields": {"client": "HPC Cape Town", "items": [
                    {"qty": 203.5, "unit": "m²", "description": "Plastered walls"},
                    {"qty": 120, "unit": "m²", "description": "Bagged brick walls"},
                    {"qty": 1305, "unit": "m²", "description": "Dry walling walls"},
                    {"qty": 276, "unit": "m²", "description": "Plaster board ceilings"},
                    {"qty": 224, "unit": "m²", "description": "Bathroom walls (painted)"},
                    {"qty": 76, "unit": "m²", "description": "Bathroom ceilings"},
                    {"qty": 450, "unit": "m", "description": "Skirting boards"},
                    {"qty": 18, "unit": "20L", "description": "Poly Acrylic (top coat for all walls)"}]}}


def _txn(i, desc, cents, account, trade=None, project="HPC Bokaap"):
    return {"id": f"t{i}", "tenant_id": TID, "txn_date": f"2026-08-{i + 1:02d}", "description": desc,
            "amount_cents": cents, "direction": "out", "account_code": account, "project": project,
            "trade": trade, "match_status": "matched"}


@pytest.fixture()
def db(monkeypatch):
    fake = FakeDB({
        # the same BOQ filed twice ("Copy of …") counts once
        "vula_filed_documents": [INTERIOR_BOQ, {**INTERIOR_BOQ}],
        "commerce_bank_transactions": [
            _txn(0, "FNB APP RTC PMT TO NELITHO WAGES DIGG WAGE", 21_000_000, "subcontractors",
                 "Ceilings – subcontract labour"),
            _txn(1, "FNB APP PAYMENT TO SPORTY CEILINGS SOQ01726", 11_209_216, "cost_of_sales"),
            _txn(2, "FNB APP PAYMENT TO HPC CIELINGS SOQ01726", 800_000, "cost_of_sales"),
            _txn(3, "PAYSHAP ACCOUNT OFF-US HPC PAINTER WAGE", 2_125_000, "subcontractors", "Painting"),
            _txn(4, "FNB APP PAYMENT TO HPC PAINT 0084", 7_000_000, "cost_of_sales"),
            _txn(5, "FNB APP PAYMENT TO HPC GEYSER", 4_400_000, "cost_of_sales"),
            _txn(6, "NELITHO WAGES", 5_700_000, "casual_labour", "Labour"),   # trade not filed yet
        ]})
    monkeypatch.setattr(service, "_client", lambda: fake)
    monkeypatch.setattr(job_costing, "overhead_rate", lambda tid, days=90: 12.4)
    return fake


def test_installed_rate_is_what_the_trade_cost_over_its_boq_quantity(db):
    rates = {r["trade"]: r for r in job_costing.trade_rates(TID)}
    ceil = rates["Ceilings & drywall"]
    assert ceil["quantity"] == 1657            # 1,305 + 276 + 76, the copy not counted twice
    assert ceil["labour_cents"] == 21_000_000 and ceil["materials_cents"] == 11_209_216 + 800_000
    assert ceil["cost_cents_per_unit"] == round((21_000_000 + 12_009_216) / 1657)
    paint = rates["Painting"]
    assert paint["quantity"] == 2204.5         # every wall and ceiling m² on the BOQ
    assert paint["labour_cents"] == 2_125_000 and paint["materials_cents"] == 7_000_000
    assert "Plumbing" not in rates             # the geyser has no measured quantity


def test_price_advice_for_a_ceiling_quotes_the_installed_rate(db):
    adv = job_costing.price_advice(TID, "ceiling", quantity=120, unit="m2")
    per_m2 = round((21_000_000 + 12_009_216) / 1657)
    assert adv["cost_cents"] == per_m2 and adv["unit"] == "m2"
    assert adv["price_covering_overheads_cents"] == round(per_m2 * 1.124 * 1.10)
    assert "Ceilings & drywall on HPC Bokaap" in adv["text"] and "1,657 m2" in adv["text"]


def test_a_product_is_not_given_a_trades_installed_rate(db):
    price_book.record_from_document(TID, {"id": "inv1", "category": "Invoice", "fields": {
        "supplier": "GARDENS HANDIMAN CENTRE", "date": "2026-10-05",
        "line_items": [{"description": "SABRE POLY ACRYLIC PAINT 20L", "quantity": 18,
                        "unit_price_cents": 180_000}]}})
    adv = job_costing.price_advice(TID, "acrylic paint 20L")
    assert adv["cost_cents"] == 180_000 and "Painting on" not in adv["text"]


def test_drywall_per_m2_never_comes_back_as_a_screw_price(db):
    db.tables["vula_filed_documents"] = []     # no BOQ → no installed rate
    price_book.record_from_document(TID, {"id": "inv2", "category": "Invoice", "fields": {
        "supplier": "GARDENS HANDIMAN CENTRE", "date": "2026-09-20",
        "line_items": [{"description": "CX25 DRYWALL SCREWS 200PC", "quantity": 1, "unit_price_cents": 2_800}]}})
    adv = job_costing.price_advice(TID, "drywall", quantity=100, unit="m2")
    assert "cost_cents" not in adv
    assert "No per-m2 rate on file for 'drywall'" in adv["message"]
    assert "CX25 DRYWALL SCREWS 200PC R28.00 per each" in adv["message"]
    # asked without a unit, the screw price is still a fair answer about that product
    assert job_costing.price_advice(TID, "drywall screws")["cost_cents"] == 2_800


def test_a_rooms_total_is_not_stored_as_a_rate_per_m2():
    obs = price_book.observations_for({"id": "est", "tenant_id": TID, "category": "Quote / Estimate",
                                       "fields": {"supplier": "DIGG", "date": "2026-08-13", "line_items": [
        {"description": "Main Boardroom (47m²)", "quantity": 1, "unit": "m2", "unit_price_cents": 8_822_500},
        {"description": "Reception", "quantity": 1, "unit": "m2", "unit_price_cents": 30_580_500},
        {"description": "Small Meeting Room A", "quantity": 1, "unit": "m2", "unit_price_cents": 72_138}]}})
    by = {o["description"]: o for o in obs}
    assert by["Main Boardroom (47m²)"]["unit"] == "m2"
    assert by["Main Boardroom (47m²)"]["unit_price_cents"] == round(8_822_500 / 47)
    assert by["Main Boardroom (47m²)"]["quantity"] == 47
    assert by["Reception"]["unit"] == "sum"
    assert by["Small Meeting Room A"]["unit"] == "m2" and by["Small Meeting Room A"]["unit_price_cents"] == 72_138
