"""
vula/skills/construction_rates_scraper.py

Vula Scout — Construction Material Rate Scraper

Scrapes live material prices from SA suppliers and cost guides.
Runs weekly via n8n → updates the Qdrant knowledge base → 
Vula QS Pro pulls fresh rates instead of static AECOM tables.

Sources:
    - AECOM Africa Cost Guide (estimationqs.com summary)
    - Builders Warehouse (builders.co.za)
    - Cashbuild (cashbuild.co.za)  
    - Italtile (italtile.co.za) — tiles and flooring
    - Buildmate (buildmate.co.za) — materials
    - BER Building Cost Index (ber.ac.za)
    - Rand Tools (randtools.co.za) — SA-specific calculators

Schedule: n8n cron — every Monday 06:00 SAST
Output: JSON to Qdrant + SQLite cache + WhatsApp summary to Judy

Usage:
    scraper = ConstructionRatesScraper()
    rates = await scraper.run_full_update()
    print(rates.summary)
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import sqlite3
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

import httpx

from config import settings

logger = logging.getLogger(__name__)

OLLAMA_BASE = settings.ollama_base
LLM_MODEL   = settings.model_worker
DB_PATH     = Path.home() / ".vula" / "construction_rates.db"
QDRANT_BASE = settings.qdrant_base
COLLECTION  = "vula_digg_rates"

# ─── Data structures ─────────────────────────────────────────────────────────

@dataclass
class MaterialRate:
    key: str                    # e.g. "tiles_porcelain_600"
    label: str                  # e.g. "Porcelain Tiles 600x600 (laid)"
    unit: str                   # e.g. "m²"
    low: float                  # Lower bound R/unit
    high: float                 # Upper bound R/unit
    mid: float = 0.0            # Auto-calculated
    source: str = ""            # Where scraped from
    scraped_at: str = ""        # ISO timestamp
    change_pct: float = 0.0    # vs previous scrape
    notes: str = ""

    def __post_init__(self):
        if not self.mid:
            self.mid = (self.low + self.high) / 2
        if not self.scraped_at:
            self.scraped_at = datetime.now().isoformat()


@dataclass
class ScrapedCatalogue:
    source_name: str
    source_url: str
    rates: List[MaterialRate] = field(default_factory=list)
    scraped_at: str = field(default_factory=lambda: datetime.now().isoformat())
    status: str = "ok"
    error: Optional[str] = None


@dataclass
class UpdateResult:
    total_rates: int
    updated: int
    new: int
    unchanged: int
    sources_scraped: List[str]
    duration_s: float
    summary: str


# ─── Web fetcher ─────────────────────────────────────────────────────────────

class RateFetcher:
    HEADERS = {
        "User-Agent": "Mozilla/5.0 (compatible; VulaScout/1.0; construction-research)",
        "Accept": "text/html,application/xhtml+xml,*/*;q=0.9",
        "Accept-Language": "en-ZA,en;q=0.9",
    }

    async def fetch(self, url: str, timeout: float = 20.0) -> str:
        """Fetch a URL and return markdown-friendly text."""
        try:
            # Try Crawl4AI first
            return await self._crawl4ai(url)
        except Exception:
            return await self._httpx_fetch(url, timeout)

    async def _crawl4ai(self, url: str) -> str:
        try:
            from crawl4ai import AsyncWebCrawler, CrawlerRunConfig, CacheMode
            config = CrawlerRunConfig(cache_mode=CacheMode.BYPASS)
            async with AsyncWebCrawler() as crawler:
                result = await crawler.arun(url=url, config=config)
                if result.success:
                    return result.markdown.fit_markdown or result.markdown.raw_markdown
            raise RuntimeError("Crawl4AI returned no content")
        except ImportError:
            raise RuntimeError("crawl4ai not installed")

    async def _httpx_fetch(self, url: str, timeout: float) -> str:
        async with httpx.AsyncClient(headers=self.HEADERS, timeout=timeout, follow_redirects=True) as client:
            resp = await client.get(url)
            resp.raise_for_status()
            text = resp.text
            # Strip HTML tags minimally
            text = re.sub(r"<script[^>]*>.*?</script>", "", text, flags=re.DOTALL)
            text = re.sub(r"<style[^>]*>.*?</style>", "", text, flags=re.DOTALL)
            text = re.sub(r"<[^>]+>", " ", text)
            text = re.sub(r"\s+", " ", text)
            return text[:15000]  # Cap for LLM


# ─── LLM extractor ───────────────────────────────────────────────────────────

class RateExtractor:
    def __init__(self):
        self.fetcher = RateFetcher()

    async def extract_rates(self, url: str, source_name: str, prompt: str) -> ScrapedCatalogue:
        """Fetch a page and extract construction rates using local DeepSeek."""
        try:
            content = await self.fetcher.fetch(url)
        except Exception as e:
            logger.warning(f"Failed to fetch {url}: {e}")
            return ScrapedCatalogue(source_name=source_name, source_url=url, status="fetch_failed", error=str(e))

        extraction_prompt = (
            f"{prompt}\n\n"
            f"Page content:\n{content[:8000]}\n\n"
            f"Extract ONLY construction material costs. Return valid JSON array:\n"
            f'[{{"label": "...", "unit": "m²|m³|kg|no|m run|tonne", "low": 000, "high": 000, "notes": "..."}}]\n'
            f"Include only items with actual ZAR prices. Return [] if no prices found.\n"
            f"JSON only, no other text:"
        )

        try:
            # The Ollama tunnel is behind Cloudflare Access — send the service-token headers
            # (same ones llm_router uses) or this direct call is blocked at the edge.
            from core.llm_router import _ollama_headers
            async with httpx.AsyncClient(timeout=60.0) as client:
                resp = await client.post(
                    f"{OLLAMA_BASE}/api/generate",
                    headers=_ollama_headers() or None,
                    json={
                        "model": LLM_MODEL,
                        "prompt": extraction_prompt,
                        "stream": False,
                        "options": {"temperature": 0.1, "num_predict": 2048},
                    },
                )
                resp.raise_for_status()
                raw = resp.json().get("response", "")

            # Strip think tags
            raw = re.sub(r"<think>.*?</think>", "", raw, flags=re.DOTALL).strip()

            # Extract JSON array
            match = re.search(r"\[.*\]", raw, re.DOTALL)
            if not match:
                return ScrapedCatalogue(source_name=source_name, source_url=url, status="no_data")

            items = json.loads(match.group())
            rates = []
            for item in items:
                if not item.get("label") or not item.get("low"):
                    continue
                key = re.sub(r"[^a-z0-9]", "_", item["label"].lower())[:40]
                rates.append(MaterialRate(
                    key=key,
                    label=item.get("label", ""),
                    unit=item.get("unit", "m²"),
                    low=float(item.get("low", 0)),
                    high=float(item.get("high", item.get("low", 0))),
                    source=source_name,
                    notes=item.get("notes", ""),
                ))

            return ScrapedCatalogue(source_name=source_name, source_url=url, rates=rates)

        except (json.JSONDecodeError, Exception) as e:
            logger.error(f"Rate extraction failed for {source_name}: {e}")
            return ScrapedCatalogue(source_name=source_name, source_url=url, status="parse_failed", error=str(e))


# ─── Database ─────────────────────────────────────────────────────────────────

class RatesDatabase:
    def __init__(self, db_path: Path = DB_PATH):
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init()

    def _init(self):
        conn = sqlite3.connect(self.db_path)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS material_rates (
                key TEXT PRIMARY KEY,
                label TEXT NOT NULL,
                unit TEXT,
                low REAL,
                high REAL,
                mid REAL,
                source TEXT,
                scraped_at TEXT,
                prev_mid REAL,
                change_pct REAL,
                notes TEXT
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS scrape_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                source TEXT,
                url TEXT,
                status TEXT,
                rates_extracted INTEGER,
                scraped_at TEXT
            )
        """)
        conn.commit()
        conn.close()

    def upsert(self, rate: MaterialRate) -> str:
        """Insert or update a rate. Returns 'new' | 'updated' | 'unchanged'."""
        conn = sqlite3.connect(self.db_path)
        existing = conn.execute("SELECT mid FROM material_rates WHERE key=?", (rate.key,)).fetchone()
        status = "unchanged"

        if not existing:
            conn.execute(
                "INSERT INTO material_rates VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (rate.key, rate.label, rate.unit, rate.low, rate.high, rate.mid,
                 rate.source, rate.scraped_at, None, 0.0, rate.notes)
            )
            status = "new"
        else:
            prev_mid = existing[0]
            change = ((rate.mid - prev_mid) / prev_mid * 100) if prev_mid else 0
            if abs(change) > 0.5:
                conn.execute("""
                    UPDATE material_rates
                    SET low=?, high=?, mid=?, source=?, scraped_at=?, prev_mid=?, change_pct=?, notes=?
                    WHERE key=?
                """, (rate.low, rate.high, rate.mid, rate.source, rate.scraped_at, prev_mid, change, rate.notes, rate.key))
                rate.change_pct = change
                status = "updated"

        conn.commit()
        conn.close()
        return status

    def get_all(self) -> List[Dict]:
        conn = sqlite3.connect(self.db_path)
        rows = conn.execute("SELECT * FROM material_rates ORDER BY label").fetchall()
        cols = ["key","label","unit","low","high","mid","source","scraped_at","prev_mid","change_pct","notes"]
        conn.close()
        return [dict(zip(cols, row)) for row in rows]

    def get_changes(self, threshold_pct: float = 5.0) -> List[Dict]:
        """Rates that moved significantly since last scrape."""
        conn = sqlite3.connect(self.db_path)
        rows = conn.execute(
            "SELECT * FROM material_rates WHERE ABS(change_pct) > ? ORDER BY ABS(change_pct) DESC",
            (threshold_pct,)
        ).fetchall()
        cols = ["key","label","unit","low","high","mid","source","scraped_at","prev_mid","change_pct","notes"]
        conn.close()
        return [dict(zip(cols, row)) for row in rows]

    def log_scrape(self, catalogue: ScrapedCatalogue):
        conn = sqlite3.connect(self.db_path)
        conn.execute(
            "INSERT INTO scrape_log (source, url, status, rates_extracted, scraped_at) VALUES (?,?,?,?,?)",
            (catalogue.source_name, catalogue.source_url, catalogue.status, len(catalogue.rates), catalogue.scraped_at)
        )
        conn.commit()
        conn.close()


