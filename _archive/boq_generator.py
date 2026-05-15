"""
vula/takeoff/boq_generator.py

Vula BOQ Generator

Takes an ExtractedProject (from PlanReader) and generates
a complete, priced Bill of Quantities covering all trades:

    Demolition → Structure → Wet Works → Drywall & Ceilings
    → Flooring → Joinery → Glazing → Tiling → Plumbing
    → Electrical → HVAC → Fire → Painting → External

Each line item:
    - Derived from the extracted geometry (rooms, walls, doors)
    - Priced from the live rates database (AECOM + scraped)
    - Tagged with confidence score
    - Flagged if specialist quote is required
    - Ready to feed into OrderManager

Usage:
    project = await PlanReader("digg_001").read(Path("plans.pdf"))
    boq = await BOQGenerator(project, tenant_id="digg_001").generate()
    print(f"Total: {boq.grand_total}")
    print(f"Items: {len(boq.items)}")
"""

from __future__ import annotations

import json
import logging
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

from vula.takeoff.plan_reader import ExtractedProject, Room, DoorWindow, MEPSchedule

logger = logging.getLogger(__name__)

DB_PATH = Path.home() / ".vula" / "construction_rates.db"

# ─── BOQ data structures ─────────────────────────────────────────────────────

@dataclass
class BOQItem:
    id: str
    trade: str
    description: str
    unit: str
    qty: float
    rate_low: float
    rate_high: float
    rate_used: float        # Mid unless overridden
    line_total: float       # rate_used × qty
    source: str
    confidence: float       # 0–100
    notes: str = ""
    requires_specialist: bool = False
    supplier_key: str = ""  # Links to OrderManager


@dataclass
class BOQ:
    project_name: str
    tenant_id: str
    items: List[BOQItem] = field(default_factory=list)
    markup_pct: float = 15.0

    @property
    def net_total(self) -> float:
        return sum(i.line_total for i in self.items)

    @property
    def markup_amount(self) -> float:
        return self.net_total * self.markup_pct / 100

    @property
    def grand_total(self) -> float:
        return self.net_total + self.markup_amount

    @property
    def by_trade(self) -> Dict[str, List[BOQItem]]:
        result = {}
        for item in self.items:
            result.setdefault(item.trade, []).append(item)
        return result

    @property
    def specialist_items(self) -> List[BOQItem]:
        return [i for i in self.items if i.requires_specialist]

    def to_dict(self) -> dict:
        return {
            "project_name": self.project_name,
            "net_total": self.net_total,
            "markup_pct": self.markup_pct,
            "markup_amount": self.markup_amount,
            "grand_total": self.grand_total,
            "items": [
                {
                    "id": i.id,
                    "trade": i.trade,
                    "description": i.description,
                    "unit": i.unit,
                    "qty": round(i.qty, 2),
                    "rate_used": round(i.rate_used, 2),
                    "line_total": round(i.line_total, 2),
                    "confidence": i.confidence,
                    "notes": i.notes,
                    "requires_specialist": i.requires_specialist,
                }
                for i in self.items
            ],
        }


# ─── Rate lookup ──────────────────────────────────────────────────────────────

