"""
core/mass_mind/patterns.py — pattern library (Mass Mind Phase 1, migration 162).

The deliberate, anonymized aggregation the design doc calls for: rollup() periodically turns
per-tenant, fenced reflections (core/memory/reflection.py, migration 159) into
(business_type, skill, model_tier) -> win rate rows with NO tenant identifier — the tenant
boundary that makes cross-tenant learning safe. suggest_tier() is the read side, consulted by
core/hrm/orchestrator.py::_select_model only when a tenant's own history has nothing to say for
the current request (never overrides real tenant-specific signal).

business_type is the already-established industry-vertical taxonomy set at onboarding
(vula_tenant_config.business_type, vula/api/onboarding.py::_map_business_type) — not a new
dimension invented for this.
"""
from __future__ import annotations

import logging
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Optional

logger = logging.getLogger(__name__)

# A rollup row only counts once at least this many samples back it — otherwise one lucky/unlucky
# tenant's small run of outcomes would look like a platform-wide pattern.
MIN_SAMPLE = 5

# Same "good outcome" bar core/memory/reflection.py::get_routing_hints already uses.
_WIN_THRESHOLD = 0.6


def _client():
    from vula.commerce import service
    return service._client()


def rollup(days: float = 30.0, min_sample: int = MIN_SAMPLE) -> int:
    """Recompute the pattern library from the last `days` of reflections across every tenant.
    Returns the number of (business_type, skill, model_tier) rows written. Fails open — never
    raises; a failed rollup just leaves the previous pattern library in place until the next
    tick, never breaks the loop that calls this."""
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    try:
        rows = (_client().table("vula_reflections")
                .select("tenant_id,primary_skill,winning_tier,outcome_score")
                .gte("created_at", cutoff).limit(20000).execute().data or [])
    except Exception as exc:
        logger.debug("pattern rollup read skipped (run migration 159?): %s", exc)
        return 0

    from vula.api.tenants import get_config
    business_type_cache: dict[str, str] = {}

    def _business_type(tenant_id: Optional[str]) -> str:
        if not tenant_id:
            return "other"
        if tenant_id not in business_type_cache:
            try:
                business_type_cache[tenant_id] = (get_config(tenant_id) or {}).get(
                    "business_type") or "other"
            except Exception:
                business_type_cache[tenant_id] = "other"
        return business_type_cache[tenant_id]

    groups: dict[tuple, list[float]] = defaultdict(list)
    for r in rows:
        skill, tier, score = r.get("primary_skill"), r.get("winning_tier"), r.get("outcome_score")
        if not skill or not tier or score is None:
            continue
        groups[(_business_type(r.get("tenant_id")), skill, tier)].append(score)

    written = 0
    now_iso = datetime.now(timezone.utc).isoformat()
    for (business_type, skill, tier), scores in groups.items():
        n = len(scores)
        if n < min_sample:
            continue
        win_rate = sum(1 for s in scores if s > _WIN_THRESHOLD) / n
        avg_score = sum(scores) / n
        try:
            _client().table("vula_mass_mind_patterns").upsert({
                "business_type": business_type, "skill": skill, "model_tier": tier,
                "sample_count": n, "win_rate": round(win_rate, 3),
                "avg_score": round(avg_score, 3), "updated_at": now_iso,
            }, on_conflict="business_type,skill,model_tier").execute()
            written += 1
        except Exception as exc:
            logger.debug("pattern rollup write skipped for %s/%s/%s (run migration 162?): %s",
                        business_type, skill, tier, exc)
    return written


def suggest_tier(tenant_id: str, skill: str, min_sample: int = MIN_SAMPLE) -> Optional[str]:
    """The best-performing model tier the pattern library has evidence for, for a tenant shaped
    like this one (same business_type) running this skill — or None if there's no qualifying
    row (migration not applied, not enough data yet, or genuinely no signal). Never raises."""
    try:
        from vula.api.tenants import get_config
        business_type = (get_config(tenant_id) or {}).get("business_type") or "other"
    except Exception:
        return None

    try:
        rows = (_client().table("vula_mass_mind_patterns")
                .select("model_tier,win_rate,avg_score,sample_count")
                .eq("business_type", business_type).eq("skill", skill)
                .gte("sample_count", min_sample).execute().data or [])
    except Exception as exc:
        logger.debug("pattern suggest skipped (run migration 162?): %s", exc)
        return None

    if not rows:
        return None
    best = max(rows, key=lambda r: (r["win_rate"], r["avg_score"]))
    return best["model_tier"]
