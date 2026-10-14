"""Costing from what the business already paid (vula/commerce/costing_history.py) and the
"set up costing" questions on WhatsApp (vula/commerce/costing_setup.py).

Prices come from the price book's invoice lines (pack size read from the wording, VAT taken off
or left on per how the invoice was written and whether the business is VAT registered); labour
and running costs from the bank lines. Every suggestion is offered, never set on its own."""
from datetime import date
from decimal import Decimal as D

import pytest

from tests.test_manufacturing_flows import TID, Store, store  # noqa: F401 — the shared costing store fixture
from vula.commerce import costing, costing_history as hist, costing_setup as setup, manufacturing as mfg, \
    price_book, service


def obs(desc, cents, *, doc="d1", on="2026-09-03", supplier="BUILD IT", kind=None, unit=None, source="invoice"):
    return {"description": desc, "norm_key": price_book.norm_key(desc), "unit": unit,
            "kind": kind or price_book.kind_of(desc), "supplier": supplier, "project": None,
            "source_kind": source, "quantity": 1, "unit_price_cents": cents, "observed_on": on, "doc_id": doc}


# ── reading invoice wording ──────────────────────────────────────────────────────────────

@pytest.mark.parametrize("text,want", [
    ("CEMENT MIX 25KG", (D(25), "kg")), ("Sealer 5L", (D(5), "l")), ("PIGMENT RED 500 G", (D(500), "g")),
    ("6 x 500ml glue", (D(3000), "ml")), ("PVA 20 Lt", (D(20), "l")), ("2x4 timber 3m", (D(3), "m")),
    ("Rhino board 9.5mm", None),            # a thickness, not what the pack holds
    ("Hinge brass", None),
])
def test_pack_size_from_the_invoice_wording(text, want):
    assert hist.pack_from_text(text) == want


def test_vat_basis_is_read_from_how_the_invoice_adds_up():
    incl = {"total_cents": 11500, "vat_cents": 1500, "line_items": [{"total_cents": 11500}]}
    excl = {"total_cents": 11500, "vat_cents": 1500, "line_items": [{"total_cents": 10000}]}
    none = {"total_cents": 10000, "vat_cents": 0, "line_items": [{"total_cents": 10000}]}
    assert [hist._vat_basis(f) for f in (incl, excl, none, {})] == ["incl", "excl", "no_vat", "unknown"]
    # registered: cost is before VAT; not registered: the VAT is part of the cost
    assert hist.cost_basis_price(11500, "incl", True)[0] == 10000
    assert hist.cost_basis_price(10000, "excl", True)[0] == 10000
    assert hist.cost_basis_price(10000, "excl", False)[0] == 11500
    assert hist.cost_basis_price(11500, "incl", False)[0] == 11500
    assert "check" in hist.cost_basis_price(9500, "unknown", True)[1]


def test_material_names_match_invoice_items_offered_not_assumed():
    assert hist._match_score("cement", "cem ii 42 5n 50kg") > 0
    assert hist._match_score("sealer", "sealant clear 5l") > 0
    assert hist._match_score("concrete mix", "cement mix 25kg") == 0          # different things
    assert hist._match_score("sand", "sand river 40kg") > hist._match_score("sand", "sanding disc 115mm")


# ── price suggestions ────────────────────────────────────────────────────────────────────

@pytest.fixture
def book(monkeypatch):
    rows = [obs("CEMENT MIX 25KG", 9775, doc="d1", on="2026-09-03"),
            obs("CEMENT MIX 25KG", 9200, doc="d0", on="2026-07-10"),
            obs("SANDING DISC 115MM", 1500, doc="d2"),
            obs("SAND RIVER 40KG", 4600, doc="d2"),
            obs("Tiling labour per m2", 16800, doc="d3", unit="m2")]
    fields = {"d1": {"total_cents": 9775, "vat_cents": 1275, "line_items": [{"total_cents": 9775}]},   # incl
              "d2": {"total_cents": 7015, "vat_cents": 915, "line_items": [{"total_cents": 1500}, {"total_cents": 4600}]}}
    monkeypatch.setattr(price_book, "_fetch", lambda tid, *a, **k: list(rows))
    monkeypatch.setattr(hist, "_doc_fields", lambda tid, doc: fields.get(doc, {}))
    monkeypatch.setattr(hist, "remembered_key", lambda tid, mid: None)
    monkeypatch.setattr(costing, "_vat", lambda tid: (True, True))
    return rows