# ─── Main scraper ─────────────────────────────────────────────────────────────

class ConstructionRatesScraper:
    """
    Weekly construction rate scraper for Vula DIGG.

    Scrapes 6 SA sources, extracts ZAR rates using DeepSeek,
    compares against previous week, flags significant movements,
    and sends Judy a WhatsApp digest of what changed.
    """

    SOURCES = [
        {
            "name": "AECOM 2025/26",
            "url": "https://estimationqs.com/building-costs-per-square-metre-in-south-africa-2025-to-2026-aecom-guide/",
            "prompt": "Extract all South African construction cost benchmarks in ZAR per square metre from this AECOM cost guide. Include office, retail, industrial, residential, fitout, parking, MEP rates.",
        },
        {
            "name": "Rand Tools SA",
            "url": "https://randtools.co.za/build",
            "prompt": "Extract all building material costs in South Africa in ZAR from this construction calculator page. Focus on cement, tiles, paint, bricks, concrete per unit rates.",
        },
        {
            "name": "Builders Warehouse",
            "url": "https://www.builders.co.za/Tiles-&-Flooring/Tiles/c/Tiles",
            "prompt": "Extract tile and flooring prices in ZAR per m² from Builders Warehouse. Include product name, size, price per unit or per m².",
        },
        {
            "name": "Cashbuild",
            "url": "https://www.cashbuild.co.za/shop/c/building-materials",
            "prompt": "Extract building material prices in ZAR from Cashbuild. Include cement bags, bricks, sand, stone per unit prices.",
        },
        {
            "name": "Italtile",
            "url": "https://www.italtile.co.za/tiles",
            "prompt": "Extract tile prices per m² in ZAR from Italtile South Africa. Include floor tiles and wall tiles.",
        },
        {
            "name": "SA Construction Cost Index",
            "url": "https://buildingcostsouthafrica.co.za",
            "prompt": "Extract construction cost per square metre benchmarks in ZAR from this South African building cost guide. Include all building types.",
        },
    ]

    def __init__(self):
        self.extractor = RateExtractor()
        self.db = RatesDatabase()

    async def run_full_update(self) -> UpdateResult:
        """Run all scrapers and update the database."""
        started = time.time()
        all_rates = []
        sources_scraped = []

        # Scrape all sources concurrently (with limit)
        semaphore = asyncio.Semaphore(3)

        async def scrape_one(source: dict) -> ScrapedCatalogue:
            async with semaphore:
                logger.info(f"Scraping: {source['name']}")
                catalogue = await self.extractor.extract_rates(
                    source["url"], source["name"], source["prompt"]
                )
                self.db.log_scrape(catalogue)
                return catalogue

        catalogues = await asyncio.gather(*[scrape_one(s) for s in self.SOURCES], return_exceptions=True)

        # Process results
        counts = {"new": 0, "updated": 0, "unchanged": 0}
        for cat in catalogues:
            if isinstance(cat, Exception):
                logger.error(f"Scraper error: {cat}")
                continue
            sources_scraped.append(cat.source_name)
            for rate in cat.rates:
                status = self.db.upsert(rate)
                counts[status] += 1
                all_rates.append(rate)

        # Build summary
        changes = self.db.get_changes(threshold_pct=5.0)
        change_text = ""
        if changes:
            change_text = "\n".join([
                f"  {r['label']}: {r['change_pct']:+.1f}% ({r['prev_mid']:.0f} → {r['mid']:.0f})"
                for r in changes[:5]
            ])

        summary = (
            f"Vula Scout — Construction Rates Update\n"
            f"{'─' * 40}\n"
            f"Scraped: {len(sources_scraped)} sources\n"
            f"Rates: {len(all_rates)} total · {counts['new']} new · {counts['updated']} updated\n"
            f"Duration: {time.time()-started:.1f}s\n"
        )
        if change_text:
            summary += f"\nSignificant changes (>5%):\n{change_text}\n"

        return UpdateResult(
            total_rates=len(all_rates),
            updated=counts["updated"],
            new=counts["new"],
            unchanged=counts["unchanged"],
            sources_scraped=sources_scraped,
            duration_s=round(time.time() - started, 1),
            summary=summary,
        )

    async def get_live_rate(self, material_description: str) -> Optional[Dict]:
        """
        Query the database for a specific material rate.
        Falls back to scraping if not in DB.
        
        Usage: rate = await scraper.get_live_rate("porcelain tiles 600x600")
        """
        all_rates = self.db.get_all()
        desc_lower = material_description.lower()

        # Find best match
        matches = [r for r in all_rates if any(word in r["label"].lower() for word in desc_lower.split())]
        if matches:
            best = max(matches, key=lambda r: sum(1 for w in desc_lower.split() if w in r["label"].lower()))
            return best

        # Not in DB — attempt live scrape from AECOM source
        logger.info(f"Rate not cached, attempting live lookup: {material_description}")
        cat = await self.extractor.extract_rates(
            self.SOURCES[0]["url"],
            "AECOM 2025/26 (live)",
            f"Find the construction cost rate in ZAR for: {material_description}",
        )
        if cat.rates:
            rate = cat.rates[0]
            self.db.upsert(rate)
            return {"label": rate.label, "low": rate.low, "high": rate.high, "mid": rate.mid, "unit": rate.unit}

        return None

    def whatsapp_digest(self) -> str:
        """
        Generate a weekly WhatsApp message for Judy summarising rate changes.
        Sent by n8n on Monday morning.
        """
        changes = self.db.get_changes(threshold_pct=5.0)
        all_rates = self.db.get_all()

        msg = f"📊 *Vula QS — Weekly Rate Update*\n_{datetime.now().strftime('%d %B %Y')}_\n\n"

        if changes:
            msg += f"*{len(changes)} rates moved significantly:*\n"
            for r in changes[:8]:
                emoji = "📈" if r["change_pct"] > 0 else "📉"
                msg += f"{emoji} {r['label']}: {r['change_pct']:+.1f}%\n"
        else:
            msg += "✅ No significant rate changes this week\n"

        msg += f"\n📦 *{len(all_rates)} rates tracked* across AECOM, Builders Warehouse, Cashbuild & Italtile\n"
        msg += "\nOpen Vula QS Pro for the full breakdown → vula.co.za/qs"
        return msg


