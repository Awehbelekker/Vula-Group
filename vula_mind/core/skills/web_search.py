"""
core/skills/web_search.py

Web search skill — wires to VulaWebScraper for company research,
SA tender monitoring, and live price/news intelligence.

Detects query intent to pick the right scraper mode:
  - tender keywords → SA tender monitor
  - company/supplier keywords → company research
  - price/rate keywords → price monitor
  - default → news digest
"""
from __future__ import annotations

import logging
import re

from core.skills.base import BaseSkill, SkillInput, SkillOutput

logger = logging.getLogger(__name__)

_TENDER_RE = re.compile(r"\b(tender|bid|rfp|rfq|government|procurement|etenders)\b", re.I)
_COMPANY_RE = re.compile(r"\b(company|supplier|contractor|about|website|who is|research)\b", re.I)
_PRICE_RE = re.compile(r"\b(price|cost|rate|aecom|material|steel|cement|concrete|labour)\b", re.I)


class WebSearchSkill(BaseSkill):
    name = "web_search"
    description = "Searches the web for current information, SA tenders, company research, and market prices"

    async def run(self, inp: SkillInput) -> SkillOutput:
        from vula.skills.web_scraper import VulaWebScraper
        scraper = VulaWebScraper(tenant_id=inp.tenant_id)
        q = inp.question

        try:
            if _TENDER_RE.search(q):
                result = await scraper.monitor_tenders(keywords=q.split()[:5])
                opportunities = getattr(result, "opportunities", []) or []
                if not opportunities:
                    return SkillOutput(
                        answer="No relevant SA tenders found for this query.",
                        skill_name=self.name,
                        confidence=0.4,
                    )
                answer = "**SA Tender Opportunities:**\n" + "\n".join(
                    f"- [{t.get('title','?')}]({t.get('url','')}) — {t.get('closing_date','?')}"
                    for t in opportunities[:8]
                )
                return SkillOutput(answer=answer, skill_name=self.name, confidence=0.8,
                                   sources=[{"type": "tender", "url": t.get("url", "")}
                                            for t in opportunities[:5]])

            elif _COMPANY_RE.search(q):
                url_match = re.search(r"https?://\S+|www\.\S+\.\w{2,}", q)
                url = url_match.group(0) if url_match else f"https://www.google.com/search?q={q.replace(' ', '+')}"
                profile = await scraper.research_company(url=url)
                answer = (
                    f"**{profile.name or 'Company'}**\n"
                    f"{profile.description or ''}\n\n"
                    f"**Services:** {', '.join(profile.services[:5]) if profile.services else 'N/A'}\n"
                    f"**Contact:** {profile.contact_info or 'N/A'}\n"
                    f"**Pitch angle:** {profile.suggested_pitch_angle or 'N/A'}"
                )
                return SkillOutput(answer=answer, skill_name=self.name, confidence=0.75,
                                   sources=[{"type": "company", "url": profile.url}])

            elif _PRICE_RE.search(q):
                search_urls = [
                    f"https://www.buildit.co.za/search?query={q.replace(' ', '+')}",
                    f"https://www.cashbuild.co.za/search/?query={q.replace(' ', '+')}",
                ]
                results = await scraper.monitor_prices(urls=search_urls)
                if not results:
                    return SkillOutput(
                        answer=f"Could not retrieve current pricing for: {q}",
                        skill_name=self.name,
                        confidence=0.3,
                    )
                price_lines = []
                for r in results:
                    if r.products:
                        items = ", ".join(
                            f"{p['name']}: R{p['price']}" for p in r.products[:3]
                        )
                    else:
                        items = "No prices found"
                    price_lines.append(f"- {r.url}: {items}")
                answer = "**Current Market Prices (SA):**\n" + "\n".join(price_lines)
                return SkillOutput(answer=answer, skill_name=self.name, confidence=0.65,
                                   sources=[{"type": "price", "url": r.url} for r in results])

            else:
                # General news digest
                result = await scraper.news_digest(
                    topics=[q[:100]],
                    industry="construction",
                )
                digest = getattr(result, "summary", "") or str(result)[:800]
                return SkillOutput(
                    answer=digest or f"No recent news found for: {q}",
                    skill_name=self.name,
                    confidence=0.5,
                )

        except Exception as exc:
            logger.error("Web search skill failed: %s", exc)
            return SkillOutput(
                answer=f"Web search failed: {exc}",
                skill_name=self.name,
                confidence=0.0,
                error=str(exc),
            )