def test_cement_price_from_the_latest_invoice_vat_taken_off(book):
    s = hist.price_suggestions(TID, "cement", material={"id": "m1", "name": "Cement", "base_unit": "g"})
    assert len(s) == 1
    top = s[0]
    assert top["invoice_price_cents"] == 9775 and top["vat_basis"] == "incl"
    assert top["price_cents"] == 8500                                 # R97.75 / 1.15
    assert (top["pack_size"], top["pack_unit"]) == (D(25), "kg")
    assert hist.show_unit_cost(top["unit_cost"], "g") == "R3.40 per kg"
    line = hist.suggestion_line(top, 1)
    assert line.startswith("1. *CEMENT MIX 25KG* — R97.75 (BUILD IT, 2026-09-03; 2 times R92.00–R97.75)")
    assert "R3.40 per kg" in line and "VAT taken off" in line


def test_labour_lines_are_never_offered_as_material_prices(book):
    assert hist.price_suggestions(TID, "tiling") == []


def test_exact_word_ranks_first_and_lines_priced_excl_vat_stay(book):
    s = hist.price_suggestions(TID, "sand", material={"id": "m2", "name": "Sand", "base_unit": "g"})
    assert [x["description"] for x in s] == ["SAND RIVER 40KG"]           # the disc is measured in mm, not g
    assert s[0]["vat_basis"] == "excl" and s[0]["price_cents"] == 4600
    each = hist.price_suggestions(TID, "sand")                            # no material yet: any unit
    assert [x["description"] for x in each][:2] == ["SAND RIVER 40KG", "SANDING DISC 115MM"]


def test_remembered_match_comes_first(book, monkeypatch):
    monkeypatch.setattr(hist, "remembered_key", lambda tid, mid: price_book.norm_key("SANDING DISC 115MM"))
    s = hist.price_suggestions(TID, "sand", material={"id": "m2", "name": "Sand", "base_unit": "each"})
    assert s[0]["description"] == "SANDING DISC 115MM"


# ── the owner picks one (preview → confirm → read back) ──────────────────────────────────

@pytest.fixture
def priced(store, book, monkeypatch):
    def set_price(tid, material, unit_cost, *, key=None, note=None, actor=None):
        m = store.materials[material["id"]]
        m["unit_cost"] = D(str(unit_cost)).quantize(D("0.000001"))
        m["_key"], m["_note"] = key, note
        return m
    monkeypatch.setattr(hist, "set_price", set_price)

    class _Q:
        def __init__(self, target):
            self.t, self.vals = target, None

        def update(self, vals):
            self.vals = vals
            return self

        def eq(self, col, val):
            if col == "id":
                store.materials[val].update(self.vals)
            return self

        def execute(self):
            return self
    monkeypatch.setattr(costing, "_db", lambda: type("DB", (), {"table": lambda self, n: _Q(n)})())
    return store


def test_use_the_invoice_price_for_cement(priced):
    m = priced.create_material(TID, "Cement", "kg")
    ask = mfg.price_material(TID, "cement")
    assert ask["status"] == "need_info" and ask["suggestions"][0].startswith("1. *CEMENT MIX 25KG*")
    prev = mfg.price_material(TID, "cement", pick=1)
    assert prev["preview"] and prev["price"] == "R3.40 per kg" and prev["pack"].startswith("25 kg")
    assert priced.materials[m["id"]]["unit_cost"] is None                  # nothing set on the preview
    done = mfg.price_material(TID, "cement", pick=1, confirm=True)
    assert done["reply_verbatim"] == "✅ Cement: R3.40 per kg (packs of 25 kg)."
    assert priced.materials[m["id"]]["_key"] == price_book.norm_key("CEMENT MIX 25KG")
    assert priced.materials[m["id"]]["pack_size"] == 25.0


