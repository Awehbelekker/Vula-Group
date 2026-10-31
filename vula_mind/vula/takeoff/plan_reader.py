"""
vula/takeoff/plan_reader.py

Vula Takeoff — AI Plan Reading Engine

Reads architectural drawings (PDF, DXF, DWG, image) and extracts:
- Room schedule (names, areas, finishes)
- Wall lengths and heights per room
- Door and window schedule (type, size, count)
- Floor areas by finish type
- Ceiling areas by type
- Wet area boundaries (for waterproofing)
- MEP fixture counts from schedules
- Drawing scale and title block info

Then feeds extracted geometry into the BOQ generator which prices
each element using AECOM 2025/26 rates from the rates database.

File format support:
    PDF     → pdfplumber (text) + GLM-OCR (scanned/raster)
    DXF     → ezdxf (native vector — most accurate)
    DWG     → conversion via ODA File Converter or LibreDWG
    Image   → GLM-OCR (PNG/JPG/TIFF of scanned plans)

Usage:
    reader = PlanReader(tenant_id="digg_001")
    project = await reader.read(Path("sporty_tv_phase2.pdf"))
    boq = await BOQGenerator(project).generate()
    order = OrderManager(boq).build_order()
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import math
import re
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple


from config import settings
from vula.takeoff.accuracy import measured_confidence, parse_scale, review_reasons, schedule_rooms_from_text

logger = logging.getLogger(__name__)

OLLAMA_BASE  = settings.ollama_base
OCR_MODEL    = settings.model_ocr
REASON_MODEL = settings.model_reasoner
FAST_MODEL   = settings.model_worker

# ─── Core data structures ─────────────────────────────────────────────────────

@dataclass
class DrawingSheet:
    sheet_number: str           # A001, M100 etc
    title: str
    scale: str                  # "1:100", "1:50" etc
    discipline: str             # arch | structural | mep | civil
    raw_text: str               # extracted text content
    raw_markdown: str           # OCR markdown
    image_path: Optional[str] = None
    status: str = "pending"     # pending | read | failed
    # Not a confidence any more: kept for old job rows. Measured confidence is per project
    # (vula/takeoff/accuracy.measured_confidence).
    confidence: float = 0.0
    source: str = ""            # text | ocr | dxf | image — how the content was read
    # Calibration (Phase 4b): the scale as a number, how it was established, and who confirmed it.
    # dxf_units — declared $INSUNITS (true size); inferred_from_extent — unitless DXF, guessed;
    # title_block — "SCALE 1:100" printed on the sheet (never checked against a dimension);
    # user_two_point / dimension_check — set when a person confirms a known dimension at review.
    scale_ratio: Optional[float] = None
    scale_method: str = ""
    scale_verified_by: str = ""


@dataclass
class Room:
    name: str
    area: float                 # m²
    perimeter: float = 0.0     # m (for wall lengths)
    height: float = 2.7        # m (default floor-to-ceiling)
    floor_finish: str = ""
    ceiling_finish: str = ""
    wall_finish: str = ""
    is_wet: bool = False        # ablutions, kitchen, studio
    notes: str = ""


@dataclass
class DoorWindow:
    ref: str                    # D1, W1, FD01 etc
    type_: str                  # hollow_core | solid | fire | acoustic | glass
    width: float                # m
    height: float               # m
    count: int = 1
    frame: str = ""
    glazing: str = ""
    fire_rating: str = ""       # 30min | 60min | 120min
    notes: str = ""


@dataclass
class MEPSchedule:
    trade: str                  # plumbing | electrical | hvac | fire
    item: str                   # "WC", "WHB", "DB board", "sprinkler head"
    count: int = 0
    unit: str = "no"
    notes: str = ""


@dataclass
class TitleBlock:
    project_name: str = ""
    project_number: str = ""
    client: str = ""
    address: str = ""
    architect: str = ""
    date: str = ""
    revision: str = ""
    scale: str = ""


@dataclass
class ExtractedProject:
    """Everything Vula reads from a set of drawings."""
    title_block: TitleBlock = field(default_factory=TitleBlock)
    sheets: List[DrawingSheet] = field(default_factory=list)
    rooms: List[Room] = field(default_factory=list)
    doors_windows: List[DoorWindow] = field(default_factory=list)
    mep_schedules: List[MEPSchedule] = field(default_factory=list)
    gross_floor_area: float = 0.0
    net_floor_area: float = 0.0
    external_walls_m: float = 0.0
    internal_walls_m: float = 0.0
    number_of_storeys: int = 1
    building_type: str = ""         # office | retail | residential | mixed
    confidence_overall: float = 0.0
    extraction_notes: List[str] = field(default_factory=list)
    raw_ai_response: str = ""
    # Phase 4c/4d (vula/takeoff/accuracy.py): the AI reader's rooms kept apart from the
    # deterministic reading (DXF geometry / the room schedule's printed areas), what confidence
    # was measured from, and the review gate's state.
    ai_rooms: List[Room] = field(default_factory=list)
    deterministic_rooms: List[Tuple[str, float]] = field(default_factory=list)
    confidence_signals: Dict[str, Dict[str, float]] = field(default_factory=dict)
    review: Dict[str, object] = field(default_factory=lambda: {"status": "not_required", "reasons": []})


# ─── PDF Reader ───────────────────────────────────────────────────────────────

class PDFReader:
    """
    Reads PDF drawing sets — text-based and scanned.
    
    Strategy:
    1. Try pdfplumber for text-based PDFs (Revit exports, CAD-generated)
    2. For pages with <50 chars extracted, treat as scanned → GLM-OCR
    3. Extract page images at 200 DPI for GLM-OCR processing
    """

    def __init__(self, ocr: bool = True):
        # ocr=False: pages with no text layer are kept, unread, instead of sent to the OCR model
        # (the model-free benchmark, evals/takeoff_bench.py).
        self.ocr = ocr

    async def read(self, path: Path) -> List[DrawingSheet]:
        sheets = []
        try:
            import pdfplumber
            with pdfplumber.open(path) as pdf:
                for i, page in enumerate(pdf.pages):
                    text = page.extract_text() or ""
                    tables = page.extract_tables() or []
                    table_text = "\n".join(
                        " | ".join(str(c) for c in row if c)
                        for table in tables for row in table if row
                    )
                    full_text = f"{text}\n{table_text}".strip()

                    # Identify sheet info from title block
                    sheet_num, title, scale, discipline = self._parse_title_block(full_text)

                    if len(full_text) < 50 and not self.ocr:
                        markdown, source = full_text, "ocr_skipped"
                    elif len(full_text) < 50:
                        # Scanned page — extract image and OCR
                        # Own temp file per page and job (#8: concurrent jobs shared /tmp/vula_sheet_{n}.png).
                        with tempfile.TemporaryDirectory(prefix="vula_sheet_") as td:
                            img_path = Path(td) / f"page_{i}.png"
                            page.to_image(resolution=200).save(str(img_path))
                            markdown = await self._ocr_image(img_path)
                        source = "ocr"
                    else:
                        markdown = full_text
                        source = "text"

                    sheets.append(DrawingSheet(
                        sheet_number=sheet_num or f"SH{i+1:03d}",
                        title=title or f"Sheet {i+1}",
                        scale=scale or "unknown",
                        discipline=discipline,
                        raw_text=text,
                        raw_markdown=markdown,
                        status="read",
                        source=source,
                        scale_ratio=parse_scale(scale),
                        scale_method="title_block" if scale else "",
                    ))

        except ImportError:
            logger.error("pdfplumber not installed: pip install pdfplumber")
            # Try pure OCR fallback
            sheets = await self._full_ocr_fallback(path)

        return sheets

    async def _ocr_image(self, img_path: Path) -> str:
        """Read a page image with the vision route (local OCR model first, cloud vision when the
        local one is unreachable) — core/llm_router.resolve_vision_route, like the Smart Scanner.
        2026-10-31: was a raw httpx POST to Ollama with no Cloudflare Access headers."""
        try:
            import litellm
            from core.llm_router import generation_kwargs, resolve_vision_route
            with open(img_path, "rb") as f:
                img_b64 = base64.b64encode(f.read()).decode()
            model, api_key, api_base = await resolve_vision_route()
            # The router's kwargs win (its local timeout sits under Cloudflare's 100 s tunnel limit).
            kw = {"timeout": 90, "temperature": 0.1, "max_tokens": 4096, **generation_kwargs(model)}
            resp = await litellm.acompletion(
                model=model, api_key=api_key, api_base=api_base, **kw,
                messages=[{"role": "user", "content": [
                    {"type": "text", "text": (
                        "You are reading an architectural drawing. Extract ALL text, numbers, "
                        "dimensions, room names, schedules, and annotations. Preserve table "
                        "structure. Return clean markdown.")},
                    {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{img_b64}"}},
                ]}])
            return resp.choices[0].message.content or ""
        except Exception as e:
            logger.warning(f"Plan OCR failed: {e}")
            return ""

    async def _full_ocr_fallback(self, path: Path) -> List[DrawingSheet]:
        """Convert PDF pages to images and OCR each."""
        try:
            from pdf2image import convert_from_path
            images = convert_from_path(str(path), dpi=200)
            sheets = []
            for i, img in enumerate(images):
                with tempfile.TemporaryDirectory(prefix="vula_ocr_") as td:
                    img_path = Path(td) / f"page_{i}.png"
                    img.save(str(img_path))
                    markdown = await self._ocr_image(img_path)
                sheets.append(DrawingSheet(
                    sheet_number=f"SH{i+1:03d}",
                    title=f"Sheet {i+1}",
                    scale="unknown",
                    discipline="arch",
                    raw_text="",
                    raw_markdown=markdown,
                    status="read",
                    source="ocr",
                ))
            return sheets
        except ImportError:
            logger.error("pdf2image not installed: pip install pdf2image")
            return []

    def _parse_title_block(self, text: str) -> Tuple[str, str, str, str]:
        """Extract sheet number, title, scale, discipline from title block text."""
        sheet = ""
        title = ""
        scale = ""
        discipline = "arch"

        # Sheet number patterns: A001, M100, E200, P300, S400
        m = re.search(r'\b([AMEPSC]\d{3,4})\b', text)
        if m:
            sheet = m.group(1)
            prefix = sheet[0]
            discipline = {"A": "arch", "M": "mep", "E": "mep", "P": "mep", "S": "structural", "C": "civil"}.get(prefix, "arch")

        # Scale: 1:100, 1:50 etc
        m = re.search(r'(?:SCALE|Scale)[:\s]+(\d+:\d+)', text, re.IGNORECASE)
        if m:
            scale = m.group(1)

        # Title: line after "DRAWING TITLE" or first all-caps line
        m = re.search(r'(?:DRAWING TITLE|TITLE)[:\s]+([A-Z][A-Z\s\-&]+)', text)
        if m:
            title = m.group(1).strip()[:60]

        return sheet, title, scale, discipline


# ─── Plane geometry (metres) ─────────────────────────────────────────────────

def _shoelace(pts: List[Tuple[float, float]]) -> float:
    n = len(pts)
    return abs(sum(pts[i][0] * pts[(i + 1) % n][1] - pts[(i + 1) % n][0] * pts[i][1]
                   for i in range(n))) / 2.0 if n >= 3 else 0.0


def _perimeter(pts: List[Tuple[float, float]]) -> float:
    n = len(pts)
    return sum(math.dist(pts[i], pts[(i + 1) % n]) for i in range(n)) if n >= 2 else 0.0


def _centroid(pts: List[Tuple[float, float]]) -> Tuple[float, float]:
    return (sum(p[0] for p in pts) / len(pts), sum(p[1] for p in pts) / len(pts))


def _inside(pt: Tuple[float, float], poly: List[Tuple[float, float]]) -> bool:
    """Ray-casting point-in-polygon."""
    x, y = pt
    inside = False
    n = len(poly)
    for i in range(n):
        (x1, y1), (x2, y2) = poly[i], poly[(i + 1) % n]
        if (y1 > y) != (y2 > y) and x < (x2 - x1) * (y - y1) / (y2 - y1) + x1:
            inside = not inside
    return inside


# ─── DXF/DWG Reader ──────────────────────────────────────────────────────────

class DXFReader:
    """
    Reads DXF files natively using ezdxf.
    Extracts polylines, hatches, text, blocks.
    Most accurate for vector measurements.
    """

    # $INSUNITS → metres per drawing unit (AutoCAD codes). NEW-T1: areas were taken in raw drawing
    # units and reported as m² — a plan drawn in mm (the SA norm) came out 1,000,000× too big.
    _INSUNITS = {1: ("in", 0.0254), 2: ("ft", 0.3048), 4: ("mm", 0.001), 5: ("cm", 0.01),
                 6: ("m", 1.0), 14: ("dm", 0.1)}

    def __init__(self):
        self.last_units: Dict[str, object] = {}
        self.last_outline_area: float = 0.0      # m², the building outline when one was drawn
        self.last_outline_perimeter: float = 0.0
        self.last_unlabelled: int = 0

    def _units(self, doc, msp) -> Dict[str, object]:
        code = int(doc.header.get("$INSUNITS", 0) or 0)
        if code in self._INSUNITS:
            name, k = self._INSUNITS[code]
            return {"units": name, "to_metres": k, "method": "dxf_units", "verified": True}
        # Unitless: infer from the drawing's size. A building plan's extent is metres-to-hundreds
        # of metres; in millimetres that's thousands-to-hundreds-of-thousands. Never trusted —
        # marked unverified so the job goes to review (vula/takeoff/accuracy.py).
        extent = 0.0
        for e in msp.query("LWPOLYLINE POLYLINE"):
            for x, y in self._points(e):
                extent = max(extent, abs(x), abs(y))
        name, k = ("mm", 0.001) if extent > 1000 else ("m", 1.0)
        return {"units": name, "to_metres": k, "method": "inferred_from_extent", "verified": False}

    @staticmethod
    def _block_kind(name: str) -> str:
        words = set(re.split(r"[^A-Z0-9]+", name.upper()))
        if words & {"DOOR", "DR", "DOORS"} or re.match(r"^D\d+$", name.upper()):
            return "door"
        if words & {"WINDOW", "WD", "WIN", "WINDOWS"} or re.match(r"^W\d+$", name.upper()):
            return "window"
        return ""

    async def read(self, path: Path) -> Tuple[List[Room], List[DoorWindow]]:
        try:
            import ezdxf
            doc = ezdxf.readfile(str(path))
            msp = doc.modelspace()
            self.last_units = self._units(doc, msp)
            k = float(self.last_units["to_metres"])

            # Closed polylines, in metres.
            polys: List[List[Tuple[float, float]]] = []
            for entity in msp.query("LWPOLYLINE POLYLINE"):
                if getattr(entity, "is_closed", False):
                    pts = [(x * k, y * k) for x, y in self._points(entity)]
                    if len(pts) >= 3 and _shoelace(pts) > 2:      # ignore tiny entities (m²)
                        polys.append(pts)

            # Room labels: TEXT/MTEXT whose insertion point falls inside a polygon.
            labels: List[Tuple[Tuple[float, float], str]] = []
            for entity in msp.query("TEXT MTEXT"):
                try:
                    txt = entity.plain_text() if entity.dxftype() == "MTEXT" else entity.dxf.text
                    ins = entity.dxf.insert
                except Exception:
                    continue
                if isinstance(txt, str) and txt.strip():
                    labels.append(((float(ins[0]) * k, float(ins[1]) * k), txt.strip().splitlines()[0]))

            # A polygon that contains the centre of two or more others is the building outline
            # (or a zone boundary), not a room: counting it too double-counts every room inside.
            # Its area is the measured gross floor area.
            centres = [_centroid(p) for p in polys]
            outline_idx = {i for i, p in enumerate(polys)
                           if sum(1 for j, c in enumerate(centres) if j != i and _inside(c, p)) >= 2}
            outline = max(outline_idx, key=lambda i: _shoelace(polys[i]), default=None)
            self.last_outline_area = round(_shoelace(polys[outline]), 2) if outline is not None else 0.0
            # The outline's perimeter is the external wall length — what tells internal partitions
            # apart from external walls when the bill is priced (boq_generator, partitions).
            self.last_outline_perimeter = round(_perimeter(polys[outline]), 2) if outline is not None else 0.0

            rooms = []
            doors = []
            self.last_unlabelled = 0
            for i, pts in enumerate(polys):
                if i in outline_idx:
                    continue
                name = next((t for (pt, t) in labels if _inside(pt, pts)), "")
                if not name:
                    self.last_unlabelled += 1
                    name = f"Unlabelled {self.last_unlabelled}"
                rooms.append(Room(name=name, area=round(_shoelace(pts), 2),
                                  perimeter=round(_perimeter(pts), 2)))

            # Extract blocks for doors/windows — whole words only (NEW-T7: "DR" in "DRAIN" was a door)
            for entity in msp.query("INSERT"):
                kind = self._block_kind(entity.dxf.name)
                if kind == "door":
                    doors.append(DoorWindow(ref=f"D{len(doors)+1}", type_="solid", width=0.9, height=2.1))
                elif kind == "window":
                    doors.append(DoorWindow(ref=f"W{len(doors)+1}", type_="window", width=1.2, height=1.2))

            return rooms, doors

        except ImportError:
            logger.error("ezdxf not installed: pip install ezdxf")
            return [], []
        except Exception as e:
            logger.error(f"DXF read failed: {e}")
            return [], []

    @staticmethod
    def _points(entity) -> List[Tuple[float, float]]:
        """(x, y) of a polyline's vertices. NEW-T9 (2026-10-31): the old code read `.x`/`.y` off
        LWPOLYLINE.vertices(), which yields plain tuples — the AttributeError was swallowed, so
        every LWPOLYLINE (what modern CAD writes) measured 0 m² and no DXF ever produced a room."""
        try:
            if entity.dxftype() == "LWPOLYLINE":
                return [(float(x), float(y)) for x, y in entity.get_points("xy")]
            return [(float(p[0]), float(p[1])) for p in entity.points()]
        except Exception:
            return []

    def _polyline_area(self, entity) -> float:
        """Area of a closed polyline (shoelace), in drawing units²."""
        return _shoelace(self._points(entity))

    def _polyline_perimeter(self, entity) -> float:
        """Perimeter of a closed polyline, in drawing units."""
        return _perimeter(self._points(entity))


# ─── AI Extraction Engine ─────────────────────────────────────────────────────

class DrawingIntelligence:
    """
    Uses DeepSeek R1 to extract structured data from drawing text/markdown.
    
    Handles:
    - Room schedule parsing (including poorly formatted ones)
    - Door/window schedule parsing
    - MEP fixture counts from M, E, P sheets
    - Area calculations from annotated plans
    - Title block information
    """

    async def extract_all(self, sheets: List[DrawingSheet]) -> ExtractedProject:
        """Main extraction — runs all sub-extractors in parallel."""
        project = ExtractedProject()

        # Group sheets by discipline
        arch_sheets = [s for s in sheets if s.discipline == "arch"]
        mep_sheets  = [s for s in sheets if s.discipline == "mep"]

        # Run in parallel
        results = await asyncio.gather(
            self._extract_title_block(sheets[0] if sheets else None),
            self._extract_rooms(arch_sheets),
            self._extract_door_window_schedule(arch_sheets),
            self._extract_mep_schedule(mep_sheets),
            self._extract_areas(arch_sheets),
            return_exceptions=True,
        )

        if not isinstance(results[0], Exception) and results[0]:
            project.title_block = results[0]
        if not isinstance(results[1], Exception) and results[1]:
            project.rooms = results[1]
        if not isinstance(results[2], Exception) and results[2]:
            project.doors_windows = results[2]
        if not isinstance(results[3], Exception) and results[3]:
            project.mep_schedules = results[3]
        if not isinstance(results[4], Exception) and results[4]:
            areas = results[4]
            project.gross_floor_area = areas.get("gfa", 0)
            project.net_floor_area   = areas.get("nfa", 0)

        project.sheets = sheets
        return project

    async def _extract_rooms(self, sheets: List[DrawingSheet]) -> List[Room]:
        if not sheets:
            return []

        combined = "\n\n---\n\n".join(s.raw_markdown[:3000] for s in sheets[:5])

        schema = """{"rooms": [{"name": "room name", "area": 0.0, "floor_finish": "lvt|carpet|tile|epoxy|screed|concrete", "ceiling_finish": "standard|acoustic|exposed|feature", "wall_finish": "painted|tiled|acoustic_panel|feature", "is_wet": false, "height": 2.7}]}"""

        result = await self._llm_extract(
            combined,
            "Extract the room schedule from these architectural drawings. "
            "List every room/space with its area in m², floor finish, ceiling type, and wall finish. "
            "Mark wet areas (bathrooms, kitchens, studios with drainage) as is_wet=true.",
            schema,
            model=FAST_MODEL,
        )

        rooms = []
        for r in result.get("rooms", []):
            rooms.append(Room(
                name=r.get("name", "Unknown Room"),
                area=float(r.get("area", 0)),
                height=float(r.get("height", 2.7)),
                floor_finish=r.get("floor_finish", ""),
                ceiling_finish=r.get("ceiling_finish", "standard"),
                wall_finish=r.get("wall_finish", "painted"),
                is_wet=bool(r.get("is_wet", False)),
            ))

        return rooms

    async def _extract_door_window_schedule(self, sheets: List[DrawingSheet]) -> List[DoorWindow]:
        # Find the door/window schedule sheet
        schedule_sheets = [s for s in sheets if "SCHEDULE" in s.title.upper() or "DOOR" in s.title.upper()]
        if not schedule_sheets:
            schedule_sheets = sheets

        combined = "\n\n".join(s.raw_markdown[:2000] for s in schedule_sheets[:3])

        schema = """{"items": [{"ref": "D1", "type": "hollow_core|solid|fire_60|fire_120|acoustic|glass|sliding", "width": 0.9, "height": 2.1, "count": 1, "fire_rating": "", "notes": ""}]}"""

        result = await self._llm_extract(
            combined,
            "Extract the complete door and window schedule. "
            "Include door reference, type, dimensions (width × height in metres), count, and fire rating if shown.",
            schema,
            model=FAST_MODEL,
        )

        items = []
        for item in result.get("items", []):
            items.append(DoorWindow(
                ref=item.get("ref", ""),
                type_=item.get("type", "solid"),
                width=float(item.get("width", 0.9)),
                height=float(item.get("height", 2.1)),
                count=int(item.get("count", 1)),
                fire_rating=item.get("fire_rating", ""),
                notes=item.get("notes", ""),
            ))

        return items

    async def _extract_mep_schedule(self, sheets: List[DrawingSheet]) -> List[MEPSchedule]:
        if not sheets:
            return []

        combined = "\n\n".join(s.raw_markdown[:2000] for s in sheets[:6])

        schema = """{"items": [{"trade": "plumbing|electrical|hvac|fire", "item": "fixture name", "count": 0, "unit": "no|m²|kW|point"}]}"""

        result = await self._llm_extract(
            combined,
            "Extract all MEP (mechanical, electrical, plumbing, fire) fixture counts and equipment schedules. "
            "Include: WC, WHB, urinals, DB boards, lighting points, data points, HVAC units, sprinkler heads, "
            "fire detectors, extract fans, and any other MEP items listed.",
            schema,
            model=FAST_MODEL,
        )

        schedules = []
        for item in result.get("items", []):
            schedules.append(MEPSchedule(
                trade=item.get("trade", "electrical"),
                item=item.get("item", ""),
                count=int(item.get("count", 0)),
                unit=item.get("unit", "no"),
            ))

        return schedules

    async def _extract_areas(self, sheets: List[DrawingSheet]) -> Dict[str, float]:
        combined = "\n\n".join(s.raw_markdown[:3000] for s in sheets[:4])

        schema = """{"gfa": 0.0, "nfa": 0.0, "mezzanine_area": 0.0, "external_wall_length": 0.0, "number_of_floors": 1, "building_type": "office|retail|residential|industrial|mixed"}"""

        return await self._llm_extract(
            combined,
            "Extract gross floor area (GFA), net floor area (NFA), mezzanine area if any, "
            "external wall perimeter length in metres, number of floors, and building/fitout type.",
            schema,
            model=FAST_MODEL,
        )

    async def _extract_title_block(self, sheet: Optional[DrawingSheet]) -> TitleBlock:
        if not sheet:
            return TitleBlock()

        schema = """{"project_name": "", "project_number": "", "client": "", "address": "", "architect": "", "date": "", "revision": ""}"""

        result = await self._llm_extract(
            sheet.raw_markdown[:2000],
            "Extract the title block information from this drawing.",
            schema,
            model=FAST_MODEL,
        )

        return TitleBlock(
            project_name=result.get("project_name", ""),
            project_number=result.get("project_number", ""),
            client=result.get("client", ""),
            address=result.get("address", ""),
            architect=result.get("architect", ""),
            date=result.get("date", ""),
            revision=result.get("revision", ""),
        )

    async def _llm_extract(self, text: str, instruction: str, schema: str, model: str = FAST_MODEL) -> Dict:
        """One structured read of drawing text, through core/llm_router: local-first, the
        Cloudflare Access headers the router installs, and a logged cloud retry when the local
        call fails. 2026-10-31: was a raw httpx POST to Ollama that returned {} in production —
        no rooms, GFA 0. Nothing is priced from it unreviewed (vula/takeoff/accuracy.py)."""
        from core.prompt_safety import UNTRUSTED_CONTENT_RULE
        messages = [
            {"role": "system", "content": UNTRUSTED_CONTENT_RULE +
                "You extract quantities from architectural drawings. Copy numbers exactly as "
                "printed; never estimate a figure that isn't on the drawing. Return ONLY valid JSON."},
            {"role": "user", "content": f"{instruction}\n\nDrawing content:\n{text[:6000]}\n\n"
                                        f"Return ONLY valid JSON matching this schema:\n{schema}"},
        ]
        try:
            import litellm
            from core.llm_router import complete_local_first, resolve_generation_route
            litellm.drop_params = True
            route = await resolve_generation_route(model, task_type="takeoff_extraction", messages=messages)
            # No timeout here: complete_local_first sets the local one (a second one is a TypeError
            # that silently sent every call to the cloud).
            resp, _ = await complete_local_first(route, task_type="takeoff_extraction", messages=messages,
                                                 temperature=0.1, max_tokens=2048)
            raw = resp.choices[0].message.content or ""
            raw = re.sub(r"<think>.*?</think>", "", raw, flags=re.DOTALL).strip()
            match = re.search(r"\{.*\}", raw, re.DOTALL)
            if match:
                return json.loads(match.group())
        except Exception as e:
            logger.error(f"Plan extraction failed: {e}")
        return {}


# ─── Extraction quality (2026-08 accuracy audit) ──────────────────────────────
# gross_floor_area and the room schedule are extracted by two SEPARATE LLM calls
# (_extract_areas / _extract_rooms) and should roughly agree — the same "don't trust
# confidence, cross-check independently-derived numbers" discipline as
# vula/commerce/extraction_quality.py and bank_rec.reconciliation_ok(). Previously nothing
# checked this: a hallucinated GFA (or a room schedule missing half the building) fed
# straight into BOQGenerator, which prices demolition/finishes/etc. directly off GFA —
# a wrong number here silently becomes a wrong quoted cost, with "confidence" that was
# never more than a hardcoded constant per extraction method (0.90 for text, 0.70 for OCR),
# not a measured signal.

def geometry_reconciled(project: "ExtractedProject") -> Tuple[bool, List[str]]:
    """Cross-checks independently-extracted geometry. Returns (ok, warnings) — never blocks
    anything, matches the soft-caveat pattern used everywhere else this session."""
    warnings: List[str] = []
    gfa = project.gross_floor_area or 0
    room_sum = sum(r.area for r in project.rooms)
    if gfa > 0 and room_sum > 0:
        ratio = room_sum / gfa
        if ratio > 1.15:
            warnings.append(
                f"Room areas sum to {room_sum:.0f}m² but the extracted GFA is only "
                f"{gfa:.0f}m² — one of these was likely misread.")
        elif ratio < 0.35:
            warnings.append(
                f"Room areas sum to only {room_sum:.0f}m² of a {gfa:.0f}m² GFA — the room "
                f"schedule may be missing rooms.")
    readable = [s for s in project.sheets if s.status == "read"]
    if readable and not project.rooms:
        warnings.append(
            "Sheets were read successfully but no rooms were extracted — check the "
            "drawing set has a legible room schedule.")
    return (len(warnings) == 0, warnings)


def assess(project: "ExtractedProject", tenant_id: str = "", history: Optional[float] = None,
           use_history: bool = True) -> "ExtractedProject":
    """Measured confidence + the review gate (vula/takeoff/accuracy.py), in place."""
    ok, warnings = geometry_reconciled(project)
    if not ok:
        project.extraction_notes.extend(warnings)
        for w in warnings:
            logger.warning("[%s] plan reading quality check: %s", tenant_id, w)
    project.confidence_overall, project.confidence_signals = measured_confidence(
        project, history=history, use_history=use_history)
    reasons = review_reasons(project, warnings)
    project.review = {"status": "pending" if reasons else "not_required", "reasons": reasons}
    return project


# ─── Main PlanReader ──────────────────────────────────────────────────────────

class PlanReader:
    """
    Main entry point — reads any drawing format and returns ExtractedProject.
    
    Usage:
        reader = PlanReader(tenant_id="digg_001")
        project = await reader.read(Path("plans.pdf"))
        # project.rooms, project.doors_windows, project.mep_schedules etc.
    """

    def __init__(self, tenant_id: str = "default"):
        self.tenant_id = tenant_id
        self.pdf_reader = PDFReader()
        self.dxf_reader = DXFReader()
        self.intelligence = DrawingIntelligence()

    async def read(self, file_path: Path) -> ExtractedProject:
        suffix = file_path.suffix.lower()
        started = time.time()

        logger.info(f"[{self.tenant_id}] Reading plans: {file_path.name}")

        sheets = []
        rooms_from_vector = []
        doors_from_vector = []

        if suffix == ".pdf":
            sheets = await self.pdf_reader.read(file_path)

        elif suffix == ".dxf":
            rooms_from_vector, doors_from_vector = await self.dxf_reader.read(file_path)
            u = self.dxf_reader.last_units
            sheets = [DrawingSheet(
                sheet_number="DXF001", title="DXF Drawing",
                scale=f"true size ({u.get('units', '?')})", discipline="arch",
                raw_text="", raw_markdown="DXF file — geometry extracted directly",
                status="read", source="dxf",
                scale_ratio=1.0 if u else None,
                scale_method=str(u.get("method", "")),
                scale_verified_by="dxf_header" if u.get("verified") else "",
            )]

        elif suffix in (".png", ".jpg", ".jpeg", ".tiff"):
            # Single scanned image
            img_path = file_path
            markdown = await self.pdf_reader._ocr_image(img_path)
            sheets = [DrawingSheet(
                sheet_number="IMG001", title="Scanned Drawing",
                scale="unknown", discipline="arch",
                raw_text="", raw_markdown=markdown,
                status="read", source="image",
            )]

        else:
            logger.warning(f"Unsupported format: {suffix}")
            return ExtractedProject(extraction_notes=[f"Unsupported format: {suffix}"])

        if suffix == ".dxf":
            # The geometry IS the reading: no text for a model to read, and asking one anyway
            # only invites an invented GFA.
            project = ExtractedProject(sheets=sheets, rooms=rooms_from_vector,
                                       doors_windows=doors_from_vector,
                                       gross_floor_area=self.dxf_reader.last_outline_area,
                                       external_walls_m=self.dxf_reader.last_outline_perimeter)
            project.deterministic_rooms = [(r.name, r.area) for r in rooms_from_vector]
            if self.dxf_reader.last_unlabelled:
                project.extraction_notes.append(
                    f"{self.dxf_reader.last_unlabelled} closed shape(s) have no room label inside.")
        else:
            project = await self.intelligence.extract_all(sheets)
            project.ai_rooms = list(project.rooms)
            # The room schedule's printed areas, read without a model — the second reading the
            # AI's rooms are checked against.
            arch_text = "\n".join(s.raw_markdown or s.raw_text for s in sheets if s.discipline == "arch")
            project.deterministic_rooms = schedule_rooms_from_text(arch_text)
            if not project.rooms and project.deterministic_rooms:
                project.rooms = [Room(name=n, area=a) for n, a in project.deterministic_rooms]

        assess(project, self.tenant_id)

        elapsed = round(time.time() - started, 1)
        logger.info(f"[{self.tenant_id}] Plan reading complete in {elapsed}s: "
                    f"{len(project.rooms)} rooms, {len(project.doors_windows)} openings, "
                    f"GFA={project.gross_floor_area}m²")

        return project
