"""Owner flows for costing (vula/commerce/manufacturing.py) as WhatsApp drives them: add a
material, book deliveries, save a recipe, log a batch, undo it — preview first, then confirm, then
the reply built from what was read back. The store below follows migration 211's rules (weighted
average on receipt, versions, issue at the average); the SQL itself is tested on a real Postgres in
tests/test_manufacturing_sql.py."""
from decimal import Decimal as D

import pytest

from vula.commerce import costing, manufacturing as mfg, service

TID = "seedleme"


class Store:
    def __init__(self):
        self.materials, self.boms, self.runs, self.n = {}, [], {}, 0
        self.settings = {"tenant_id": TID, "labour_rate_cents": 6000, "overhead_rule": "per_labour_hour",
                         "overhead_rate_cents": 2500}
        self.products = [{"id": "p1", "name": "Round Drop-In Basin", "price_cents": 69000},
                         {"id": "p2", "name": "Oval Basin - Sage", "price_cents": 79000},
                         {"id": "p3", "name": "Oval Basin - Charcoal", "price_cents": 79000}]

    def _id(self, p):
        self.n += 1
        return f"{p}{self.n}"

    def list_materials(self, tid):
        return sorted([m for m in self.materials.values() if m["active"]], key=lambda m: m["name"])

    def create_material(self, tid, name, unit, *, pack_size=None, pack_unit=None, supplier=None):
        m = {"id": self._id("m"), "tenant_id": tid, "name": " ".join(name.split()),
             "base_unit": costing.base_unit_for(unit), "pack_size": pack_size, "pack_unit": pack_unit,
             "unit_cost": None, "stock_qty": D(0), "active": True}
        self.materials[m["id"]] = m
        return m

    def move_material(self, tid, material, *, qty=None, unit=None, total_cents=None, reason="receive", **kw):
        m = self.materials[material["id"]]
        base = costing.to_base(qty, unit or m["base_unit"], m)
        cost = (D(total_cents) / 100 / base).quantize(D("0.000001")) if total_cents is not None else None
        if cost is not None and reason in ("receive", "opening"):
            old = max(D(m["stock_qty"]), D(0))
            m["unit_cost"] = cost if m["unit_cost"] is None or old == 0 else \
                ((old * D(str(m["unit_cost"])) + base * cost) / (old + base)).quantize(D("0.000001"))
        m["stock_qty"] = D(m["stock_qty"]) + base
        return {"qty_after": m["stock_qty"], "unit_cost_after": m["unit_cost"], "base_qty": base, "unit_cost": cost}

    def active_bom(self, tid, *, product_id=None, material_id=None):
        return next((b for b in self.boms if b["product_id"] == product_id and b["status"] == "active"), None)

    def save_recipe(self, tid, *, product_id=None, material_id=None, lines, batch_yield=1, labour_hours=0, **kw):
        version = 1 + max([b["version"] for b in self.boms if b["product_id"] == product_id] or [0])
        for b in self.boms:
            if b["product_id"] == product_id:
                b["status"] = "archived"
        bom = {"id": self._id("b"), "product_id": product_id, "material_id": None, "version": version,
               "status": "active", "batch_yield": D(str(batch_yield)), "labour_hours": D(str(labour_hours)),
               "lines": [{"material_id": ln["material"]["id"], "qty": ln["qty"], "unit": ln["unit"],
                          "qty_base": costing.to_base(ln["qty"], ln["unit"], ln["material"]),
                          "waste_pct": ln.get("waste_pct") or 0} for ln in lines]}
        self.boms.append(bom)
        return bom["id"]

    def last_runs(self, tid, *, product_id=None, material_id=None, limit=10):
        rs = [r for r in self.runs.values() if r["product_id"] == product_id and r["status"] == "confirmed"]
        return list(reversed(rs))[:limit]

    def draft_run(self, tid, bom, preview, **kw):
        r = {"id": self._id("r"), "bom_id": bom["id"], "product_id": bom["product_id"], "status": "draft",
             "good_qty": preview["good"], "reject_qty": preview["rejects"],
             "labour_hours": preview["labour_hours"], "inputs": preview["inputs"],
             "labour_cents": preview["labour_cents"], "overhead_cents": preview["overhead_cents"]}
        self.runs[r["id"]] = r
        return r

    def confirm_run(self, tid, run_id, actor=None):
        r = self.runs[run_id]
        if r["status"] != "draft":
            return {"error": "already_" + r["status"]}
        mat = sum(D(i["qty_base"]) * D(str(self.materials[i["material_id"]]["unit_cost"])) for i in r["inputs"])
        for i in r["inputs"]:
            self.materials[i["material_id"]]["stock_qty"] -= D(i["qty_base"])
        r.update(status="confirmed", materials_cents=costing.cents(mat),
                 confirmed_at=f"2026-10-14T0{self.n % 9}:00")
        r["total_cents"] = r["materials_cents"] + r["labour_cents"] + r["overhead_cents"]
        r["unit_cost"] = (D(r["total_cents"]) / 100 / D(r["good_qty"])).quantize(D("0.000001"))
        return {"total_cents": r["total_cents"]}

    def reverse_run(self, tid, run_id, actor=None):
        r = self.runs[run_id]
        for i in r["inputs"]:
            self.materials[i["material_id"]]["stock_qty"] += D(i["qty_base"])
        r["status"] = "reversed"
        return {"reversed": True}