def test_owner_says_what_they_pay(priced):
    priced.create_material(TID, "Pigment", "g")
    done = mfg.price_material(TID, "pigment", total_cents=8500, qty=500, unit="g", confirm=True)
    assert done["reply_verbatim"] == "✅ Pigment: R170.00 per kg."


def test_new_material_reply_points_at_the_invoice_price(priced):
    r = mfg.add_material(TID, "Cement", "kg", confirm=True)
    assert "Your invoices show *CEMENT MIX 25KG*" in r["reply_verbatim"]
    assert "use the invoice price for Cement" in r["reply_verbatim"]


# ── labour and running costs from the bank ───────────────────────────────────────────────

def test_bank_monthly_figures_and_rates(monkeypatch):
    lines = {
        "labour": [{"txn_date": "2026-07-20", "amount_cents": 1840000, "account_code": "wages"},
                   {"txn_date": "2026-08-20", "amount_cents": 1840000, "account_code": "wages"},
                   {"txn_date": "2026-09-20", "amount_cents": 1840000, "account_code": "casual_labour"}],
        "over": [{"txn_date": "2026-07-17", "amount_cents": 2400000, "account_code": "rent"},
                 {"txn_date": "2026-08-01", "amount_cents": 645000, "account_code": "utilities"}]}
    monkeypatch.setattr(hist, "_bank_out", lambda tid, accounts, since:
                        lines["labour"] if "wages" in accounts else lines["over"])
    b = hist.bank_suggestions(TID, today=date(2026, 10, 14))
    assert b["months"] == D("2.92")                                      # 17 Jul → 14 Oct
    assert b["labour_monthly_cents"] == 1890411                          # R55,200 / 2.92
    assert b["overhead_by_account"][0] == ("rent", 821918)
    assert b["overhead_monthly_cents"] == 1042808
    hpm = hist.hours_per_month(80)
    assert hpm == D("346.67")
    assert hist.per(b["labour_monthly_cents"], hpm) == 5453               # R54.53 an hour
    assert hist.per(None, hpm) is None and hist.per(100, 0) is None


# ── "set up costing", one question at a time ─────────────────────────────────────────────

@pytest.mark.asyncio
async def test_set_up_costing_conversation(priced, monkeypatch):
    setup._reset()
    monkeypatch.setattr(setup, "_modules_ok", lambda tid: True)
    monkeypatch.setattr(setup, "_product_row", lambda tid, p: next(x for x in priced.products if x["id"] == p["id"]))
    monkeypatch.setattr(hist, "bank_suggestions", lambda tid: {
        "months": D(3), "since": "2026-07-16", "labour_monthly_cents": 1840000, "overhead_monthly_cents": 1075000,
        "overhead_by_account": [("rent", 800000), ("electricity & water", 215000), ("insurance", 60000)]})

    def save_settings(tid, *, labour_rate_cents=None, overhead_rule=None, overhead_rate_cents=None, actor=None):
        priced.settings.update(labour_rate_cents=labour_rate_cents, overhead_rule=overhead_rule,
                               overhead_rate_cents=overhead_rate_cents)
        return priced.settings
    monkeypatch.setattr(costing, "save_settings", save_settings)
    phone = "27662545068"

    async def say(text):
        return await setup.handle(TID, phone, text)

    assert await say("what does a basin cost us") is None                 # not started → not ours
    r = await say("set up costing")
    assert "Which product shall we cost?" in r
    r = await say("round drop-in basin")
    assert "What goes into ONE batch of *Round Drop-In Basin*" in r
    r = await say("72 kg cement, 600 g pigment and 1 l sealer — makes 4, 6 hours")
    assert "• 72 kg cement\n• 600 g pigment\n• 1 l sealer" in r and "Makes 4 good; 6 labour hours" in r
    assert not priced.boms                                                # shown first, not saved
    r = await say("yes")
    assert "Recipe saved for *Round Drop-In Basin*" in r and len(priced.boms) == 1
    r = await say("next")
    assert "Now prices — 3 materials without one." in r
    assert "*cement* — your invoices show:\n1. *CEMENT MIX 25KG*" in r
    r = await say("yes")
    assert "✅ cement: R3.40 per kg (packs of 25 kg)." in r
    assert "What do you pay for *pigment*?" in r                          # nothing like it on invoices
    r = await say("R85 for 500 g")
    assert "✅ pigment: R170.00 per kg." in r
    r = await say("R480 for 5 l")
    assert "✅ sealer: R96.00 per l." in r
    assert "wages and casual labour came to about *R18,400.00 a month*" in r
    r = await say("2 people, 40 hours each")
    assert "That's about R53.08 per labour hour (R18,400.00 a month ÷ 346.67 hours)" in r
    r = await say("yes")
    assert "Running costs from your bank: rent R8,000.00 · electricity & water R2,150.00" in r
    r = await say("1")
    assert "That's R31.01 per labour hour" in r
    r = await say("yes")
    assert r == "Save this? Labour R53.08 an hour; running costs: R31.01 per labour hour. *yes* / *no*"
    assert priced.settings["labour_rate_cents"] == 6000                    # still the old one
    r = await say("yes")
    assert priced.settings == {**priced.settings, "labour_rate_cents": 5308, "overhead_rule": "per_labour_hour",
                               "overhead_rate_cents": 3101}
    assert "Costing is set up ✅" in r and "Round Drop-In Basin" in r
    assert not setup.active(TID, phone)


