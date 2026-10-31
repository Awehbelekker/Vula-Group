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

import logging
import math
import re
import sqlite3
from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from typing import Dict, List, Optional

from vula.takeoff.plan_reader import ExtractedProject

logger = logging.getLogger(__name__)

# On the persistent data volume (/data on Railway) — ~/.vula was wiped on every deploy.
from config import settings as _settings  # noqa: E402
DB_PATH = _settings.data_dir / "construction_rates.db"

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
    rate_cents: int = 0
    line_total_cents: int = 0

    def __post_init__(self):
        # Money is integer cents, worked out here (CLAUDE.md non-negotiable; NEW-T4 in
        # docs/golive-and-takeoff-audit.md). Same rounding as the online BOQ —
        # vula/commerce/boq_online.amount_cents — so a takeoff bill and an online bill of the
        # same lines agree to the cent. line_total stays as rands for existing readers.
        from vula.commerce.boq_online import amount_cents
        self.rate_cents = int((Decimal(str(self.rate_used or 0)) * 100)
                              .quantize(Decimal("1"), rounding=ROUND_HALF_UP))
        self.line_total_cents = amount_cents(Decimal(str(self.qty or 0)), self.rate_cents)
        self.line_total = self.line_total_cents / 100


@dataclass
class BOQ:
    project_name: str
    tenant_id: str
    items: List[BOQItem] = field(default_factory=list)
    markup_pct: float = 15.0

    @property
    def net_total_cents(self) -> int:
        return sum(i.line_total_cents for i in self.items)

    @property
    def markup_cents(self) -> int:
        return int((Decimal(self.net_total_cents) * Decimal(str(self.markup_pct)) / 100)
                   .quantize(Decimal("1"), rounding=ROUND_HALF_UP))

    @property
    def grand_total_cents(self) -> int:
        return self.net_total_cents + self.markup_cents

    @property
    def net_total(self) -> float:
        return self.net_total_cents / 100

    @property
    def markup_amount(self) -> float:
        return self.markup_cents / 100

    @property
    def grand_total(self) -> float:
        return self.grand_total_cents / 100

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
            "net_total_cents": self.net_total_cents,
            "markup_cents": self.markup_cents,
            "grand_total_cents": self.grand_total_cents,
            "items": [
                {
                    "id": i.id,
                    "trade": i.trade,
                    "description": i.description,
                    "unit": i.unit,
                    "qty": round(i.qty, 2),
                    "rate_used": round(i.rate_used, 2),
                    "line_total": round(i.line_total, 2),
                    "rate_cents": i.rate_cents,
                    "line_total_cents": i.line_total_cents,
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

    # What a scraped rate's LABEL must say to stand in for a BOQ key (NEW-T2). Scraped keys are
    # slugs of whatever label the page used ("porcelain_tiles_600x600__laid_"), so the old
    # `key LIKE '%tiles_porcelain_floor%'` never matched one and every line quietly priced from
    # FALLBACK — while a loose substring could match the wrong item. Each group is "any of these
    # words"; every group must appear. Same unit is required, and the price must sit within
    # BAND of the reference rate (a mis-read page doesn't become a quote).
    TERMS: Dict[str, List[List[str]]] = {
        "demolition_light": [["demolition", "strip"]],
        "concrete_30mpa": [["concrete"], ["30mpa", "30 mpa", "30"]],
        "structural_steel": [["structural"], ["steel"]],
        "brickwork_face": [["face"], ["brick"]],
        "brickwork_plaster": [["brick"], ["plaster"]],
        "waterproofing": [["waterproof"]],
        "drywall_single": [["drywall", "partition", "gypsum", "plasterboard"], ["single"]],
        "drywall_double": [["drywall", "partition", "gypsum", "plasterboard"], ["double", "acoustic"]],
        "ceiling_standard": [["ceiling"], ["suspended", "grid", "standard"]],
        "ceiling_acoustic": [["ceiling"], ["acoustic"]],
        "screed_50mm": [["screed"]],
        "lvt_vinyl": [["lvt", "vinyl"]],
        "carpet_commercial": [["carpet"]],
        "epoxy_coating": [["epoxy"]],
        "tiles_porcelain_floor": [["porcelain"], ["tile"]],
        "tiles_ceramic_wall": [["ceramic"], ["tile"], ["wall"]],
        "tile_adhesive_grout": [["adhesive", "grout"]],
        "door_hollow_core": [["door"], ["hollow"]],
        "door_solid": [["door"], ["solid"]],
        "door_fire_60": [["door"], ["fire"], ["60"]],
        "door_fire_120": [["door"], ["fire"], ["120"]],
        "door_acoustic": [["door"], ["acoustic"]],
        "glass_partition": [["glass", "glazed"], ["partition"]],
        "window_alu_dg": [["window"], ["alumin"]],
        "kitchen_unit": [["kitchen"]],
        "paint_interior": [["paint"], ["interior", "internal", "wall"]],
        "paving_brick": [["paving", "paver"]],
        "fire_sprinkler": [["sprinkler"]],
        "fire_detection": [["detection", "detector", "alarm"]],
        "fire_extinguisher": [["extinguisher"]],
        "data_point_cat6a": [["data", "cat6"]],
        "hvac_vrf": [["vrf"]],
        "hvac_split": [["split"]],
    }
    # Words that mean a different item even when every TERMS group matched.
    EXCLUDE: Dict[str, List[str]] = {
        "door_solid": ["fire", "acoustic", "hollow"],
        "door_hollow_core": ["fire", "acoustic"],
        "drywall_single": ["double", "acoustic"],
        "ceiling_standard": ["acoustic"],
        "paint_interior": ["exterior", "external", "roof"],
    }
    BAND = (0.25, 4.0)

    def __init__(self, db_path: Path = DB_PATH):
        self.db_path = db_path

    def _rows(self) -> List[tuple]:
        if not self.db_path.exists():
            return []
        try:
            conn = sqlite3.connect(self.db_path)
            try:
                return conn.execute("SELECT label, unit, low, high, source FROM material_rates").fetchall()
            finally:
                conn.close()
        except Exception:
            return []

    def _matches(self, key: str, label: str) -> bool:
        groups = self.TERMS.get(key)
        if not groups:
            return False
        text = " " + re.sub(r"[^a-z0-9]+", " ", (label or "").lower()) + " "
        if any(f" {w}" in text for w in self.EXCLUDE.get(key, [])):
            return False
        return all(any(f" {w}" in text for w in group) for group in groups)

    def get(self, key: str, unit: Optional[str] = None) -> tuple:
        """Returns (low, high, unit, source) — a scraped market rate for the same item in the same
        unit when one is on file, else the reference FALLBACK."""
        ref = self.FALLBACK.get(key)
        want = _unit_key(unit or (ref[2] if ref else ""))
        for label, row_unit, low, high, source in self._rows():
            if not self._matches(key, label) or _unit_key(row_unit) != want:
                continue
            try:
                low, high = float(low), float(high or low)
            except (TypeError, ValueError):
                continue
            if low <= 0:
                continue
            if ref and ref[0] > 0:
                ref_mid, mid = (ref[0] + ref[1]) / 2, (low + high) / 2
                if not (self.BAND[0] * ref_mid <= mid <= self.BAND[1] * ref_mid):
                    continue
            return low, high, row_unit, source
        return ref or (0, 0, unit or "item", "Estimate")


class ReviewRequired(Exception):
    """Raised by BOQGenerator.generate for a project whose quantities await (or failed) review."""


# ─── BOQ Generator ────────────────────────────────────────────────────────────

def _unit_key(unit) -> str:
    u = str(unit or "").strip().lower().replace("²", "2").replace("³", "3")
    return {"sqm": "m2", "no": "each", "nr": "each", "item": "each", "ea": "each"}.get(u, u)


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
        self._own: Optional[list] = None
        self._id = 0

    def _own_rate(self, rate_key: str, unit: str) -> Optional[tuple]:
        """The tenant's own rate for this item, before any platform or market rate: its QS rate
        library, then rates learned from its own invoices/quotes/BOQs (vula/api/qs.search_rates).
        2026-09-28: Takeoff priced every job from scraped market rates and a built-in table even
        for a tenant with ~1,000 priced lines of its own on file. Matched conservatively — every
        word of the rate key in the rate's description, and the same unit — so a loose match
        never replaces a market rate with the wrong item's price."""
        if not self.tenant_id or self.tenant_id == "default":
            return None
        if self._own is None:
            try:
                from vula.api.qs import search_rates
                self._own = search_rates(self.tenant_id, "", limit=3000)
            except Exception:
                self._own = []
        words = [w for w in rate_key.lower().split("_") if len(w) >= 3]
        want = _unit_key(unit)
        for r in self._own:
            desc = (r.get("description") or "").lower()
            if words and all(w in desc for w in words) and _unit_key(r.get("unit")) == want:
                rate = float(r.get("rate") or 0)
                if rate <= 0:
                    continue
                low = (r.get("low_cents") or rate * 100) / 100
                high = (r.get("high_cents") or rate * 100) / 100
                source = r.get("source") if r.get("learned") else f"Your rate{' · ' + r['source'] if r.get('source') else ''}"
                return low, high, rate, source
        return None

    def _next_id(self, prefix: str) -> str:
        self._id += 1
        return f"{prefix}_{self._id:03d}"

    def _item(self, trade, description, unit, qty, rate_key,
              confidence=85, notes="", specialist=False) -> BOQItem:
        own = self._own_rate(rate_key, unit)
        if own:
            low, high, mid, source = own
        else:
            low, high, _, source = self.rates.get(rate_key, unit)
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
            line_total=0,           # worked out in cents by BOQItem.__post_init__
            source=source,
            confidence=confidence,
            notes=notes,
            requires_specialist=specialist or confidence < 70,
            supplier_key=rate_key,
        )

    async def generate(self) -> BOQ:
        p = self.project
        # Phase 4d: quantities a person hasn't confirmed are never priced (vula/takeoff/accuracy.py).
        status = (getattr(p, "review", None) or {}).get("status", "not_required")
        if status in ("pending", "rejected"):
            raise ReviewRequired(f"Takeoff quantities are {status} review — not priced.")
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
            # concrete_30mpa is a per-m³ rate: measure the slab as a volume (NEW-T3 — it was m² × an m³
            # rate, ~6.7× too high for a 150mm slab).
            boq.items.append(self._item("concrete", "Mezzanine concrete slab 150mm thk", "m³", mez_area * 0.15, "concrete_30mpa", 85, f"{mez_area:.1f}m² × 0.15m. Pending structural engineer sign-off"))
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
        boq.items.append(self._item("electrical", "DB board — 3-phase main distribution", "no", 1, "electrical_db", 88))
        boq.items.append(self._item("electrical", "Electrical fitout — lighting, power, data (office areas)", "m²", round(gfa), "electrical_fitout", 85))
        boq.items.append(self._item("electrical", "Emergency lighting & exit signage", "no", max(6, int(gfa / 20)), "electrical_emerg_light", 88))
        boq.items.append(self._item("electrical", "Structured cabling cat6A — data points", "point", max(8, int(gfa / 9)), "data_point_cat6a", 82))

        # ── HVAC ─────────────────────────────────────────────────────────────
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
