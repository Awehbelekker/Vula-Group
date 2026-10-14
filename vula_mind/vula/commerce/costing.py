"""
vula/commerce/costing.py — what it actually costs to make something, and the margin on it.

Vula Manufacturing Core, phases 2–3 (design: Richard, 14 Oct 2026). Generic for any business that
makes things — switched on with the "manufacturing" module, set up by the business itself
(materials, recipes, batches), never per-tenant code:

    unit cost = (materials + labour + overhead) / good units

  materials  each input at its weighted-average cost on the day (migration 211's material_move
             keeps the average; claimable VAT is never part of it)
  labour     hours on the batch × the business's labour rate
  overhead   rent, power, tooling — by the one rule the business picks: per labour hour, per
             batch, per good unit, or none
  rejects    stay in the cost: they make each good unit dearer, so the true price is visible

All arithmetic is here, in Decimal, and money is rounded to whole cents once. No model does any of
it. The database functions (confirm_production_run) repeat the same sum when a batch is confirmed,
and the caller reads the stored figures back and checks they match this module's preview.
"""
from __future__ import annotations

import logging
from decimal import ROUND_HALF_UP, Decimal
from typing import Any, Dict, List, Optional

log = logging.getLogger(__name__)

D = Decimal
CENT = D("1")
OVERHEAD_RULES = ("none", "per_labour_hour", "per_run", "per_unit")
BASE_UNITS = ("g", "ml", "each", "m", "m2")

# unit as people say it → (base unit, how many base units it is)
UNITS: Dict[str, tuple] = {
    "mg": ("g", D("0.001")), "g": ("g", D(1)), "gram": ("g", D(1)), "grams": ("g", D(1)),
    "kg": ("g", D(1000)), "kgs": ("g", D(1000)), "kilo": ("g", D(1000)), "kilos": ("g", D(1000)),
    "kilogram": ("g", D(1000)), "kilograms": ("g", D(1000)), "t": ("g", D(1000000)),
    "ton": ("g", D(1000000)), "tons": ("g", D(1000000)), "tonne": ("g", D(1000000)),
    "ml": ("ml", D(1)), "millilitre": ("ml", D(1)), "milliliter": ("ml", D(1)),
    "l": ("ml", D(1000)), "lt": ("ml", D(1000)), "litre": ("ml", D(1000)), "litres": ("ml", D(1000)),
    "liter": ("ml", D(1000)), "liters": ("ml", D(1000)),
    "each": ("each", D(1)), "ea": ("each", D(1)), "unit": ("each", D(1)), "units": ("each", D(1)),
    "pc": ("each", D(1)), "pcs": ("each", D(1)), "piece": ("each", D(1)), "pieces": ("each", D(1)),
    "x": ("each", D(1)), "": ("each", D(1)),
    "mm": ("m", D("0.001")), "cm": ("m", D("0.01")), "m": ("m", D(1)), "metre": ("m", D(1)),
    "metres": ("m", D(1)), "meter": ("m", D(1)), "meters": ("m", D(1)),
    "m2": ("m2", D(1)), "m²": ("m2", D(1)), "sqm": ("m2", D(1)), "sq m": ("m2", D(1)),
}
# a purchase pack: "2 bags" of a material whose pack is 25 kg
PACK_WORDS = {"bag", "bags", "pack", "packs", "bottle", "bottles", "tin", "tins", "box", "boxes",
              "roll", "rolls", "pail", "pails", "drum", "drums", "sack", "sacks", "case", "cases",
              "bucket", "buckets", "can", "cans", "tub", "tubs", "carton", "cartons"}


class CostingError(ValueError):
    """Said to the owner as it is — e.g. a unit that doesn't fit the material."""


def dec(value: Any) -> Decimal:
    try:
        return D(str(value))
    except Exception:
        raise CostingError(f"'{value}' isn't a number.")


def cents(value: Decimal) -> int:
    """Rands → whole cents, rounded once (half up)."""
    return int((value * 100).quantize(CENT, rounding=ROUND_HALF_UP))