@pytest.mark.asyncio
async def test_set_up_costing_needs_the_module_and_stop_ends_it(priced, monkeypatch):
    setup._reset()
    monkeypatch.setattr(setup, "_modules_ok", lambda tid: False)
    assert "Manufacturing module" in await setup.handle(TID, "p", "set up costing")
    monkeypatch.setattr(setup, "_modules_ok", lambda tid: True)
    await setup.handle(TID, "p", "set up costing")
    assert "Saved what we did" in await setup.handle(TID, "p", "stop")
    assert await setup.handle(TID, "p", "yes") is None


@pytest.mark.asyncio
async def test_a_unit_that_does_not_fit_is_asked_again(priced, monkeypatch):
    setup._reset()
    monkeypatch.setattr(setup, "_modules_ok", lambda tid: True)
    priced.create_material(TID, "Cement", "kg")
    await setup.handle(TID, "p", "set up costing")
    await setup.handle(TID, "p", "round drop-in basin")
    r = await setup.handle(TID, "p", "2 l cement — makes 1, 1 hour")
    assert "Cement is measured in g, not l" in r and "What goes into ONE batch" in r


@pytest.mark.parametrize("text,cents", [("R18,400", 1840000), ("R 18 400", 1840000), ("R95,50", 9550),
                                        ("950 rand", 95000), ("18400", 1840000), ("R1 500 for 2 bags", 150000)])
def test_money_as_people_write_it(text, cents):
    assert setup.parse_money(text) == cents


def test_prices_and_hours_as_people_write_them():
    assert setup.parse_price("R95 for a 25 kg bag") == {"total_cents": 9500, "qty": D(25), "unit": "kg",
                                                        "pack": (D(25), "kg")}
    assert setup.parse_price("R3.80/kg")["unit"] == "kg"
    assert setup.parse_price("R190 for 2 bags")["unit"] == "bags"
    assert setup.parse_price("R95")["qty"] is None
    assert setup.parse_hours_week("2 people, 40 hours each") == 80
    assert setup.parse_hours_week("3 x 45") == 135
    assert setup.parse_rate("R400 a day") == 5000
    r = setup.parse_recipe("Concrete mix: 18kg; pigment 150 g and 0,25 l sealer, 2 x bolts")
    assert [(i["material"], i["qty"], i["unit"]) for i in r["ingredients"]] == [
        ("Concrete mix", 18.0, "kg"), ("pigment", 150.0, "g"), ("sealer", 0.25, "l"), ("bolts", 2.0, "each")]