# ─── FastAPI endpoint (add to vula/api/server.py) ────────────────────────────

"""
Add these to vula/api/server.py:

from vula.skills.construction_rates_scraper import ConstructionRatesScraper

@app.post("/rates/refresh")
async def refresh_rates(background_tasks: BackgroundTasks):
    scraper = ConstructionRatesScraper()
    background_tasks.add_task(scraper.run_full_update)
    return {"status": "queued", "message": "Rate update running in background"}

@app.get("/rates")
async def get_rates():
    scraper = ConstructionRatesScraper()
    return {"rates": scraper.db.get_all(), "last_updated": datetime.now().isoformat()}

@app.get("/rates/changes")
async def get_rate_changes(threshold: float = 5.0):
    scraper = ConstructionRatesScraper()
    return {"changes": scraper.db.get_changes(threshold)}

@app.get("/rates/search")
async def search_rate(q: str):
    scraper = ConstructionRatesScraper()
    rate = await scraper.get_live_rate(q)
    return {"rate": rate, "query": q}
"""

# ─── n8n Workflow JSON (import into your n8n instance) ───────────────────────

N8N_WORKFLOW = {
    "name": "Vula QS — Weekly Rate Scraper",
    "nodes": [
        {
            "name": "Cron — Monday 06:00 SAST",
            "type": "n8n-nodes-base.cron",
            "parameters": {"triggerTimes": {"item": [{"mode": "everyWeek", "weekday": 1, "hour": 6, "minute": 0}]}}
        },
        {
            "name": "Trigger Rate Scraper",
            "type": "n8n-nodes-base.httpRequest",
            "parameters": {"method": "POST", "url": "http://localhost:7437/rates/refresh"}
        },
        {
            "name": "Wait for completion",
            "type": "n8n-nodes-base.wait",
            "parameters": {"amount": 5, "unit": "minutes"}
        },
        {
            "name": "Get changes",
            "type": "n8n-nodes-base.httpRequest",
            "parameters": {"method": "GET", "url": "http://localhost:7437/rates/changes?threshold=5.0"}
        },
        {
            "name": "WhatsApp Digest to Judy",
            "type": "n8n-nodes-base.httpRequest",
            "parameters": {
                "method": "POST",
                "url": "http://localhost:7437/notifications/whatsapp",
                "body": {"to": "JUDY_WHATSAPP_NUMBER", "message": "={{$json.whatsapp_digest}}"}
            }
        }
    ]
}


if __name__ == "__main__":
    async def main():
        print("🔍 Vula Scout — Construction Rate Scraper")
        print("=" * 50)
        scraper = ConstructionRatesScraper()
        print("Running full update (this takes 2–5 minutes)...\n")
        result = await scraper.run_full_update()
        print(result.summary)
        print("\n📱 WhatsApp digest preview:")
        print(scraper.whatsapp_digest())

    asyncio.run(main())