@pytest.fixture
def store(monkeypatch):
    s = Store()
    for name in ("list_materials", "create_material", "move_material", "active_bom", "save_recipe",
                 "last_runs", "draft_run", "confirm_run", "reverse_run"):
        monkeypatch.setattr(costing, name, getattr(s, name))
    monkeypatch.setattr(costing, "get_run", lambda tid, rid: s.runs.get(rid))
    monkeypatch.setattr(costing, "settings_for", lambda tid: s.settings)
    monkeypatch.setattr(costing, "_vat", lambda tid: (False, True))

    async def list_products(tid, **kw):
        return s.products

    monkeypatch.setattr(service, "list_products", list_products)
    return s


@pytest.mark.asyncio
async def test_set_up_and_cost_a_basin_end_to_end(store):
    prev = mfg.add_material(TID, "Concrete mix", "kg", pack_size=25, pack_unit="kg")
    assert prev["preview"] and not store.materials
    assert mfg.add_material(TID, "Concrete mix", "kg", pack_size=25, pack_unit="kg", confirm=True)["verified"]
    assert mfg.receive(TID, "concrete mix", 2, "bags", None)["status"] == "need_info"      # asks the price
    r = mfg.receive(TID, "concrete mix", 2, "bags", 19000, confirm=True)
    assert "+50 kg booked in" in r["reply_verbatim"] and "R3.80 per kg" in r["reply_verbatim"]
    r = mfg.receive(TID, "concrete mix", 1, "bag", 10000, confirm=True)
    assert "average cost now R3.87 per kg" in r["reply_verbatim"]          # (190 + 100) / 75 kg

    ingredients = [{"material": "concrete mix", "qty": 72, "unit": "kg", "waste_pct": 5},
                   {"material": "pigment", "qty": 600, "unit": "g"},
                   {"material": "sealer", "qty": 1, "unit": "l"}]
    prev = await mfg.set_recipe(TID, "round drop-in basins", ingredients, makes=4, labour_hours=6)
    assert prev["preview"] and prev["new_materials"] == "pigment, sealer — will be added with no price yet"
    saved = await mfg.set_recipe(TID, "round drop-in basins", ingredients, makes=4, labour_hours=6, confirm=True)
    assert "Recipe saved for *Round Drop-In Basin* (version 1)" in saved["reply_verbatim"]
    assert "⚠️ no price yet" in saved["reply_verbatim"]

    blocked = await mfg.log_batch(TID, "round basin", good=3, rejects=1, labour_hours=6, confirm=True)
    assert "no price for: pigment, sealer" in blocked["error"]               # never costed on a guess

    mfg.receive(TID, "pigment", 1, "kg", 22000, confirm=True)
    mfg.receive(TID, "sealer", 5, "l", 48000, confirm=True)
    prev = await mfg.log_batch(TID, "round basin", good=3, rejects=1, labour_hours=6)
    assert prev["preview"] and prev["cost_per_unit"] == "R343.45"
    done = await mfg.log_batch(TID, "round basin", good=3, rejects=1, labour_hours=6, confirm=True)
    assert "Cost *R343.45 each* — materials R520.35, labour R360.00, overhead R150.00" in done["reply_verbatim"]
    assert "Main driver: yield — 1 fewer good than the recipe's 4 (1 rejected)" in done["reply_verbatim"]
    assert store.materials[next(iter(store.materials))]["stock_qty"] == D("-600.0000")

    sheet = await mfg.sheet(TID, "round basin")
    assert "*R257.59 each*" in sheet["reply_verbatim"] and "Last batches: R343.45" in sheet["reply_verbatim"]
    assert "margin *R432.41*" in sheet["reply_verbatim"]                      # R690 − R257.59 (not VAT registered)

    undo = await mfg.undo_last_batch(TID, "round basin", confirm=True)
    assert undo["verified"] and store.materials[next(iter(store.materials))]["stock_qty"] == D("75000.0000")


