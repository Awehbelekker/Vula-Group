"""
vula/commerce/trade_checklists.py — what must be considered when costing work in each trade.

Trade costing, phase 2 (14 Nov 2026; Ian: "Vula needs to know the trades to be able to cost
properly … not miss cost like labour, bricks, cement and sand and plastering"). One checklist per
ASAQS trade (vula/commerce/boq_template.py TRADES): the components a build-up for work in that
trade must account for. STRUCTURE ONLY — no quantities and no prices. Quantities come from a
researched, cited, person-confirmed build-up (vula/commerce/buildups.py); prices from the
business's own records and checked market prices.

A component is (key, label, kind, usual unit). kind is material | labour | plant. A component a
particular item doesn't need (a fair-face wall has no plaster) is simply absent from its build-up;
the estimate lists every checklist component that is neither in the build-up nor excluded, so
nothing is left out silently. Reviewed by the business's QS before relying on it.
"""
from __future__ import annotations

from typing import Dict, List, Optional, Tuple

Component = Tuple[str, str, str, str]          # key, label, kind, usual unit

_LABOUR = ("labour", "Labour", "labour", "hour")
_SUPERVISION = ("supervision", "Supervision / foreman", "labour", "hour")
_WASTE_NOTE = "Waste is a percentage on each material line, not a component."

