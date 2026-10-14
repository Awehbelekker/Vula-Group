"""The costing engine (Manufacturing Core phase 2): one hand-costed basin, worked out on paper,
must come out of the engine to the cent — the same figures migration 211's confirm function
produces (tests/test_manufacturing_sql.py)."""
from decimal import Decimal as D

import pytest

from vula.commerce import costing

CONCRETE = {"id": "c1", "name": "Concrete mix", "base_unit": "g", "pack_size": 25, "pack_unit": "kg",
            "unit_cost": "0.003867"}                    # (2 × R95 + R100) / 75 kg
PIGMENT = {"id": "c2", "name": "Pigment", "base_unit": "g", "unit_cost": "0.22"}
SEALER = {"id": "c3", "name": "Sealer", "base_unit": "ml", "pack_size": 5, "pack_unit": "l", "unit_cost": "0.096"}
MATS = {m["id"]: m for m in (CONCRETE, PIGMENT, SEALER)}
BOM = {"id": "b2", "product_id": "p1", "version": 2, "batch_yield": 4, "labour_hours": 6}
LINES = [{"material_id": "c1", "qty_base": 72000, "waste_pct": 5},
         {"material_id": "c2", "qty_base": 600, "waste_pct": 0},
         {"material_id": "c3", "qty_base": 1000, "waste_pct": 0}]
SETTINGS = {"labour_rate_cents": 6000, "overhead_rule": "per_labour_hour", "overhead_rate_cents": 2500}


def test_units_convert_and_refuse_a_wrong_unit():
    assert costing.to_base(18, "kg", CONCRETE) == D(18000)
    assert costing.to_base(2, "bags", CONCRETE) == D(50000)          # 2 × 25 kg
    assert costing.to_base("0.25", "l", SEALER) == D(250)
    with pytest.raises(costing.CostingError, match="measured in g, not ml"):
        costing.to_base(1, "ml", CONCRETE)
    with pytest.raises(costing.CostingError, match="pack size"):
        costing.to_base(1, "bag", PIGMENT)
    assert costing.show_qty(75600, "g") == "75.6 kg" and costing.show_qty(600, "g") == "600 g"


def test_hand_costed_batch_to_the_cent():
    # materials: 75 600 g × 0.003867 = 292.3452 + 600 g × 0.22 = 132 + 1000 ml × 0.096 = 96 → R520.3452
    # labour 6 h × R60 = R360; overhead 6 h × R25 = R150; total R1 030.35 over 3 good = R343.45
    c = costing.batch_cost(BOM, LINES, MATS, SETTINGS, good=3, rejects=1)
    assert c["materials_cents"] == 52035 and c["labour_cents"] == 36000 and c["overhead_cents"] == 15000
    assert c["total_cents"] == 103035 and c["unit_cost"] == D("343.450000") and c["unit_cost_cents"] == 34345
    assert [i["qty_base"] for i in c["inputs"]] == [D("75600.0000"), D("600.0000"), D("1000.0000")]
    assert c["complete"]


def test_rejects_make_each_good_unit_dearer_and_say_why():
    planned = costing.batch_cost(BOM, LINES, MATS, SETTINGS)                 # 4 good
    actual = costing.batch_cost(BOM, LINES, MATS, SETTINGS, good=3, rejects=1)
    assert planned["unit_cost_cents"] == 25759 and actual["unit_cost_cents"] > planned["unit_cost_cents"]
    assert costing.main_driver(actual, planned) == "yield — 1 fewer good than the recipe's 4 (1 rejected)"
    slow = costing.batch_cost(BOM, LINES, MATS, SETTINGS, labour_hours=9)
    assert costing.main_driver(slow, planned) == "labour — 9 h against 6 h expected"


def test_every_overhead_rule():
    h, g = D(6), D(3)
    assert costing.labour_and_overhead({"labour_rate_cents": 6000, "overhead_rule": "per_run",
                                        "overhead_rate_cents": 40000}, h, g) == (36000, 40000)
    assert costing.labour_and_overhead({"overhead_rule": "per_unit", "overhead_rate_cents": 1250}, h, g) == (0, 3750)
    assert costing.labour_and_overhead({"overhead_rule": "none", "overhead_rate_cents": 999}, h, g) == (0, 0)


def test_a_missing_price_is_flagged_never_guessed():
    mats = dict(MATS, c2={**PIGMENT, "unit_cost": None})
    c = costing.batch_cost(BOM, LINES, mats, SETTINGS)
    assert c["missing_costs"] == ["Pigment"] and not c["complete"]


def test_margin_excludes_vat_and_target_price():
    m = costing.margin(34345, 69000, vat_registered=True)              # R690 incl. VAT → R600 excl.
    assert m == {"price_cents": 60000, "margin_cents": 25655, "margin_pct": D("42.8")}
    assert costing.margin(34345, 60000, vat_registered=False)["margin_cents"] == 25655
    assert costing.price_for_margin(34345, 40, vat_registered=False) == 57242
    assert costing.price_for_margin(34345, 40, vat_registered=True) == 65828


def test_cost_sheet_text_reads_from_the_sheet():
    sheet = {"product": "Round basin", "recipe_version": 2, "vat_registered": False,
             "expected": costing.batch_cost(BOM, LINES, MATS, SETTINGS),
             "margin": costing.margin(25759, 60000, vat_registered=False), "runs": [{"unit_cost_cents": 34345}]}
    text = costing.cost_sheet_text(sheet)
    assert "• Concrete mix: 75.6 kg — R292.35" in text and "*R257.59 each*" in text
    assert "margin *R342.41*" in text and "Last batches: R343.45" in text