class RateLookup:
    """Query the live rates database built by the weekly scraper."""

    # Fallback AECOM 2025/26 rates when DB not yet populated
    FALLBACK: Dict[str, tuple] = {
        # (low, high, unit, source)
        "demolition_light":      (120,   280,  "m²",     "AECOM 2025/26"),
        "demolition_structural": (380,   750,  "m²",     "AECOM 2025/26"),
        "concrete_30mpa":        (2100,  2600, "m³",     "AfriSam 2025"),
        "structural_steel":      (38000, 55000,"tonne",  "AECOM 2025/26"),
        "brickwork_face":        (680,   950,  "m²",     "AECOM 2025/26"),
        "brickwork_plaster":     (520,   750,  "m²",     "AECOM 2025/26"),
        "waterproofing":         (180,   380,  "m²",     "Sika SA 2025"),
        "drywall_single":        (420,   680,  "m²",     "Gyproc 2025"),
        "drywall_double":        (650,   980,  "m²",     "AECOM 2025/26"),
        "ceiling_standard":      (380,   580,  "m²",     "AECOM 2025/26"),
        "ceiling_acoustic":      (680,   1200, "m²",     "AECOM 2025/26"),
        "screed_50mm":           (95,    160,  "m²",     "AECOM 2025/26"),
        "lvt_vinyl":             (280,   520,  "m²",     "Polyflor 2025"),
        "carpet_commercial":     (220,   480,  "m²",     "Belgotex 2025"),
        "epoxy_coating":         (180,   380,  "m²",     "Sika SA 2025"),
        "tiles_porcelain_floor": (380,   650,  "m²",     "Italtile 2025"),
        "tiles_ceramic_wall":    (280,   480,  "m²",     "Builders 2025"),
        "tile_adhesive_grout":   (85,    140,  "m²",     "Bostik 2025"),
        "door_hollow_core":      (2800,  4500, "no",     "AECOM 2025/26"),
        "door_solid":            (3500,  6500, "no",     "AECOM 2025/26"),
        "door_fire_60":          (12000, 22000,"no",     "AECOM 2025/26"),
        "door_fire_120":         (22000, 38000,"no",     "AECOM 2025/26"),
        "door_acoustic":         (28000, 55000,"no",     "AECOM 2025/26"),
        "glass_partition":       (1800,  3500, "m²",     "AECOM 2025/26"),
        "window_alu_dg":         (2200,  4000, "m²",     "Aluminium SA 2025"),
        "kitchen_unit":          (4500,  9000, "m run",  "AECOM 2025/26"),
        "plumbing_bathroom_full":(35000, 85000,"no",     "AECOM 2025/26"),
        "plumbing_drain_point":  (3500,  6500, "no",     "AECOM 2025/26"),
        "electrical_db":         (8500,  22000,"no",     "AECOM 2025/26"),
        "electrical_fitout":     (450,   900,  "m²",     "AECOM 2025/26"),
        "electrical_emerg_light":(800,   1500, "no",     "AECOM 2025/26"),
        "data_point_cat6a":      (1800,  3200, "point",  "AECOM 2025/26"),
        "hvac_vrf":              (5500,  9000, "kW",     "ExtraAir 2025"),
        "hvac_split":            (3800,  6500, "kW",     "AECOM 2025/26"),
        "fire_sprinkler":        (380,   650,  "m²",     "AECOM 2025/26"),
        "fire_detection":        (95,    180,  "m²",     "AECOM 2025/26"),
        "fire_extinguisher":     (800,   1800, "no",     "AECOM 2025/26"),
        "paint_interior":        (65,    130,  "m²",     "Plascon/Dulux 2025"),
        "paving_brick":          (280,   520,  "m²",     "AECOM 2025/26"),
    }

    def __init__(self, db_path: Path = DB_PATH):
        self.db_path = db_path

    def get(self, key: str) -> tuple:
        """Returns (low, high, unit, source) — DB first, fallback second."""
        if self.db_path.exists():
            try:
                conn = sqlite3.connect(self.db_path)
                row = conn.execute(
                    "SELECT low, high, unit, source FROM material_rates WHERE key LIKE ? LIMIT 1",
                    (f"%{key}%",)
                ).fetchone()
                conn.close()
                if row:
                    return float(row[0]), float(row[1]), row[2], row[3]
            except Exception:
                pass

        return self.FALLBACK.get(key, (0, 0, "item", "Estimate"))


# ─── BOQ Generator ────────────────────────────────────────────────────────────

