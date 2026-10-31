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
    excluded: List[str] = field(default_factory=list)     # what this bill deliberately leaves out

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
        # The rate range, source and trade grouping travel with the bill (the Excel export's Source
        # column and Trade Summary sheet were empty because to_dict dropped them).
        items = [
            {
                "id": i.id,
                "trade": i.trade,
                "description": i.description,
                "unit": i.unit,
                "qty": round(i.qty, 2),
                "rate_low": round(i.rate_low, 2),
                "rate_high": round(i.rate_high, 2),
                "rate_used": round(i.rate_used, 2),
                "line_total": round(i.line_total, 2),
                "rate_cents": i.rate_cents,
                "line_total_cents": i.line_total_cents,
                "source": i.source,
                "confidence": i.confidence,
                "notes": i.notes,
                "requires_specialist": i.requires_specialist,
            }
            for i in self.items
        ]
        by_trade: Dict[str, list] = {}
        for it in items:
            by_trade.setdefault(it["trade"], []).append(it)
        vat_cents = int((Decimal(self.grand_total_cents) * Decimal("0.15")).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
        return {
            "project_name": self.project_name,
            "net_total": self.net_total,
            "markup_pct": self.markup_pct,
            "markup_amount": self.markup_amount,
            "grand_total": self.grand_total,
            "net_total_cents": self.net_total_cents,
            "markup_cents": self.markup_cents,
            "grand_total_cents": self.grand_total_cents,
            "vat_cents": vat_cents,
            "total_incl_vat_cents": self.grand_total_cents + vat_cents,
            "items": items,
            "by_trade": by_trade,
            "excluded": list(self.excluded),
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
    # Every FALLBACK rate is a laid / installed / erected rate. The scraped catalogue
    # (vula_material_rates, checked 2026-10-31) is mostly retail SUPPLY prices — a porcelain tile
    # at R40–R150/m² against R380–R650/m² laid — so a scraped row only stands in when its label
    # says it includes the work. Rows with no source (test rows found in production) never do.
    INSTALLED = ["laid", "installed", "install", "fixed", "supply and fix", "supply fix",
                 "erected", "applied", "complete", "fitted", "incl labour", "including labour"]

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
        if not any(f" {w} " in text or f" {w}" in text for w in self.INSTALLED):
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
            if low <= 0 or not (source or "").strip():
                continue
            if ref and ref[0] > 0:
                ref_mid, mid = (ref[0] + ref[1]) / 2, (low + high) / 2
                if not (self.BAND[0] * ref_mid <= mid <= self.BAND[1] * ref_mid):
                    continue
            return low, high, row_unit, source
        return ref or (0, 0, unit or "item", "Estimate")


def _is_window(d) -> bool:
    return (d.type_ or "").lower() == "window" or str(d.ref or "").upper().startswith("W")


def _perim(room) -> float:
    """A room's perimeter; a square's when only the area was read (no wall ever left out)."""
    return room.perimeter or math.sqrt(max(room.area or 0, 0)) * 4


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
        # `confidence` is now the cap for HOW the line is derived (100 = straight from the measured
        # quantity); the quantities' own measured/approved confidence caps it further.
        base = getattr(self, "_base_conf", None)
        if base:
            confidence = min(confidence, base)
        if not mid:
            notes = (notes + " · " if notes else "") + "No rate on file — add one to price this line"
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
        """Price what was MEASURED — room floors, walls and skirting, ceilings, doors and windows,
        wet areas, and fixtures counted from a schedule. 2026-10-31 (v1 scope): nothing is
        guessed from floor area any more (electrical, HVAC, fire, demolition, screed, door counts,
        kitchen runs, mezzanine steel, an acoustic/standard partition split). Those are listed in
        BOQ.excluded so the bill says what it leaves out, instead of a ~R50k "complete" BOQ built
        from fixed minimums when the reader found nothing."""
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
        # Line confidence follows the quantities: a person approved them, or what was measured.
        self._base_conf = 100.0 if status == "approved" else round((p.confidence_overall or 0) * 100, 1)
        rooms = [r for r in p.rooms if (r.area or 0) > 0]
        dws = p.doors_windows or []
        windows = [d for d in dws if _is_window(d)]
        glass = [d for d in dws if not _is_window(d) and d.type_ in ("glass", "glazed")]
        doors = [d for d in dws if d not in windows and d not in glass]
        door_area = sum(d.width * d.height * (d.count or 1) for d in doors)
        door_width = sum(d.width * (d.count or 1) for d in doors)
        window_area = sum(d.width * d.height * (d.count or 1) for d in windows)
        heights = sorted({round(r.height or 2.7, 2) for r in rooms})
        h_note = (f"Wall height {heights[0]:.2f} m" if len(heights) == 1 else "Wall heights per room") + \
            (" (confirmed at review)" if status == "approved" else " (assumed — confirm at review)")
        wet = [r for r in rooms if r.is_wet]
        boq.excluded = [
            "Electrical, HVAC and fire protection — not measured from plans (v1); price from the engineers' schedules.",
            "Demolition / strip-out and screed — not measured.",
        ]

        # ── STRUCTURE: mezzanine slab (measured area × the 150 mm the line states) ──
        mez = [r for r in rooms if "mezz" in r.name.lower()]
        if mez:
            mez_area = sum(r.area for r in mez)
            # concrete_30mpa is a per-m³ rate: measure the slab as a volume (NEW-T3).
            boq.items.append(self._item("concrete", "Mezzanine concrete slab 150mm thk", "m³", mez_area * 0.15,
                                        "concrete_30mpa", 100,
                                        f"{mez_area:.1f}m² × 0.15m. Pending structural engineer sign-off"))
            boq.excluded.append("Mezzanine steel structure — needs the structural engineer's BOQ.")

        # ── WET WORKS: floor + a 150 mm upstand on the perimeter ──
        if wet:
            wp = sum(r.area + _perim(r) * 0.15 for r in wet)
            boq.items.append(self._item("wet_works", "Waterproofing membrane — wet areas incl. 150mm upstand",
                                        "m²", wp, "waterproofing", 100))

        # ── PARTITIONS: internal wall length = (Σ room perimeters − external walls) / 2 — each
        # internal wall bounds two rooms. Only when the external wall length is known.
        perims = sum(_perim(r) for r in rooms)
        if p.external_walls_m and perims > p.external_walls_m:
            internal_m = (perims - p.external_walls_m) / 2
            h = sum((r.height or 2.7) * _perim(r) for r in rooms) / perims
            area = max(0.0, internal_m * h - door_area)
            boq.items.append(self._item("drywall", "Partition wall — single skin 70mm (internal walls)", "m²",
                                        area, "drywall_single", 95,
                                        f"{internal_m:.1f} m of internal wall × {h:.2f} m, less doors. {h_note}"))
        elif rooms:
            boq.excluded.append("Partition walls — the external wall length isn't known, so internal walls "
                                "can't be told apart. Add it at review or measure partitions separately.")

        # ── CEILINGS: per room, never over gross floor area; "exposed"/none means no ceiling ──
        acoustic = sum(r.area for r in rooms if "acoustic" in (r.ceiling_finish or "").lower()
                       or "studio" in r.name.lower())
        none = sum(r.area for r in rooms if (r.ceiling_finish or "").lower() in ("exposed", "none", "open"))
        standard = sum(r.area for r in rooms) - acoustic - none
        if standard > 0:
            boq.items.append(self._item("drywall", "Suspended ceiling — standard 600×600 grid", "m²",
                                        standard, "ceiling_standard", 100))
        if acoustic > 0:
            boq.items.append(self._item("drywall", "Suspended ceiling — acoustic tile", "m²", acoustic,
                                        "ceiling_acoustic", 100))

        # ── FLOORING: each room's finish once. A wet room with no finish is tiled; its floor tiles
        # are priced here only (they were in flooring AND tiling before).
        finish_areas: Dict[str, float] = {}
        for r in rooms:
            ff = (r.floor_finish or "").lower() or ("porcelain_tile" if r.is_wet else "")
            if ff:
                finish_areas[ff] = finish_areas.get(ff, 0) + r.area
        finish_map = {
            "lvt": ("flooring", "LVT / Vinyl plank (laid)", "m²", "lvt_vinyl", 100),
            "vinyl_lvt": ("flooring", "LVT / Vinyl plank (laid)", "m²", "lvt_vinyl", 100),
            "carpet": ("flooring", "Commercial carpet (laid)", "m²", "carpet_commercial", 100),
            "epoxy": ("flooring", "Epoxy floor coating", "m²", "epoxy_coating", 100),
            "porcelain_tile": ("flooring", "Porcelain floor tiles 600×600 (laid)", "m²", "tiles_porcelain_floor", 100),
            "tile": ("flooring", "Porcelain floor tiles 600×600 (laid)", "m²", "tiles_porcelain_floor", 100),
            "studio_floor": ("flooring", "Broadcast studio floor (specialist)", "m²", "lvt_vinyl", 60, True),
            "raised_access": ("flooring", "Raised access floor system", "m²", "lvt_vinyl", 65, True),
        }
        tiled_floor = 0.0
        for finish, area in finish_areas.items():
            mapping = finish_map.get(finish)
            if not mapping:
                boq.excluded.append(f"Floor finish '{finish}' ({area:.1f} m²) — no rate mapping; price it by hand.")
                continue
            trade, desc, unit, rate_key, cap = mapping[:5]
            if rate_key == "tiles_porcelain_floor":
                tiled_floor += area
            boq.items.append(self._item(trade, desc, unit, area, rate_key, cap,
                                        specialist=len(mapping) > 5 and mapping[5]))

        # ── SKIRTING: perimeter of rooms that aren't tiled, less door openings (both faces) ──
        skirt_rooms = [r for r in rooms if not r.is_wet and "tile" not in (r.floor_finish or "").lower()]
        if skirt_rooms:
            length = max(0.0, sum(_perim(r) for r in skirt_rooms) - 2 * door_width)
            boq.items.append(self._item("joinery", "Skirting", "m", length, "skirting", 95,
                                        "Room perimeters less door openings"))

        # ── TILING: wall tiles in wet rooms to 2.1 m; adhesive/grout over every tiled surface ──
        wall_tiles = 0.0
        for r in wet:
            a = _perim(r) * 2.1
            wall_tiles += a
            boq.items.append(self._item("tiling", f"Ceramic wall tiles to 2.1m — {r.name}", "m²", a,
                                        "tiles_ceramic_wall", 95))
        if tiled_floor + wall_tiles > 0:
            boq.items.append(self._item("tiling", "Tile adhesive and grout — all tiled areas", "m²",
                                        tiled_floor + wall_tiles, "tile_adhesive_grout", 100))

        # ── DOORS, WINDOWS, GLAZING from the schedule: counts per type, windows and glass by area ──
        rate_map = {"hollow_core": "door_hollow_core", "solid": "door_solid", "fire_60": "door_fire_60",
                    "fire_120": "door_fire_120", "acoustic": "door_acoustic", "sliding": "door_solid"}
        for d in doors:
            boq.items.append(self._item(
                "joinery", f"{d.type_.replace('_', ' ').title()} door {d.ref} ({d.width}×{d.height}m)",
                "no", d.count or 1, rate_map.get(d.type_, "door_solid"),
                100 if d.type_ in rate_map else 70, d.fire_rating, d.type_ == "acoustic"))
        for w in windows:
            boq.items.append(self._item("glazing", f"Aluminium window {w.ref} ({w.width}×{w.height}m × {w.count or 1})",
                                        "m²", w.width * w.height * (w.count or 1), "window_alu_dg", 100))
        for g in glass:
            boq.items.append(self._item("glazing", f"Glass partition {g.ref} ({g.width}×{g.height}m × {g.count or 1})",
                                        "m²", g.width * g.height * (g.count or 1), "glass_partition", 100))
        if not dws:
            boq.excluded.append("Doors and windows — no schedule read; nothing estimated.")

        # ── PLUMBING: fixtures counted on a schedule, each a fixture point (not a whole bathroom) ──
        for m in [m for m in p.mep_schedules if m.trade == "plumbing" and (m.count or 0) > 0]:
            boq.items.append(self._item("plumbing", f"Plumbing point — {m.item}", "no", m.count,
                                        "plumbing_drain_point", 65,
                                        "Supply and connection point only — sanitaryware priced separately"))
        if wet and not any(m.trade == "plumbing" for m in p.mep_schedules):
            boq.excluded.append("Plumbing fixtures — no schedule read for the wet rooms; nothing estimated.")

        # ── PAINTING: wall faces of painted rooms (every face, so shared walls count once per side),
        # less openings and wall tiles. Ceilings aren't painted here.
        painted = [r for r in rooms if not (r.wall_finish or "").lower().startswith(("tile", "acoustic", "feature"))]
        if painted:
            faces = sum(_perim(r) * (r.height or 2.7) for r in painted)
            area = max(0.0, faces - 2 * door_area - window_area - wall_tiles)
            boq.items.append(self._item("painting", "Interior paint — 2 coats walls", "m²", area,
                                        "paint_interior", 95, f"Room wall faces less openings and tiles. {h_note}"))

        logger.info(f"BOQ generated: {len(boq.items)} items, net total R{boq.net_total:,.0f}")
        return boq