def rands(c: int) -> str:
    return f"R{c / 100:,.2f}"


def base_unit_for(unit: str) -> str:
    u = (unit or "").strip().lower()
    if u in UNITS:
        return UNITS[u][0]
    raise CostingError(f"I don't know the unit '{unit}' — use g/kg, ml/l, each, m or m².")


def to_base(qty: Any, unit: str, material: Dict[str, Any]) -> Decimal:
    """How many of the material's base units `qty unit` is: 18 kg → 18000 (g); 2 bags of a
    25 kg-bag material → 50000 (g). Refuses a unit that doesn't measure the material."""
    q = dec(qty)
    u = (unit or "").strip().lower()
    base = material.get("base_unit") or "each"
    if u in PACK_WORDS:
        size, pack_unit = material.get("pack_size"), material.get("pack_unit")
        if not size or not pack_unit:
            raise CostingError(f"How much is in one {u.rstrip('s')} of {material.get('name')}? "
                               "Set its pack size first (e.g. 25 kg).")
        return q * to_base(size, pack_unit, material)
    if u not in UNITS:
        raise CostingError(f"I don't know the unit '{unit}' — use g/kg, ml/l, each, m or m².")
    unit_base, factor = UNITS[u]
    if unit_base != base:
        raise CostingError(f"{material.get('name')} is measured in {base}, not {unit}.")
    return q * factor


def show_qty(q: Any, base_unit: str) -> str:
    """75600 g → 75.6 kg; 600 g → 600 g; 1000 ml → 1 l."""
    v = dec(q)
    if base_unit == "g" and abs(v) >= 1000:
        v, base_unit = v / 1000, "kg"
    elif base_unit == "ml" and abs(v) >= 1000:
        v, base_unit = v / 1000, "l"
    text = f"{v.normalize():f}" if v == v.to_integral() else f"{v.quantize(D('0.001')).normalize():f}"
    return f"{text} {base_unit}" if base_unit != "each" else text


def labour_and_overhead(settings: Dict[str, Any], hours: Decimal, good: Decimal) -> tuple:
    """(labour_cents, overhead_cents) for a batch, by the business's own rule."""
    rate = dec(settings.get("labour_rate_cents") or 0)
    labour = int((hours * rate).quantize(CENT, rounding=ROUND_HALF_UP))
    rule = settings.get("overhead_rule") or "none"
    orate = dec(settings.get("overhead_rate_cents") or 0)
    if rule == "per_labour_hour":
        overhead = hours * orate
    elif rule == "per_run":
        overhead = orate
    elif rule == "per_unit":
        overhead = good * orate
    else:
        overhead = D(0)
    return labour, int(overhead.quantize(CENT, rounding=ROUND_HALF_UP))