class BOQGenerator:
    """
    Generates a complete BOQ from an ExtractedProject.
    
    Rules:
    - Quantities derived from geometry (room areas × finish coverage factors)
    - Doors/windows from schedule
    - MEP from fixture schedules + floor area benchmarks
    - Every item has confidence score — low confidence flagged for review
    - Items with confidence <70% marked requires_specialist=True
    """

    def __init__(self, project: ExtractedProject, tenant_id: str = "default", markup: float = 15.0):
        self.project = project
        self.tenant_id = tenant_id
        self.markup = markup
        self.rates = RateLookup()
        self._id = 0

    def _next_id(self, prefix: str) -> str:
        self._id += 1
        return f"{prefix}_{self._id:03d}"

    def _item(self, trade, description, unit, qty, rate_key,
              confidence=85, notes="", specialist=False) -> BOQItem:
        low, high, _, source = self.rates.get(rate_key)
        mid = (low + high) / 2
        return BOQItem(
            id=self._next_id(trade[:3].upper()),
            trade=trade,
            description=description,
            unit=unit,
            qty=round(qty, 2),
            rate_low=low,
            rate_high=high,
            rate_used=mid,
            line_total=round(mid * qty, 2),
            source=source,
            confidence=confidence,
            notes=notes,
            requires_specialist=specialist or confidence < 70,
            supplier_key=rate_key,
        )

    async def generate(self) -> BOQ:
        p = self.project
        boq = BOQ(
            project_name=p.title_block.project_name or "Untitled Project",
            tenant_id=self.tenant_id,
            markup_pct=self.markup,
        )

        gfa = p.gross_floor_area or sum(r.area for r in p.rooms)
        rooms = p.rooms

        # ── DEMOLITION ──────────────────────────────────────────────────────
        boq.items.append(self._item(
            "demolition", "Strip out existing fit-out — full scope",
            "m²", gfa, "demolition_light", 90,
            "Full strip-out of existing fit-out including partitions, ceilings, flooring"
        ))

        # ── STRUCTURE ───────────────────────────────────────────────────────
        # Check for mezzanine
        mez_rooms = [r for r in rooms if "mezzanine" in r.name.lower() or "mezz" in r.name.lower()]
        if mez_rooms:
            mez_area = sum(r.area for r in mez_rooms)
            boq.items.append(self._item("concrete", "Mezzanine concrete slab 150mm thk", "m²", mez_area, "concrete_30mpa", 85, "Pending structural engineer sign-off"))
            boq.items.append(self._item("concrete", "Mezzanine steel structure (erected)", "tonne", round(mez_area * 0.035, 1), "structural_steel", 70, "Estimate — structural BOQ required", True))

        # ── WET WORKS ───────────────────────────────────────────────────────
        wet_rooms = [r for r in rooms if r.is_wet]
        if wet_rooms:
            wet_area = sum(r.area for r in wet_rooms) * 1.2  # 20% extra for upstand
            boq.items.append(self._item("wet_works", "Waterproofing membrane — wet areas", "m²", wet_area, "waterproofing", 90))

        # ── DRYWALL & PARTITIONS ─────────────────────────────────────────────
        # Estimate internal wall area from room perimeters
        internal_wall_area = sum(r.perimeter * r.height for r in rooms if r.perimeter > 0)
        if not internal_wall_area:
            # Fallback: estimate from GFA (typical fitout: 1.8m² wall per m² floor)
            internal_wall_area = gfa * 1.8

        # Split: 30% acoustic, 70% standard
        acoustic_area = internal_wall_area * 0.30
        standard_area = internal_wall_area * 0.70

        boq.items.append(self._item("drywall", "Partition wall — single skin 70mm", "m²", round(standard_area), "drywall_single", 85))
        boq.items.append(self._item("drywall", "Partition wall — acoustic double skin", "m²", round(acoustic_area), "drywall_double", 80, "Studio and meeting room boundaries"))

        # Ceilings — from room areas
        all_ceiling_area = gfa
        acoustic_ceiling = sum(r.area for r in rooms if "acoustic" in r.ceiling_finish or "studio" in r.name.lower() or "board" in r.name.lower())
        standard_ceiling  = all_ceiling_area - acoustic_ceiling

        boq.items.append(self._item("drywall", "Suspended ceiling — standard 600×600 grid", "m²", round(standard_ceiling), "ceiling_standard", 90))
        if acoustic_ceiling > 0:
            boq.items.append(self._item("drywall", "Suspended ceiling — acoustic tile", "m²", round(acoustic_ceiling), "ceiling_acoustic", 85))

        # ── SCREED ──────────────────────────────────────────────────────────
        boq.items.append(self._item("flooring", "Sand/cement screed 50mm — general", "m²", round(gfa * 0.85), "screed_50mm", 90))

        # ── FLOORING — from room finishes ────────────────────────────────────
        finish_areas: Dict[str, float] = {}
        for room in rooms:
            ff = room.floor_finish.lower()
            finish_areas[ff] = finish_areas.get(ff, 0) + room.area

        finish_map = {
            "lvt": ("flooring", "LVT / Vinyl plank (laid)", "m²", "lvt_vinyl", 90),
            "vinyl_lvt": ("flooring", "LVT / Vinyl plank (laid)", "m²", "lvt_vinyl", 90),
            "carpet": ("flooring", "Commercial carpet (laid)", "m²", "carpet_commercial", 88),
            "epoxy": ("flooring", "Epoxy floor coating", "m²", "epoxy_coating", 88),
            "porcelain_tile": ("flooring", "Porcelain floor tiles 600×600 (laid)", "m²", "tiles_porcelain_floor", 90),
            "tile": ("flooring", "Porcelain floor tiles 600×600 (laid)", "m²", "tiles_porcelain_floor", 90),
            "studio_floor": ("flooring", "Broadcast studio floor (specialist)", "m²", "lvt_vinyl", 60, True),
            "raised_access": ("flooring", "Raised access floor system", "m²", "lvt_vinyl", 65, True),
        }

        for finish, area in finish_areas.items():
            if area < 2 or not finish:
                continue
            mapping = finish_map.get(finish)
            if mapping:
                trade, desc, unit, rate_key, conf = mapping[:5]
                specialist = len(mapping) > 5 and mapping[5]
                boq.items.append(self._item(trade, desc, unit, round(area), rate_key, conf, specialist=bool(specialist)))

        # ── TILING — from wet rooms ──────────────────────────────────────────
        for room in wet_rooms:
            # Floor tiles
            boq.items.append(self._item("tiling", f"Porcelain floor tiles 600×600 — {room.name}", "m²", round(room.area), "tiles_porcelain_floor", 92))
            # Wall tiles (to 2.1m height on perimeter)
            wall_tile_area = (room.perimeter or math.sqrt(room.area) * 4) * 2.1
            boq.items.append(self._item("tiling", f"Ceramic wall tiles to 2.1m — {room.name}", "m²", round(wall_tile_area), "tiles_ceramic_wall", 88))

        if wet_rooms:
            total_tiled = sum(r.area + (r.perimeter or math.sqrt(r.area)*4)*2.1 for r in wet_rooms)
            boq.items.append(self._item("tiling", "Tile adhesive and grout — all tiled areas", "m²", round(total_tiled), "tile_adhesive_grout", 92))

        # ── JOINERY & DOORS ──────────────────────────────────────────────────
        if p.doors_windows:
            for dw in p.doors_windows:
                rate_map = {
                    "hollow_core": "door_hollow_core",
                    "solid": "door_solid",
                    "fire_60": "door_fire_60",
                    "fire_120": "door_fire_120",
                    "acoustic": "door_acoustic",
                    "glass": "glass_partition",
                }
                rate_key = rate_map.get(dw.type_, "door_solid")
                specialist = dw.type_ in ("acoustic",)
                conf = 95 if dw.type_ in ("hollow_core", "solid", "fire_60") else 70
                boq.items.append(self._item(
                    "joinery",
                    f"{dw.type_.replace('_',' ').title()} door {dw.ref} ({dw.width}×{dw.height}m)",
                    "no", dw.count, rate_key, conf,
                    dw.fire_rating, specialist
                ))
        else:
            # Estimate from GFA if no schedule
            door_count = max(1, int(gfa / 25))
            boq.items.append(self._item("joinery", "Solid core door — estimated count", "no", door_count, "door_solid", 70, "Count estimated from GFA — verify from door schedule"))

        # Kitchen joinery
        kitchen_rooms = [r for r in rooms if "kitchen" in r.name.lower() or "break" in r.name.lower()]
        for kr in kitchen_rooms:
            kitchen_run = math.sqrt(kr.area) * 1.5
            boq.items.append(self._item("joinery", f"Kitchen units — {kr.name}", "m run", round(kitchen_run), "kitchen_unit", 80))

        # ── PLUMBING ─────────────────────────────────────────────────────────
        plumbing_from_schedule = [m for m in p.mep_schedules if m.trade == "plumbing"]
        if plumbing_from_schedule:
            for item in plumbing_from_schedule:
                boq.items.append(self._item("plumbing", f"Plumbing — {item.item}", item.unit, item.count, "plumbing_bathroom_full", 85))
        else:
            # Estimate from wet rooms
            for room in wet_rooms:
                if "ablut" in room.name.lower() or "toilet" in room.name.lower() or "bath" in room.name.lower() or "wc" in room.name.lower():
                    boq.items.append(self._item("plumbing", f"Plumbing fitout — {room.name}", "no", 1, "plumbing_bathroom_full", 80, "Estimate — verify fixture count"))
            if kitchen_rooms:
                boq.items.append(self._item("plumbing", "Kitchen plumbing — sink, dishwasher, connections", "item", 1, "plumbing_drain_point", 80))

        # ── ELECTRICAL ───────────────────────────────────────────────────────
        elec_items = [m for m in p.mep_schedules if m.trade == "electrical"]
        boq.items.append(self._item("electrical", "DB board — 3-phase main distribution", "no", 1, "electrical_db", 88))
        boq.items.append(self._item("electrical", "Electrical fitout — lighting, power, data (office areas)", "m²", round(gfa), "electrical_fitout", 85))
        boq.items.append(self._item("electrical", "Emergency lighting & exit signage", "no", max(6, int(gfa / 20)), "electrical_emerg_light", 88))
        boq.items.append(self._item("electrical", "Structured cabling cat6A — data points", "point", max(8, int(gfa / 9)), "data_point_cat6a", 82))

        # ── HVAC ─────────────────────────────────────────────────────────────
        hvac_items = [m for m in p.mep_schedules if m.trade == "hvac"]
        hvac_kw = round(gfa * 0.08, 1)  # 80W/m² rule of thumb
        boq.items.append(self._item("hvac", f"VRF HVAC system — {hvac_kw}kW capacity (installed)", "kW", hvac_kw, "hvac_vrf", 85, "Confirm with HVAC engineer"))

        # ── FIRE PROTECTION ──────────────────────────────────────────────────
        boq.items.append(self._item("fire", "Sprinkler system (installed)", "m²", round(gfa), "fire_sprinkler", 88))
        boq.items.append(self._item("fire", "Fire detection & alarm system", "m²", round(gfa), "fire_detection", 85))
        boq.items.append(self._item("fire", "Portable fire extinguishers", "no", max(4, int(gfa / 50)), "fire_extinguisher", 90))

        # ── PAINTING ─────────────────────────────────────────────────────────
        # Wall area = perimeters × height (or estimate from GFA)
        total_wall_area = internal_wall_area + (gfa * 0.5)  # Add external wall faces
        boq.items.append(self._item("painting", "Interior paint — 2 coats walls & ceilings", "m²", round(total_wall_area), "paint_interior", 92))

        logger.info(f"BOQ generated: {len(boq.items)} items, net total R{boq.net_total:,.0f}")
        return boq


import math