@pytest.mark.asyncio
async def test_asks_which_product_never_guesses(store):
    res = await mfg.sheet(TID, "oval basin")
    assert res["status"] == "need_info" and "Oval Basin - Sage; Oval Basin - Charcoal" in res["message"]
    assert (await mfg.sheet(TID, "bathtub"))["error"] == "No product called 'bathtub'."


def test_settings_preview_then_save(store, monkeypatch):
    saved = {}
    monkeypatch.setattr(costing, "save_settings", lambda tid, **kw: saved.update(kw) or
                        {"labour_rate_cents": kw["labour_rate_cents"], "overhead_rule": kw["overhead_rule"],
                         "overhead_rate_cents": kw["overhead_rate_cents"]})
    assert mfg.update_settings(TID, labour_rate_cents=6500, overhead_rule="per_run", overhead_rate_cents=40000)["preview"]
    res = mfg.update_settings(TID, labour_rate_cents=6500, overhead_rule="per_run", overhead_rate_cents=40000,
                              confirm=True)
    assert res["reply_verbatim"] == "✅ Costing: labour R65.00 per hour; overhead R400.00 per batch."
    assert "must be one of" in mfg.update_settings(TID, overhead_rule="per_moon")["error"]


def test_costing_tools_only_for_the_manufacturing_module(monkeypatch):
    import core.skills.commerce_admin as ca
    names = lambda: {t["function"]["name"] for t in ca._tools_for(TID)}
    monkeypatch.setattr("vula.api.tenants.enabled_modules", lambda tid: ["products", "orders"])
    assert "log_batch" not in names()
    monkeypatch.setattr("vula.api.tenants.enabled_modules", lambda tid: ["products", "orders", "manufacturing"])
    assert {"cost_sheet", "set_recipe", "log_batch", "receive_material"} <= names()


@pytest.mark.asyncio
async def test_whatsapp_tool_preview_then_confirmed_reply(store):
    import core.skills.commerce_admin as ca
    skill = ca.CommerceAdminSkill()
    prev = await skill._manufacturing("add_material", TID, {"name": "Sealer", "unit": "l"}, "27662545068")
    assert prev["preview"] is True                          # → the Confirm / Cancel buttons
    done = await skill._manufacturing("add_material", TID, {"name": "Sealer", "unit": "l", "confirm": True}, "27662545068")
    assert done["reply_verbatim"].startswith("✅ Added *Sealer* (measured in ml)")
    bad = await skill._manufacturing("receive_material", TID, {"material": "Sealer", "qty": 2, "unit": "kg",
                                                               "total_rands": 100}, None)
    assert bad["error"] == "Sealer is measured in ml, not kg."
