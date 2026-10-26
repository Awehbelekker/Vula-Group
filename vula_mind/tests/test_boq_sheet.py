"""DIGG's Belladonna project bill, 26 Oct.

Judy sent "labour cost.xlsx" — Item | Unit | Qty | Trade | Budget | Actual | Invoiced |
Outstanding, rooms as heading rows. The sheet reader never found its header (a bare "Item" only
counted as the code column), so the model's 5-line read stood: no rooms, no total, and none of the
bathroom or bedroom lines.
"""
from openpyxl import Workbook

from vula.ingestion import boq_sheet

HEADER = ["Item", "Unit", "Qty", "Trade", "Budget", "Actual", "Invoiced", "Outstanding"]
ROWS = [
    ["Belladonna Flat – project bill"],
    [],
    HEADER,
    ["General / Site-wide"],
    ["P&G — Site Management", "wks", 4, "DiGG", 48000, 48000, 36000, 12000],
    ["Bricks (donated, delivery only)", None, None, "DiGG", 3000, 0, 0, 3000],
    ["Main Bathroom"],
    ["Wall + floor tiles, waterproofing", None, None, "Tiling team", 8400, 9120.5, 8400, 720.5],
    ["Extractor fan, lighting, sockets", None, None, "Elyas", None, None, None, None],
    ["Paint (moisture-resistant)", None, None, "Eric", 1500, None, None, None],
    ["Guest Bathroom"],
    ["Sanitaryware (toilet, basin, taps)", None, None, "Flush", 17000, 18268.3, 17000, 1268.3],
    ["Extra: heated towel rail", None, None, "Flush", None, 7799.89, 0, 7799.89],
    ["TOTAL", None, None, None, 77900, 90188.69, 61400, 24788.69],
]


def _sheet(path, rows=ROWS):
    wb = Workbook()
    ws = wb.active
    ws.title = "Project Bill"
    for r in rows:
        ws.append(r)
    wb.save(path)


def test_judys_project_bill_is_read_from_its_cells(tmp_path):
    p = tmp_path / "labour cost.xlsx"
    _sheet(p)
    lines = boq_sheet.parse(p)
    assert [li["description"] for li in lines] == [
        "P&G — Site Management", "Bricks (donated, delivery only)", "Wall + floor tiles, waterproofing",
        "Paint (moisture-resistant)", "Sanitaryware (toilet, basin, taps)", "Extra: heated towel rail"]
    pg = lines[0]
    assert pg["total_cents"] == 4800000 and pg["unit_price_cents"] == 1200000 and pg["unit"] == "wks"
    assert (pg["actual_cents"], pg["invoiced_cents"], pg["outstanding_cents"]) == (4800000, 3600000, 1200000)
    assert pg["trade"] == "DiGG" and pg["section"] == "General / Site-wide"
    assert lines[2]["section"] == "Main Bathroom" and lines[2]["actual_cents"] == 912050
    assert lines[3]["actual_cents"] is None and lines[3]["section"] == "Main Bathroom"


def test_an_unpriced_line_is_not_taken_for_a_room_heading(tmp_path):
    p = tmp_path / "labour cost.xlsx"
    _sheet(p)
    lines = boq_sheet.parse(p)
    assert all(li["section"] != "Extractor fan, lighting, sockets" for li in lines)
    assert "Extractor fan, lighting, sockets" not in [li["description"] for li in lines]


def test_an_extra_with_no_budget_is_kept_with_what_it_cost(tmp_path):
    p = tmp_path / "labour cost.xlsx"
    _sheet(p)
    extra = boq_sheet.parse(p)[-1]
    assert extra["description"] == "Extra: heated towel rail" and extra["section"] == "Guest Bathroom"
    assert extra["total_cents"] is None and extra["actual_cents"] == 779989


def test_room_totals_carry_budget_actual_and_invoiced(tmp_path):
    p = tmp_path / "labour cost.xlsx"
    _sheet(p)
    fields = boq_sheet.apply_to_fields({"line_items": [{"description": "P&G"}] * 5}, boq_sheet.parse(p))
    assert fields["sections"] == [
        {"section": "General / Site-wide", "budget_cents": 5100000, "actual_cents": 4800000,
         "invoiced_cents": 3600000},
        {"section": "Main Bathroom", "budget_cents": 990000, "actual_cents": 912050, "invoiced_cents": 840000},
        {"section": "Guest Bathroom", "budget_cents": 1700000, "actual_cents": 2606819,
         "invoiced_cents": 1700000},
    ]
    assert fields["total_cents"] == 7790000              # the TOTAL row is never a line
    assert fields["line_items_source"] == "sheet"


def test_a_room_column_is_the_section(tmp_path):
    p = tmp_path / "bill.xlsx"
    _sheet(p, [["Room", "Item", "Budget", "Actual"],
               ["Kitchen", "Cabinetry", 35000, 36100],
               ["Hallway", "Flooring / paint / stair finish", 9000, None]])
    lines = boq_sheet.parse(p)
    assert [(li["section"], li["total_cents"]) for li in lines] == [("Kitchen", 3500000), ("Hallway", 900000)]


def test_actual_cost_is_never_the_amount(tmp_path):
    p = tmp_path / "bill.xlsx"
    _sheet(p, [["Item", "Actual cost", "Budget"], ["Plumbing — whole flat", 35850, 29850]])
    (li,) = boq_sheet.parse(p)
    assert li["total_cents"] == 2985000 and li["actual_cents"] == 3585000


def test_item_stays_the_code_beside_a_description_column(tmp_path):
    p = tmp_path / "boq.xlsx"
    _sheet(p, [["Item", "Description", "Qty", "Unit", "Rate", "Amount"],
               [None, "Airconditioning"],
               ["1.1", "Cassette unit 1 kW supply & install", 2, "no", 18500, 37000]])
    assert boq_sheet.parse(p) == [{"description": "Cassette unit 1 kW supply & install", "quantity": 2,
                                   "unit": "no", "unit_price_cents": 1850000, "total_cents": 3700000,
                                   "section": "Airconditioning", "code": "1.1"}]
