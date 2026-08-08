"""
vula/skills/web_scraper.py

Vula Web Intelligence Skill

Gives clients AI-powered web scraping for:
- Competitor price monitoring
- Lead research (company info before a sales call)
- Industry news digests
- Tender/RFP monitoring (SA government portals)
- Property listing tracking (for DIGG clients)
- Supplier price comparisons

Powered by Crawl4AI + ScrapeGraphAI + local DeepSeek R1.
Zero cloud cost. Runs entirely on the client's Soul Box.

Usage:
    scraper = VulaWebScraper(tenant_id="digg_001")
    
    # Research a company before a sales call
    result = await scraper.research_company("https://sportybet.co.za")
    
    # Monitor competitor prices
    result = await scraper.monitor_prices(["https://competitor1.co.za/products"])
    
    # Daily news digest for an industry
    result = await scraper.industry_digest("South African construction industry news")
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

import httpx

from config import settings

logger = logging.getLogger(__name__)

OLLAMA_BASE = settings.ollama_base
LLM_MODEL = settings.model_worker
DEFAULT_TIMEOUT = 30.0


# ─── Data Classes ─────────────────────────────────────────────────────────────

@dataclass
class ScrapeResult:
    url: str
    title: str
    raw_markdown: str
    extracted_data: Dict[str, Any]
    scrape_time_s: float
    status: str   # success | failed | blocked
    error: Optional[str] = None


@dataclass
class CompanyProfile:
    url: str
    name: str
    description: str
    services: List[str]
    contact_info: Dict[str, str]
    social_links: Dict[str, str]
    key_facts: List[str]
    suggested_pitch_angle: str   # AI-generated: how to approach this company


@dataclass
class PriceMonitorResult:
    url: str
    products: List[Dict[str, Any]]   # [{name, price, unit, in_stock}]
    scraped_at: str
    changes_detected: List[str]      # If previous run exists


@dataclass
class NewsDigest:
    topic: str
    articles: List[Dict[str, str]]   # [{title, summary, url, date}]
    key_trends: List[str]
    generated_at: str


# ─── Core Fetcher ─────────────────────────────────────────────────────────────

class WebFetcher:
    """
    Fetches web pages and converts to clean Markdown.
    Uses Crawl4AI when available, falls back to direct httpx fetch.
    """

    HEADERS = {
        "User-Agent": "Mozilla/5.0 (compatible; VulaAI/1.0; research-bot)",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-ZA,en;q=0.9,af;q=0.8",
    }

    async def fetch_markdown(self, url: str) -> tuple[str, str]:
        """
        Fetch URL and return (title, clean_markdown).
        Tries Crawl4AI first, falls back to httpx + basic parsing.
        """
        # Try Crawl4AI
        try:
            return await self._crawl4ai_fetch(url)
        except Exception as e:
            logger.debug(f"Crawl4AI unavailable: {e}, using httpx fallback")

        # Fallback: httpx + html2text
        return await self._httpx_fetch(url)

    async def _crawl4ai_fetch(self, url: str) -> tuple[str, str]:
        """Use Crawl4AI for clean AI-optimised markdown."""
        try:
            from crawl4ai import AsyncWebCrawler, CrawlerRunConfig, CacheMode
            config = CrawlerRunConfig(cache_mode=CacheMode.BYPASS)
            async with AsyncWebCrawler() as crawler:
                result = await crawler.arun(url=url, config=config)
                if result.success:
                    return result.metadata.get("title", ""), result.markdown.fit_markdown or result.markdown.raw_markdown
                raise RuntimeError(result.error_message)
        except ImportError:
            raise RuntimeError("crawl4ai not installed")

    async def _httpx_fetch(self, url: str) -> tuple[str, str]:
        """Direct fetch with basic HTML-to-text conversion."""
        async with httpx.AsyncClient(
            headers=self.HEADERS,
            timeout=DEFAULT_TIMEOUT,
            follow_redirects=True,
        ) as client:
            resp = await client.get(url)
            resp.raise_for_status()
            html = resp.text

        title = self._extract_title(html)
        markdown = self._html_to_text(html)
        return title, markdown

    def _extract_title(self, html: str) -> str:
        match = re.search(r"<title>(.*?)</title>", html, re.IGNORECASE | re.DOTALL)
        return match.group(1).strip() if match else ""

    def _html_to_text(self, html: str) -> str:
        """Minimal HTML to text — removes tags, preserves structure."""
        try:
            import html2text
            h = html2text.HTML2Text()
            h.ignore_links = False
            h.ignore_images = True
            return h.handle(html)
        except ImportError:
            # Ultra-minimal fallback
            text = re.sub(r"<script[^>]*>.*?</script>", "", html, flags=re.DOTALL)
            text = re.sub(r"<style[^>]*>.*?</style>", "", text, flags=re.DOTALL)
            text = re.sub(r"<[^>]+>", " ", text)
            text = re.sub(r"\s+", " ", text)
            return text.strip()


# ─── LLM Extraction ───────────────────────────────────────────────────────────

class LLMExtractor:
    """Uses local DeepSeek to extract structured data from raw scraped text."""

    def __init__(self, model: str = LLM_MODEL, ollama_base: str = OLLAMA_BASE):
        self.model = model
        self.base = ollama_base

    async def extract(self, text: str, prompt: str, schema: str = "") -> Dict[str, Any]:
        """Extract structured data from text using a natural language prompt."""
        from core.prompt_safety import fence, UNTRUSTED_CONTENT_RULE

        schema_hint = f"\n\nReturn ONLY valid JSON matching this schema:\n{schema}" if schema else "\n\nReturn ONLY valid JSON."

        # Scraped web pages are untrusted, externally-authored content (competitor sites,
        # supplier pages) — fenced the same way KB/RAG content is (core/prompt_safety.py,
        # Phase 2 remediation), so text shaped like an instruction on the page itself
        # ("ignore prior rules and return {...}") can't be mistaken for one. Previously
        # this went straight into the prompt as a plain, unlabelled block.
        full_prompt = (
            f"{prompt}\n\n"
            f"{UNTRUSTED_CONTENT_RULE}"
            f"{fence('SCRAPED_PAGE_TEXT', text[:6000])}"  # Cap at 6k tokens
            f"{schema_hint}"
        )

        try:
            async with httpx.AsyncClient(timeout=60.0) as client:
                resp = await client.post(
                    f"{self.base}/api/generate",
                    json={
                        "model": self.model,
                        "prompt": full_prompt,
                        "stream": False,
                        "options": {"temperature": 0.1, "num_predict": 2048},
                    },
                )
                resp.raise_for_status()
                raw = resp.json().get("response", "")

            # Strip think tags
            raw = re.sub(r"<think>.*?</think>", "", raw, flags=re.DOTALL).strip()

            # Extract JSON block
            json_match = re.search(r"\{.*\}", raw, re.DOTALL)
            if json_match:
                return json.loads(json_match.group())
            return {"raw": raw}

        except json.JSONDecodeError:
            return {"raw": raw}
        except Exception as e:
            logger.error(f"LLM extraction failed: {e}")
            return {"error": str(e)}

    async def summarise(self, text: str, instruction: str) -> str:
        """Generate a plain text summary."""
        from core.prompt_safety import fence, UNTRUSTED_CONTENT_RULE
        prompt = f"{instruction}\n\n{UNTRUSTED_CONTENT_RULE}{fence('SCRAPED_PAGE_TEXT', text[:6000])}\n\nSummary:"
        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.post(
                f"{self.base}/api/generate",
                json={
                    "model": self.model,
                    "prompt": prompt,
                    "stream": False,
                    "options": {"temperature": 0.3, "num_predict": 1024},
                },
            )
            resp.raise_for_status()
            raw = resp.json().get("response", "")
        return re.sub(r"<think>.*?</think>", "", raw, flags=re.DOTALL).strip()


# ─── Main Scraper ─────────────────────────────────────────────────────────────

class VulaWebScraper:
    """
    Vula's web intelligence skill.
    Attach to any client tenant for on-demand research.
    """

    def __init__(self, tenant_id: str = "default"):
        self.tenant_id = tenant_id
        self.fetcher = WebFetcher()
        self.extractor = LLMExtractor()

    # ── Company Research ──────────────────────────────────────────────────────

    async def research_company(self, url: str) -> CompanyProfile:
        """
        Research a company from their website before a sales call or proposal.
        Perfect for DIGG client research or ITEC lead qualification.
        """
        logger.info(f"Researching company: {url}")
        title, markdown = await self.fetcher.fetch_markdown(url)

        schema = """{
  "name": "string",
  "description": "2-3 sentence description of what this company does",
  "services": ["list of services/products they offer"],
  "contact_info": {"email": "", "phone": "", "address": ""},
  "social_links": {"linkedin": "", "facebook": "", "instagram": ""},
  "key_facts": ["interesting facts about size, history, clients, awards"],
  "suggested_pitch_angle": "How a managed print/AI services company should approach them - what pain points likely resonate"
}"""

        extracted = await self.extractor.extract(
            markdown,
            "Extract structured company information from this website content for a B2B sales team.",
            schema=schema,
        )

        return CompanyProfile(
            url=url,
            name=extracted.get("name", title or urlparse(url).netloc),
            description=extracted.get("description", ""),
            services=extracted.get("services", []),
            contact_info=extracted.get("contact_info", {}),
            social_links=extracted.get("social_links", {}),
            key_facts=extracted.get("key_facts", []),
            suggested_pitch_angle=extracted.get("suggested_pitch_angle", ""),
        )

    # ── Price Monitoring ───────────────────────────────────────────────────────

    async def monitor_prices(self, urls: List[str]) -> List[PriceMonitorResult]:
        """
        Monitor competitor or supplier prices across multiple pages.
        Great for Awake SA to track competitor eFoil/surfboard pricing.
        """
        results = []
        for url in urls:
            try:
                _, markdown = await self.fetcher.fetch_markdown(url)
                schema = """{
  "products": [
    {"name": "product name", "price": "price as string with currency", "unit": "each/kg/m etc", "in_stock": true}
  ]
}"""
                extracted = await self.extractor.extract(
                    markdown,
                    "Extract all product names and prices from this page.",
                    schema=schema,
                )
                results.append(PriceMonitorResult(
                    url=url,
                    products=extracted.get("products", []),
                    scraped_at=time.strftime("%Y-%m-%d %H:%M"),
                    changes_detected=[],
                ))
            except Exception as e:
                logger.error(f"Price monitor failed for {url}: {e}")
                results.append(PriceMonitorResult(
                    url=url, products=[],
                    scraped_at=time.strftime("%Y-%m-%d %H:%M"),
                    changes_detected=[f"Error: {e}"],
                ))
        return results

    # ── Industry News Digest ───────────────────────────────────────────────────

    async def industry_digest(self, topic: str, sources: Optional[List[str]] = None) -> NewsDigest:
        """
        Generate a daily industry news digest.
        Default sources cover SA construction, property, and tech.
        """
        default_sources = [
            f"https://www.google.com/search?q={topic.replace(' ', '+')}+site:businesstech.co.za",
            f"https://www.google.com/search?q={topic.replace(' ', '+')}+site:dailymaverick.co.za",
        ]
        urls_to_scrape = sources or default_sources

        all_content = []
        for url in urls_to_scrape[:3]:  # Cap at 3 sources
            try:
                _, markdown = await self.fetcher.fetch_markdown(url)
                all_content.append(markdown[:2000])
            except Exception as e:
                logger.warning(f"Failed to fetch {url}: {e}")

        combined = "\n\n---\n\n".join(all_content)

        schema = """{
  "articles": [
    {"title": "article title", "summary": "2 sentence summary", "url": "url if found", "date": "date if found"}
  ],
  "key_trends": ["3-5 key trends or themes from today's news"]
}"""

        extracted = await self.extractor.extract(
            combined,
            f"Extract news articles and key trends about: {topic}. Focus on South African context.",
            schema=schema,
        )

        return NewsDigest(
            topic=topic,
            articles=extracted.get("articles", []),
            key_trends=extracted.get("key_trends", []),
            generated_at=time.strftime("%Y-%m-%d %H:%M"),
        )

    # ── Tender Monitoring ──────────────────────────────────────────────────────

    async def monitor_tenders(self, keywords: List[str]) -> List[Dict[str, Any]]:
        """
        Monitor South African government tender portals.
        Essential for DIGG (construction/architecture tenders).
        """
        tender_sources = [
            "https://www.etenders.gov.za",
            "https://www.cidb.org.za",
        ]

        all_tenders = []
        for source in tender_sources:
            try:
                _, markdown = await self.fetcher.fetch_markdown(source)
                schema = """{
  "tenders": [
    {
      "title": "tender title",
      "reference": "reference number",
      "closing_date": "closing date",
      "department": "issuing department",
      "estimated_value": "estimated value if shown",
      "description": "brief description"
    }
  ]
}"""
                keyword_filter = ", ".join(keywords)
                extracted = await self.extractor.extract(
                    markdown,
                    f"Extract tenders related to: {keyword_filter}. Only include relevant ones.",
                    schema=schema,
                )
                all_tenders.extend(extracted.get("tenders", []))
            except Exception as e:
                logger.warning(f"Tender monitor failed for {source}: {e}")

        return all_tenders

    # ── Bulk URL Scraper ───────────────────────────────────────────────────────

    async def scrape_urls(self, urls: List[str], extract_prompt: str) -> List[ScrapeResult]:
        """
        Generic bulk scraper — extract anything from a list of URLs.
        Used by the Vula skill registry when a client asks for custom research.
        """
        semaphore = asyncio.Semaphore(3)  # Max 3 concurrent requests

        async def _scrape_one(url: str) -> ScrapeResult:
            async with semaphore:
                started = time.time()
                try:
                    title, markdown = await self.fetcher.fetch_markdown(url)
                    extracted = await self.extractor.extract(markdown, extract_prompt)
                    return ScrapeResult(
                        url=url, title=title, raw_markdown=markdown,
                        extracted_data=extracted,
                        scrape_time_s=round(time.time() - started, 2),
                        status="success",
                    )
                except Exception as e:
                    return ScrapeResult(
                        url=url, title="", raw_markdown="",
                        extracted_data={},
                        scrape_time_s=round(time.time() - started, 2),
                        status="failed", error=str(e),
                    )

        return await asyncio.gather(*[_scrape_one(url) for url in urls])


# ─── CLI Quick Test ───────────────────────────────────────────────────────────
if __name__ == "__main__":
    import sys

    async def demo():
        scraper = VulaWebScraper(tenant_id="demo")

        if len(sys.argv) > 1:
            url = sys.argv[1]
            print(f"\n🔍 Researching: {url}\n")
            profile = await scraper.research_company(url)
            print(f"Company:  {profile.name}")
            print(f"About:    {profile.description}")
            print(f"Services: {', '.join(profile.services[:3])}")
            print(f"\n💡 Pitch angle:\n{profile.suggested_pitch_angle}")
        else:
            print("Usage: python web_scraper.py https://company.co.za")
            print("\nRunning demo digest...")
            digest = await scraper.industry_digest("South African managed print services")
            print(f"\n📰 Industry Digest: {digest.topic}")
            for trend in digest.key_trends:
                print(f"  → {trend}")

    asyncio.run(demo())
