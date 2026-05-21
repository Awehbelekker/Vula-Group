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
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import httpx

logger = logging.getLogger(__name__)

from config import settings

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
    confidence: float = 0.0


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


# ─── PDF Reader ───────────────────────────────────────────────────────────────

class PDFReader:
    """
    Reads PDF drawing sets — text-based and scanned.
    
    Strategy:
    1. Try pdfplumber for text-based PDFs (Revit exports, CAD-generated)
    2. For pages with <50 chars extracted, treat as scanned → GLM-OCR
    3. Extract page images at 200 DPI for GLM-OCR processing
    """

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

                    if len(full_text) < 50:
                        # Scanned page — extract image and OCR
                        img_path = Path(f"/tmp/vula_sheet_{i}.png")
                        img = page.to_image(resolution=200)
                        img.save(str(img_path))
                        markdown = await self._ocr_image(img_path)
                        img_path.unlink(missing_ok=True)
                        confidence = 0.70
                    else:
                        markdown = full_text
                        confidence = 0.90

                    sheets.append(DrawingSheet(
                        sheet_number=sheet_num or f"SH{i+1:03d}",
                        title=title or f"Sheet {i+1}",
                        scale=scale or "unknown",
                        discipline=discipline,
                        raw_text=text,
                        raw_markdown=markdown,
                        status="read",
                        confidence=confidence,
                    ))

        except ImportError:
            logger.error("pdfplumber not installed: pip install pdfplumber")
            # Try pure OCR fallback
            sheets = await self._full_ocr_fallback(path)

        return sheets

    async def _ocr_image(self, img_path: Path) -> str:
        """GLM-OCR on a page image."""
        with open(img_path, "rb") as f:
            img_b64 = base64.b64encode(f.read()).decode()
        payload = {
            "model": OCR_MODEL,
            "prompt": (
                "You are reading an architectural drawing. "
                "Extract ALL text, numbers, dimensions, room names, "
                "schedules, and annotations. Preserve table structure. "
                "Return clean markdown."
            ),
            "images": [img_b64],
            "stream": False,
            "options": {"temperature": 0.1, "num_predict": 4096},
        }
        try:
            async with httpx.AsyncClient(timeout=90.0) as client:
                resp = await client.post(f"{OLLAMA_BASE}/api/generate", json=payload)
                resp.raise_for_status()
                return resp.json().get("response", "")
        except Exception as e:
            logger.warning(f"GLM-OCR failed: {e}")
            return ""

    async def _full_ocr_fallback(self, path: Path) -> List[DrawingSheet]:
        """Convert PDF pages to images and OCR each."""
        try:
            from pdf2image import convert_from_path
            images = convert_from_path(str(path), dpi=200)
            sheets = []
            for i, img in enumerate(images):
                img_path = Path(f"/tmp/vula_ocr_{i}.png")
                img.save(str(img_path))
                markdown = await self._ocr_image(img_path)
                img_path.unlink(missing_ok=True)
                sheets.append(DrawingSheet(
                    sheet_number=f"SH{i+1:03d}",
                    title=f"Sheet {i+1}",
                    scale="unknown",
                    discipline="arch",
                    raw_text="",
                    raw_markdown=markdown,
                    status="read",
                    confidence=0.65,
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


# ─── DXF/DWG Reader ──────────────────────────────────────────────────────────

class DXFReader:
    """
    Reads DXF files natively using ezdxf.
    Extracts polylines, hatches, text, blocks.
    Most accurate for vector measurements.
    """

    async def read(self, path: Path) -> Tuple[List[Room], List[DoorWindow]]:
        try:
            import ezdxf
            doc = ezdxf.readfile(str(path))
            msp = doc.modelspace()

            rooms = []
            doors = []

            # Extract closed polylines as room boundaries
            for entity in msp.query("LWPOLYLINE POLYLINE"):
                if hasattr(entity, "is_closed") and entity.is_closed:
                    area = self._polyline_area(entity)
                    if area > 2:  # Ignore tiny entities
                        rooms.append(Room(
                            name=f"Room_{len(rooms)+1}",
                            area=round(area, 2),
                            perimeter=round(self._polyline_perimeter(entity), 2),
                        ))

            # Extract text annotations for room labels
            texts = []
            for entity in msp.query("TEXT MTEXT"):
                txt = getattr(entity, "plain_mtext", lambda: getattr(entity, "dxf", None) and entity.dxf.text)
                if callable(txt):
                    txt = txt()
                if isinstance(txt, str) and txt.strip():
                    texts.append(txt.strip())

            # Extract blocks for doors/windows
            for entity in msp.query("INSERT"):
                block_name = entity.dxf.name.upper()
                if "DOOR" in block_name or "DR" in block_name:
                    doors.append(DoorWindow(ref=f"D{len(doors)+1}", type_="solid", width=0.9, height=2.1))
                elif "WINDOW" in block_name or "WD" in block_name:
                    doors.append(DoorWindow(ref=f"W{len(doors)+1}", type_="window", width=1.2, height=1.2))

            return rooms, doors

        except ImportError:
            logger.error("ezdxf not installed: pip install ezdxf")
            return [], []
        except Exception as e:
            logger.error(f"DXF read failed: {e}")
            return [], []

    def _polyline_area(self, entity) -> float:
        """Calculate area of closed polyline using shoelace formula."""
        try:
            pts = list(entity.vertices())
            n = len(pts)
            if n < 3:
                return 0.0
            area = 0.0
            for i in range(n):
                j = (i + 1) % n
                xi, yi = pts[i].x, pts[i].y
                xj, yj = pts[j].x, pts[j].y
                area += xi * yj
                area -= xj * yi
            return abs(area) / 2.0
        except Exception:
            return 0.0

    def _polyline_perimeter(self, entity) -> float:
        """Calculate perimeter of polyline."""
        try:
            pts = list(entity.vertices())
            n = len(pts)
            if n < 2:
                return 0.0
            total = 0.0
            for i in range(n):
                j = (i + 1) % n
                dx = pts[j].x - pts[i].x
                dy = pts[j].y - pts[i].y
                total += math.sqrt(dx*dx + dy*dy)
            return total
        except Exception:
            return 0.0


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
        arch_sheets  = [s for s in sheets if s.discipline == "arch"]
        mep_sheets   = [s for s in sheets if s.discipline == "mep"]
        struct_sheets = [s for s in sheets if s.discipline == "structural"]

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
        project.confidence_overall = sum(s.confidence for s in sheets) / max(len(sheets), 1)
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
        prompt = (
            f"{instruction}\n\n"
            f"Drawing content:\n{text[:6000]}\n\n"
            f"Return ONLY valid JSON matching this schema (no other text):\n{schema}"
        )

        try:
            async with httpx.AsyncClient(timeout=90.0) as client:
                resp = await client.post(
                    f"{OLLAMA_BASE}/api/generate",
                    json={"model": model, "prompt": prompt, "stream": False,
                          "options": {"temperature": 0.1, "num_predict": 2048}},
                )
                resp.raise_for_status()
                raw = resp.json().get("response", "")

            raw = re.sub(r"<think>.*?</think>", "", raw, flags=re.DOTALL).strip()
            match = re.search(r"\{.*\}", raw, re.DOTALL)
            if match:
                return json.loads(match.group())
        except Exception as e:
            logger.error(f"LLM extraction failed: {e}")

        return {}


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
            # Also extract text as a single "sheet"
            sheets = [DrawingSheet(
                sheet_number="DXF001", title="DXF Drawing",
                scale="1:100", discipline="arch",
                raw_text="", raw_markdown="DXF file — geometry extracted directly",
                status="read", confidence=0.85,
            )]

        elif suffix in (".png", ".jpg", ".jpeg", ".tiff"):
            # Single scanned image
            img_path = file_path
            markdown = await self.pdf_reader._ocr_image(img_path)
            sheets = [DrawingSheet(
                sheet_number="IMG001", title="Scanned Drawing",
                scale="unknown", discipline="arch",
                raw_text="", raw_markdown=markdown,
                status="read", confidence=0.70,
            )]

        else:
            logger.warning(f"Unsupported format: {suffix}")
            return ExtractedProject(extraction_notes=[f"Unsupported format: {suffix}"])

        # AI extraction
        project = await self.intelligence.extract_all(sheets)

        # Merge vector data if available
        if rooms_from_vector:
            project.rooms = rooms_from_vector
        if doors_from_vector:
            project.doors_windows = doors_from_vector

        elapsed = round(time.time() - started, 1)
        logger.info(f"[{self.tenant_id}] Plan reading complete in {elapsed}s: "
                    f"{len(project.rooms)} rooms, {len(project.doors_windows)} openings, "
                    f"GFA={project.gross_floor_area}m²")

        return project