CHECKLISTS: Dict[str, List[Component]] = {
    "Preliminaries": [
        ("site_establishment", "Site establishment and removal", "plant", "item"),
        ("site_office", "Site office / storage", "plant", "week"),
        ("toilets", "Site toilets", "plant", "week"),
        ("scaffolding", "Scaffolding", "plant", "m2"),
        ("insurance", "Contract works insurance", "plant", "item"),
        ("health_safety", "Health and safety file, PPE", "plant", "item"),
        _SUPERVISION,
    ],
    "Alterations": [
        ("demolition_labour", "Demolition / hacking out", "labour", "hour"),
        ("rubble_removal", "Rubble removal (skip / trips)", "plant", "load"),
        ("propping", "Temporary propping", "plant", "item"),
        ("making_good", "Making good to existing", "labour", "hour"),
    ],
    "Earthworks": [
        ("excavation", "Excavation (machine or hand)", "plant", "m3"),
        ("backfill", "Backfill and compaction", "labour", "m3"),
        ("compactor", "Compactor hire", "plant", "day"),
        ("cart_away", "Carting away surplus soil", "plant", "m3"),
        ("anti_termite", "Soil poisoning", "material", "m2"),
        _LABOUR,
    ],
    "Concrete, Formwork and Reinforcement": [
        ("cement", "Cement", "material", "bag"),
        ("sand", "Concrete sand", "material", "m3"),
        ("stone", "Stone / aggregate", "material", "m3"),
        ("ready_mix", "Ready-mix concrete (instead of site mix)", "material", "m3"),
        ("reinforcement", "Reinforcement (bars / mesh)", "material", "kg"),
        ("binding_wire", "Binding wire, spacers, chairs", "material", "kg"),
        ("formwork", "Formwork / shuttering", "material", "m2"),
        ("dpm", "Damp-proof membrane under slabs", "material", "m2"),
        ("mixer", "Mixer / vibrator hire", "plant", "day"),
        ("curing", "Curing", "material", "m2"),
        _LABOUR,
    ],
    "Precast Concrete": [
        ("precast_units", "Precast units (lintels, slabs, kerbs)", "material", "each"),
        ("bedding_mortar", "Bedding mortar", "material", "m3"),
        ("lifting", "Lifting / crane", "plant", "day"),
        _LABOUR,
    ],
    "Masonry": [
        ("units", "Bricks or blocks", "material", "each"),
        ("mortar_cement", "Mortar cement", "material", "bag"),
        ("mortar_sand", "Mortar (building) sand", "material", "m3"),
        ("lime_plasticiser", "Lime or plasticiser", "material", "bag"),
        ("brickforce", "Brickforce", "material", "m"),
        ("wall_ties", "Wall ties", "material", "each"),
        ("dpc", "Damp-proof course", "material", "m"),
        ("lintels", "Lintels over openings", "material", "each"),
        _LABOUR,
    ],
    "Waterproofing": [
        ("membrane", "Waterproofing membrane / torch-on", "material", "m2"),
        ("primer", "Primer", "material", "l"),
        ("flashings", "Flashings", "material", "m"),
        ("sealant", "Sealant", "material", "each"),
        _LABOUR,
    ],
    "Roof Coverings, etc.": [
        ("sheeting_or_tiles", "Roof sheeting or tiles", "material", "m2"),
        ("fixings", "Fixings / screws / clips", "material", "each"),
        ("ridges_flashings", "Ridges, flashings, valleys", "material", "m"),
        ("underlay", "Underlay / insulation", "material", "m2"),
        ("gutters", "Gutters and downpipes", "material", "m"),
        _LABOUR,
    ],
    "Carpentry and Joinery": [
        ("timber", "Structural timber (trusses, purlins, battens)", "material", "m"),
        ("doors_frames", "Doors and frames", "material", "each"),
        ("joinery", "Built-in joinery (cupboards, skirting)", "material", "m"),
        ("fixings", "Fixings, brackets, hangers", "material", "each"),
        ("treatment", "Timber treatment", "material", "l"),
        _LABOUR,
    ],
    "Ceilings, Partitions and Access Flooring": [
        ("boards", "Ceiling / partition boards", "material", "m2"),
        ("framing", "Brandering or metal framing", "material", "m"),
        ("cornices", "Cornices", "material", "m"),
        ("insulation", "Insulation", "material", "m2"),
        ("jointing", "Jointing tape and compound", "material", "m2"),
        ("fixings", "Fixings", "material", "each"),
        _LABOUR,
    ],
    "Floor Coverings, Wall Linings, etc.": [
        ("covering", "Floor covering (vinyl, carpet, laminate)", "material", "m2"),
        ("underlay", "Underlay / levelling screed", "material", "m2"),
        ("adhesive", "Adhesive", "material", "kg"),
        ("trims", "Trims and skirting", "material", "m"),
        _LABOUR,
    ],
    "Ironmongery": [
        ("hardware", "Locks, handles, hinges, closers", "material", "each"),
        _LABOUR,
    ],
    "Structural Steelwork": [
        ("steel_sections", "Steel sections", "material", "kg"),
        ("plates_bolts", "Plates, bolts, welding", "material", "each"),
        ("corrosion_protection", "Corrosion protection / painting", "material", "m2"),
        ("lifting", "Crane / lifting", "plant", "day"),
        _LABOUR,
    ],
    "Metalwork": [
        ("metal_items", "Balustrades, gates, burglar bars", "material", "m"),
        ("fixings", "Fixings / anchors", "material", "each"),
        ("finish", "Galvanising or paint", "material", "m2"),
        _LABOUR,
    ],
    "Plastering": [
        ("cement", "Plaster cement", "material", "bag"),
        ("plaster_sand", "Plaster sand", "material", "m3"),
        ("lime_additive", "Lime or plaster additive", "material", "bag"),
        ("bonding_liquid", "Bonding liquid / key (on smooth surfaces)", "material", "l"),
        ("skim", "Skim coat (if specified)", "material", "bag"),
        ("beads", "Corner and stop beads", "material", "m"),
        ("scaffolding", "Scaffolding / trestles", "plant", "m2"),
        _LABOUR,
    ],
    "Tiling": [
        ("tiles", "Tiles", "material", "m2"),
        ("adhesive", "Tile adhesive", "material", "bag"),
        ("grout", "Grout", "material", "kg"),
        ("spacers_trims", "Spacers and trims", "material", "m"),
        ("waterproofing", "Waterproofing in wet areas", "material", "m2"),
        ("screed", "Screed / levelling", "material", "m2"),
        _LABOUR,
    ],
    "Plumbing and Drainage": [
        ("pipes", "Pipes (supply and waste)", "material", "m"),
        ("fittings", "Fittings", "material", "each"),
        ("sanitaryware", "Sanitaryware", "material", "each"),
        ("geyser", "Geyser / hot water", "material", "each"),
        ("taps_valves", "Taps and valves", "material", "each"),
        ("trenching", "Trenching / backfill for drains", "labour", "m"),
        _LABOUR,
    ],
    "Electrical Installation": [
        ("cable", "Cable / wire", "material", "m"),
        ("conduit", "Conduit and boxes", "material", "m"),
        ("db_breakers", "Distribution board and breakers", "material", "each"),
        ("points", "Plugs, switches, light points", "material", "each"),
        ("fittings", "Light fittings", "material", "each"),
        ("coc", "Certificate of compliance", "labour", "item"),
        _LABOUR,
    ],
    "Glazing": [
        ("glass", "Glass", "material", "m2"),
        ("putty_beads", "Putty / glazing beads / sealant", "material", "m"),
        ("frames", "Window frames (if supplied by glazier)", "material", "each"),
        _LABOUR,
    ],
    "Paintwork": [
        ("primer", "Primer / sealer", "material", "l"),
        ("paint", "Paint (finish coats)", "material", "l"),
        ("filler", "Filler and preparation", "material", "kg"),
        ("sundries", "Brushes, rollers, sandpaper, masking", "material", "item"),
        ("scaffolding", "Scaffolding / ladders", "plant", "m2"),
        _LABOUR,
    ],
    "Paperhanging": [
        ("wallpaper", "Wallpaper", "material", "roll"),
        ("paste", "Paste / primer", "material", "l"),
        _LABOUR,
    ],
    "External Works": [
        ("paving", "Paving", "material", "m2"),
        ("bedding_sand", "Bedding sand", "material", "m3"),
        ("kerbs_edging", "Kerbs / edging", "material", "m"),
        ("base_course", "Base course / compaction", "material", "m3"),
        ("fencing", "Fencing / walls", "material", "m"),
        ("landscaping", "Soil, plants, irrigation", "material", "m2"),
        _LABOUR,
    ],
    "Provisional Sums and Prime Cost Sums": [
        ("pc_items", "Prime cost items (client selections)", "material", "item"),
        ("provisional", "Provisional sums", "material", "item"),
        ("contingency", "Contingency", "material", "item"),
    ],
}


def trades() -> List[str]:
    return list(CHECKLISTS)


def checklist_for(trade: Optional[str]) -> List[Dict[str, str]]:
    return [{"key": k, "label": label, "kind": kind, "unit": unit}
            for k, label, kind, unit in CHECKLISTS.get(trade or "", [])]


def trade_of_item(item: str) -> Optional[str]:
    """The trade an item of work belongs to, by the BOQ classifier (boq_template.trade_of)."""
    try:
        from vula.commerce.boq_template import trade_of
        t = trade_of(item or "")
    except Exception:  # noqa: BLE001
        return None
    return t if t in CHECKLISTS else None