def batch_cost(bom: Dict[str, Any], lines: List[Dict[str, Any]], materials: Dict[str, Dict[str, Any]],
               settings: Dict[str, Any], *, batches: Any = 1, good: Any = None, rejects: Any = 0,
               labour_hours: Any = None, inputs: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
    """The cost of making `batches` of a recipe at today's material costs.

    good/rejects/labour_hours default to what the recipe expects. `inputs` ({material_id, qty_base})
    overrides what went in when the owner logged actual usage; otherwise it's the recipe × batches,
    waste included. Missing material costs are listed, never guessed (the cost is then marked
    incomplete and a batch can't be confirmed)."""
    b = dec(batches)
    if b <= 0:
        raise CostingError("The number of batches must be more than 0.")
    expected_good = dec(bom.get("batch_yield") or 1) * b
    good_q = dec(good) if good is not None else expected_good
    if good_q <= 0:
        raise CostingError("There must be at least some good units to cost.")
    hours = dec(labour_hours) if labour_hours is not None else dec(bom.get("labour_hours") or 0) * b
    if inputs is None:
        inputs = [{"material_id": str(ln["material_id"]),
                   "qty_base": dec(ln["qty_base"]) * (1 + dec(ln.get("waste_pct") or 0) / 100) * b}
                  for ln in lines]
    # quantities as the database stores them (numeric(14,4)), so this preview and the confirmed
    # batch sum exactly the same numbers
    inputs = [{"material_id": str(i["material_id"]),
               "qty_base": dec(i["qty_base"]).quantize(D("0.0001"), rounding=ROUND_HALF_UP)} for i in inputs]
    out_lines, materials_value, missing = [], D(0), []
    for it in inputs:
        m = materials.get(str(it["material_id"])) or {}
        q = dec(it["qty_base"])
        cost = m.get("unit_cost")
        value = q * dec(cost) if cost is not None else None
        if value is None:
            missing.append(m.get("name") or str(it["material_id"]))
        else:
            materials_value += value
        out_lines.append({"material_id": str(it["material_id"]), "name": m.get("name"),
                          "qty_base": q, "base_unit": m.get("base_unit") or "each",
                          "unit_cost": dec(cost) if cost is not None else None,
                          "cost_cents": cents(value) if value is not None else None})
    materials_cents = cents(materials_value)
    labour_cents, overhead_cents = labour_and_overhead(settings, hours, good_q)
    total = materials_cents + labour_cents + overhead_cents
    unit_cost = (D(total) / 100 / good_q).quantize(D("0.000001"), rounding=ROUND_HALF_UP)
    return {"batches": b, "good": good_q, "rejects": dec(rejects or 0), "expected_good": expected_good,
            "labour_hours": hours, "expected_hours": dec(bom.get("labour_hours") or 0) * b,
            "lines": out_lines, "inputs": [{"material_id": i["material_id"], "qty_base": dec(i["qty_base"])}
                                           for i in inputs],
            "materials_cents": materials_cents, "labour_cents": labour_cents,
            "overhead_cents": overhead_cents, "total_cents": total,
            "unit_cost": unit_cost, "unit_cost_cents": cents(unit_cost),
            "missing_costs": missing, "complete": not missing}


def main_driver(now: Dict[str, Any], expected: Dict[str, Any],
                last: Optional[Dict[str, Any]] = None) -> Optional[str]:
    """Why this batch's cost per unit differs from what the recipe expects (or the last batch):
    yield (rejects), labour time, or material prices — whichever moved it most, in plain words."""
    effects = []
    if now["good"] != now["expected_good"] and now["good"] > 0:
        per_unit_at_expected = D(now["total_cents"]) / now["expected_good"]
        per_unit_now = D(now["total_cents"]) / now["good"]
        diff = per_unit_now - per_unit_at_expected
        short = now["expected_good"] - now["good"]
        what = (f"{_n(short)} fewer good than the recipe's {_n(now['expected_good'])}" if short > 0
                else f"{_n(-short)} more good than the recipe's {_n(now['expected_good'])}")
        effects.append((abs(diff), f"yield — {what}" + (f" ({_n(now['rejects'])} rejected)" if now['rejects'] else "")))
    if now["labour_hours"] != now["expected_hours"]:
        diff = D(now["labour_cents"] - expected["labour_cents"]) / now["good"]
        effects.append((abs(diff), f"labour — {_n(now['labour_hours'])} h against {_n(now['expected_hours'])} h expected"))
    if last and last.get("materials_cents") is not None and last.get("good_qty"):
        diff = (D(now["materials_cents"]) / now["good"]) - (D(last["materials_cents"]) / dec(last["good_qty"]))
        if diff:
            effects.append((abs(diff), "material prices" + (" up" if diff > 0 else " down")
                            + " since the last batch"))
    effects = [e for e in effects if e[0] >= D("0.5")]          # ignore under half a cent a unit
    return max(effects, key=lambda e: e[0])[1] if effects else None


def _n(v: Any) -> str:
    v = dec(v)
    return f"{v.normalize():f}" if v == v.to_integral() else f"{v.quantize(D('0.01')).normalize():f}"


def margin(unit_cost_cents: int, price_cents: Optional[int], *, vat_registered: bool,
           prices_include_vat: bool = True) -> Dict[str, Any]:
    """Selling price (excl. VAT when the business is VAT registered and its prices include it),
    margin per unit in cents and as a percentage of that price."""
    if not price_cents:
        return {"price_cents": None, "margin_cents": None, "margin_pct": None}
    price_ex = D(price_cents)
    if vat_registered and prices_include_vat:
        price_ex = (price_ex * 100 / 115).quantize(CENT, rounding=ROUND_HALF_UP)
    m = int(price_ex) - unit_cost_cents
    pct = (D(m) * 100 / price_ex).quantize(D("0.1"), rounding=ROUND_HALF_UP) if price_ex else None
    return {"price_cents": int(price_ex), "margin_cents": m, "margin_pct": pct}


def price_for_margin(unit_cost_cents: int, target_pct: Any, *, vat_registered: bool,
                     prices_include_vat: bool = True) -> int:
    """The selling price (as the shop shows it) that gives `target_pct` margin on true cost."""
    t = dec(target_pct)
    if t >= 100:
        raise CostingError("A margin must be under 100%.")
    ex = D(unit_cost_cents) / (1 - t / 100)
    if vat_registered and prices_include_vat:
        ex = ex * D("1.15")
    return int(ex.quantize(CENT, rounding=ROUND_HALF_UP))


# ── Data access (Supabase) ────────────────────────────────────────────────────────────────

def _db():
    from vula.commerce import service
    return service._client()


def settings_for(tenant_id: str) -> Dict[str, Any]:
    try:
        rows = (_db().table("commerce_costing_settings").select("*").eq("tenant_id", tenant_id)
                .limit(1).execute().data or [])
    except Exception as exc:
        log.debug("costing settings unavailable (run migration 211?): %s", exc)
        rows = []
    return rows[0] if rows else {"tenant_id": tenant_id, "labour_rate_cents": 0, "overhead_rule": "none",
                                 "overhead_rate_cents": 0}


def save_settings(tenant_id: str, *, labour_rate_cents: Optional[int] = None,
                  overhead_rule: Optional[str] = None, overhead_rate_cents: Optional[int] = None,
                  actor: Optional[str] = None) -> Dict[str, Any]:
    cur = settings_for(tenant_id)
    row = {"tenant_id": tenant_id,
           "labour_rate_cents": int(labour_rate_cents if labour_rate_cents is not None else cur.get("labour_rate_cents") or 0),
           "overhead_rule": overhead_rule or cur.get("overhead_rule") or "none",
           "overhead_rate_cents": int(overhead_rate_cents if overhead_rate_cents is not None else cur.get("overhead_rate_cents") or 0),
           "updated_by": actor}
    if row["overhead_rule"] not in OVERHEAD_RULES:
        raise CostingError(f"The overhead rule must be one of {', '.join(OVERHEAD_RULES)}.")
    if row["labour_rate_cents"] < 0 or row["overhead_rate_cents"] < 0:
        raise CostingError("Rates can't be negative.")
    _db().table("commerce_costing_settings").upsert(row).execute()
    return settings_for(tenant_id)


def list_materials(tenant_id: str) -> List[Dict[str, Any]]:
    return (_db().table("commerce_materials").select("*").eq("tenant_id", tenant_id).eq("active", True)
            .order("name").execute().data or [])


def find_material(tenant_id: str, name: str) -> Optional[Dict[str, Any]]:
    """Exact (any case) first, then the one material whose name contains the words — never a
    guess between several."""
    want = " ".join((name or "").lower().split())
    if not want:
        return None
    mats = list_materials(tenant_id)
    exact = [m for m in mats if " ".join(m["name"].lower().split()) == want]
    if exact:
        return exact[0]
    words = want.split()
    hits = [m for m in mats if all(w in m["name"].lower() for w in words)]
    return hits[0] if len(hits) == 1 else None


def create_material(tenant_id: str, name: str, base_unit: str, *, pack_size: Any = None,
                    pack_unit: Optional[str] = None, supplier: Optional[str] = None) -> Dict[str, Any]:
    name = " ".join((name or "").split())
    if not name:
        raise CostingError("What's the material called?")
    base = base_unit_for(base_unit)
    if pack_size is not None and pack_unit:
        if base_unit_for(pack_unit) != base:
            raise CostingError(f"A pack of {name} can't be measured in {pack_unit} if it's counted in {base}.")
    if find_material(tenant_id, name) and " ".join(name.lower().split()) == \
            " ".join(find_material(tenant_id, name)["name"].lower().split()):
        raise CostingError(f"There's already a material called {name}.")
    row = {"tenant_id": tenant_id, "name": name, "base_unit": base,
           "pack_size": float(dec(pack_size)) if pack_size is not None else None,
           "pack_unit": (pack_unit or None), "supplier": supplier}
    res = _db().table("commerce_materials").insert(row).execute()
    return (res.data or [row])[0]


def move_material(tenant_id: str, material: Dict[str, Any], *, qty: Any = None, unit: Optional[str] = None,
                  set_qty: Any = None, total_cents: Optional[int] = None, reason: str = "receive",
                  ref_type: Optional[str] = None, ref_id: Optional[str] = None, actor: Optional[str] = None,
                  note: Optional[str] = None) -> Dict[str, Any]:
    """Receive (with what it cost, excl. claimable VAT), adjust or count a material."""
    base_qty = to_base(qty, unit or material.get("base_unit"), material) if qty is not None else None
    set_base = to_base(set_qty, unit or material.get("base_unit"), material) if set_qty is not None else None
    unit_cost = None
    if reason in ("receive", "opening") and total_cents is not None:
        if not base_qty or base_qty <= 0:
            raise CostingError("How much arrived?")
        unit_cost = (D(int(total_cents)) / 100 / base_qty).quantize(D("0.000001"), rounding=ROUND_HALF_UP)
    res = _db().rpc("material_move", {
        "p_tenant_id": tenant_id, "p_material_id": material["id"],
        "p_delta": float(base_qty) if base_qty is not None else None,
        "p_set": float(set_base) if set_base is not None else None,
        "p_unit_cost": float(unit_cost) if unit_cost is not None else None,
        "p_reason": reason, "p_ref_type": ref_type, "p_ref_id": ref_id, "p_actor": actor, "p_note": note,
    }).execute().data
    if isinstance(res, list):
        res = res[0] if res else None
    if not res:
        raise CostingError("That material isn't this business's.")
    return {"qty_after": dec(res["qty_after"]), "unit_cost_after": (dec(res["unit_cost_after"])
                                                                   if res.get("unit_cost_after") is not None else None),
            "base_qty": base_qty, "unit_cost": unit_cost}


def active_bom(tenant_id: str, *, product_id: Optional[str] = None,
               material_id: Optional[str] = None) -> Optional[Dict[str, Any]]:
    q = _db().table("commerce_boms").select("*, commerce_bom_lines(*)").eq("tenant_id", tenant_id) \
        .eq("status", "active")
    q = q.eq("product_id", product_id) if product_id else q.eq("material_id", material_id)
    rows = q.limit(1).execute().data or []
    if not rows:
        return None
    bom = rows[0]
    bom["lines"] = sorted(bom.pop("commerce_bom_lines", None) or [], key=lambda ln: ln.get("position") or 0)
    return bom


def get_bom(tenant_id: str, bom_id: str) -> Optional[Dict[str, Any]]:
    rows = (_db().table("commerce_boms").select("*, commerce_bom_lines(*)").eq("tenant_id", tenant_id)
            .eq("id", bom_id).limit(1).execute().data or [])
    if not rows:
        return None
    bom = rows[0]
    bom["lines"] = sorted(bom.pop("commerce_bom_lines", None) or [], key=lambda ln: ln.get("position") or 0)
    return bom


def save_recipe(tenant_id: str, *, product_id: Optional[str] = None, material_id: Optional[str] = None,
                lines: List[Dict[str, Any]], batch_yield: Any = 1, labour_hours: Any = 0,
                actor: Optional[str] = None, notes: Optional[str] = None) -> str:
    """lines: [{material (dict), qty, unit, waste_pct}] → a new recipe version; returns its id."""
    if not lines:
        raise CostingError("A recipe needs at least one material.")
    if dec(batch_yield) <= 0:
        raise CostingError("How many does one batch make?")
    payload = []
    for ln in lines:
        m = ln["material"]
        waste = dec(ln.get("waste_pct") or 0)
        if waste < 0 or waste >= 100:
            raise CostingError("Waste must be between 0 and 100%.")
        payload.append({"material_id": m["id"], "qty": float(dec(ln["qty"])), "unit": ln["unit"],
                        "qty_base": float(to_base(ln["qty"], ln["unit"], m)), "waste_pct": float(waste)})
    res = _db().rpc("save_bom_version", {
        "p_tenant_id": tenant_id, "p_product_id": product_id, "p_material_id": material_id,
        "p_batch_yield": float(dec(batch_yield)), "p_labour_hours": float(dec(labour_hours or 0)),
        "p_lines": payload, "p_actor": actor, "p_notes": notes}).execute().data
    if isinstance(res, list):
        res = res[0] if res else None
    if not res:
        raise CostingError("The recipe couldn't be saved.")
    return str(res)


def materials_by_id(tenant_id: str) -> Dict[str, Dict[str, Any]]:
    return {str(m["id"]): m for m in list_materials(tenant_id)}


def last_runs(tenant_id: str, *, product_id: Optional[str] = None, material_id: Optional[str] = None,
              limit: int = 10) -> List[Dict[str, Any]]:
    q = (_db().table("commerce_production_runs").select("*").eq("tenant_id", tenant_id)
         .eq("status", "confirmed"))
    q = q.eq("product_id", product_id) if product_id else q.eq("material_id", material_id)
    return q.order("confirmed_at", desc=True).limit(limit).execute().data or []


def draft_run(tenant_id: str, bom: Dict[str, Any], preview: Dict[str, Any], *,
              actor: Optional[str] = None, note: Optional[str] = None) -> Dict[str, Any]:
    row = {"tenant_id": tenant_id, "bom_id": bom["id"], "product_id": bom.get("product_id"),
           "material_id": bom.get("material_id"), "batches": float(preview["batches"]),
           "good_qty": float(preview["good"]), "reject_qty": float(preview["rejects"]),
           "labour_hours": float(preview["labour_hours"]),
           "inputs": [{"material_id": i["material_id"], "qty_base": float(i["qty_base"])} for i in preview["inputs"]],
           "labour_cents": preview["labour_cents"], "overhead_cents": preview["overhead_cents"],
           "actor": actor, "note": note}
    res = _db().table("commerce_production_runs").insert(row).execute()
    return (res.data or [row])[0]


def confirm_run(tenant_id: str, run_id: str, actor: Optional[str] = None) -> Dict[str, Any]:
    res = _db().rpc("confirm_production_run", {"p_tenant_id": tenant_id, "p_run_id": run_id,
                                               "p_actor": actor}).execute().data
    if isinstance(res, list):
        res = res[0] if res else None
    return res or {"error": "not_found"}


def reverse_run(tenant_id: str, run_id: str, actor: Optional[str] = None) -> Dict[str, Any]:
    res = _db().rpc("reverse_production_run", {"p_tenant_id": tenant_id, "p_run_id": run_id,
                                               "p_actor": actor}).execute().data
    if isinstance(res, list):
        res = res[0] if res else None
    return res or {"error": "not_found"}


def get_run(tenant_id: str, run_id: str) -> Optional[Dict[str, Any]]:
    rows = (_db().table("commerce_production_runs").select("*").eq("tenant_id", tenant_id)
            .eq("id", run_id).limit(1).execute().data or [])
    return rows[0] if rows else None


def _vat(tenant_id: str) -> tuple:
    try:
        from vula.commerce import accounting
        registered = bool(accounting.is_vat_registered(tenant_id))
    except Exception:
        registered = False
    try:
        rows = (_db().table("commerce_invoice_settings").select("prices_include_vat")
                .eq("tenant_id", tenant_id).limit(1).execute().data or [])
        incl = rows[0].get("prices_include_vat") if rows else None
    except Exception:
        incl = None
    return registered, (True if incl is None else bool(incl))


def cost_sheet(tenant_id: str, product: Dict[str, Any]) -> Dict[str, Any]:
    """One product's cost sheet: the recipe at today's costs (one batch), cost per unit, selling
    price, margin, and the last batches' actual cost per unit."""
    bom = active_bom(tenant_id, product_id=product["id"])
    if not bom:
        return {"product": product.get("name"), "error": "no_recipe"}
    mats = materials_by_id(tenant_id)
    settings = settings_for(tenant_id)
    expected = batch_cost(bom, bom["lines"], mats, settings)
    registered, incl = _vat(tenant_id)
    m = margin(expected["unit_cost_cents"], product.get("price_cents"), vat_registered=registered,
               prices_include_vat=incl)
    runs = last_runs(tenant_id, product_id=product["id"], limit=6)
    return {"product": product.get("name"), "product_id": product["id"], "recipe_version": bom["version"],
            "batch_yield": dec(bom["batch_yield"]), "labour_hours": dec(bom["labour_hours"]),
            "expected": expected, "margin": m, "vat_registered": registered,
            "runs": [{"id": r["id"], "at": r.get("confirmed_at"), "good": dec(r["good_qty"]),
                      "rejects": dec(r.get("reject_qty") or 0),
                      "unit_cost_cents": cents(dec(r["unit_cost"])) if r.get("unit_cost") is not None else None}
                     for r in runs]}


def cost_sheet_text(sheet: Dict[str, Any]) -> str:
    """The WhatsApp cost sheet — every figure from the sheet, nothing worked out here."""
    if sheet.get("error") == "no_recipe":
        return (f"{sheet['product']} has no recipe yet — tell me what goes into one batch, e.g. "
                f"\"recipe for {sheet['product']}: 18 kg concrete mix, 150 g pigment, makes 1\".")
    e = sheet["expected"]
    lines = [f"🧾 *{sheet['product']}* — cost per unit (recipe v{sheet['recipe_version']}, today's prices)"]
    for ln in e["lines"]:
        lines.append(f"• {ln['name']}: {show_qty(ln['qty_base'], ln['base_unit'])} — "
                     + (rands(ln["cost_cents"]) if ln["cost_cents"] is not None else "⚠️ no price yet"))
    lines.append(f"Materials {rands(e['materials_cents'])} · labour {rands(e['labour_cents'])}"
                 f" · overhead {rands(e['overhead_cents'])}")
    lines.append(f"One batch makes {_n(e['good'])} → *{rands(e['unit_cost_cents'])} each*")
    m = sheet["margin"]
    if m["price_cents"]:
        vat = " (excl. VAT)" if sheet.get("vat_registered") else ""
        lines.append(f"Selling at {rands(m['price_cents'])}{vat}: margin *{rands(m['margin_cents'])}* "
                     f"({m['margin_pct']}%)")
    if e["missing_costs"]:
        lines.append("⚠️ Not costed yet — no price for: " + ", ".join(e["missing_costs"])
                     + ". The cost above leaves them out.")
    if sheet["runs"]:
        recent = [r for r in sheet["runs"] if r["unit_cost_cents"] is not None][:3]
        if recent:
            lines.append("Last batches: " + ", ".join(f"{rands(r['unit_cost_cents'])}" for r in recent))
    return "\n".join(lines)
