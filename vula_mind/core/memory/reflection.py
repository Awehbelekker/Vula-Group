"""
core/memory/reflection.py

Reflection Agent — Universal Soul AI's self-improvement loop.

After every completed TaskGraph, the reflection agent:
    1. Scores how well the goal was achieved (0.0–1.0)
    2. Identifies which branches/skills/tiers worked best
    3. Writes a ReflectionLog to the memory store
    4. Accumulates these logs to improve HRM routing decisions over time

This is NOT model retraining. It's outcome-driven routing improvement:
the system remembers what worked for similar tasks and routes accordingly.

Storage: Supabase (migration 159), not local SQLite. 2026-09-15: confirmed via Railway's own
service config that Vula-Group has no persistent volume mounted — a local SQLite file here would
reset to empty on every redeploy, which happens several times a day. Every other "learned"
mechanism on the platform (voice profiles, learned answers, merchant profiles) already lives in
Supabase for exactly this reason; this was the one exception. tenant_id fencing (get_routing_hints
requires it) shipped the same day this moved off SQLite — see the Mass Mind design doc.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from core.thinkmesh.graph import ReflectionLog, TaskGraph

logger = logging.getLogger(__name__)


def _client():
    from vula.commerce import service
    return service._client()


class ReflectionAgent:
    """
    Post-task reflection and learning delta writer.

    The reflection loop is what separates Universal Soul from static
    agent systems. Every task outcome is scored and stored. HRM consults
    this store when routing similar future tasks, gradually learning
    which model tiers, skills, and strategies work best for each task type.
    """

    # -------------------------------------------------------------------------
    # Public API
    # -------------------------------------------------------------------------

    def reflect(self, graph: TaskGraph, user_feedback: Optional[float] = None) -> ReflectionLog:
        """
        Run reflection on a completed TaskGraph.

        user_feedback: optional 0.0–1.0 rating from user (thumbs up/down etc.)
        """
        # Auto-score the outcome
        auto_score = self._auto_score(graph)

        # Blend with user feedback if available
        if user_feedback is not None:
            outcome_score = (auto_score * 0.4) + (user_feedback * 0.6)
        else:
            outcome_score = auto_score

        # Determine winning branch
        winning_branch = self._find_winning_branch(graph)

        # Generate reflection text
        what_worked, what_to_try_next = self._generate_reflection_text(graph, outcome_score)

        log = ReflectionLog(
            graph_id=graph.graph_id,
            goal=graph.goal,
            merge_strategy_used=graph.merge_strategy,
            outcome_score=round(outcome_score, 2),
            skills_used=list({b.skill_id for b in graph.branches}),
            model_tiers_used=list({b.model_tier.value for b in graph.done_branches}),
            winning_branch_id=winning_branch.branch_id if winning_branch else None,
            total_latency_ms=graph.total_latency_ms,
            what_worked=what_worked,
            what_to_try_next=what_to_try_next,
            tenant_id=getattr(graph, "tenant_id", None) or "default",
        )

        # Persist to Supabase (fails open — this must never break the caller's request; the
        # caller in core/agent_runner.py already runs this in a background thread for exactly
        # this reason, since it's now a real network write, not a local disk write).
        self._write_log(log)

        # Attach to graph
        graph.reflection = log

        logger.info(
            f"Reflection for graph {graph.graph_id[:8]}: "
            f"score={outcome_score:.2f} "
            f"latency={graph.total_latency_ms}ms"
        )

        return log

    def get_routing_hints(self, tenant_id: str, goal: str, limit: int = 5) -> List[Dict[str, Any]]:
        """
        Query stored reflection logs for similar past tasks — scoped to ONE tenant.

        tenant_id is required, not optional: this store used to have no tenant column at all,
        so a tenant's routing hint could surface another tenant's stored goal text (and this
        result directly sets the model tier via HRMOrchestrator._select_model, and gets echoed
        into a customer-facing answer by core/skills/memory_recall.py). A required parameter
        makes it impossible for a future call site to silently omit the fence the way this one
        did for its entire life until 2026-09-15. Platform-wide cross-tenant pattern learning
        is a deliberate, separate aggregation (the Mass Mind rollup) that never round-trips
        through this per-tenant method.

        Keyword matching happens in Python over a bounded, already-tenant-scoped candidate set
        — same convention as vula/escalation.py::find_learned_answer — rather than a
        server-side LIKE-per-keyword scan, which doesn't map cleanly onto the Supabase query
        builder for an arbitrary number of OR'd keywords.
        """
        keywords = [w for w in goal.lower().split() if len(w) > 4]
        if not keywords:
            return []
        try:
            rows = (_client().table("vula_reflections")
                    .select("goal,primary_skill,winning_tier,outcome_score,merge_strategy,"
                            "total_latency_ms,what_worked")
                    .eq("tenant_id", tenant_id).gt("outcome_score", 0.6)
                    .order("outcome_score", desc=True).order("created_at", desc=True)
                    .limit(200).execute().data or [])
        except Exception as exc:
            logger.debug("routing-hint lookup skipped (run migration 159?): %s", exc)
            return []

        matched = [r for r in rows
                   if any(kw in (r.get("goal") or "").lower() for kw in keywords)]

        return [
            {
                "goal_preview": (r.get("goal") or "")[:80],
                "skill": r.get("primary_skill"),
                "winning_tier": r.get("winning_tier"),
                "score": r.get("outcome_score"),
                "merge_strategy": r.get("merge_strategy"),
                "latency_ms": r.get("total_latency_ms"),
                "what_worked": r.get("what_worked"),
            }
            for r in matched[:limit]
        ]

    def get_stats(self, tenant_id: Optional[str] = None, limit: int = 5000) -> Dict[str, Any]:
        """Summary statistics across reflections.

        tenant_id=None (the default) is deliberate here, unlike get_routing_hints: this backs
        operator-facing views only (/metrics, /agent/stats — both require the master API key,
        neither is scoped to a tenant request), so a platform-wide total is the correct default.
        Pass tenant_id to drill into one tenant's own learning stats instead.

        total_reflections is a real COUNT(*) (Postgres, via count="exact") — cheap and exact.
        avg_outcome_score / avg_latency_ms / most_used_skill are computed in Python over the
        most recent `limit` rows rather than a true all-time aggregate: Supabase's REST query
        builder doesn't cleanly express AVG()/GROUP BY, and this is an operator convenience
        view, not a figure shown to a tenant or used in any money/verification path.
        """
        try:
            q = _client().table("vula_reflections").select(
                "primary_skill,outcome_score,total_latency_ms", count="exact")
            if tenant_id:
                q = q.eq("tenant_id", tenant_id)
            res = q.order("created_at", desc=True).limit(limit).execute()
            rows = res.data or []
            total = res.count if res.count is not None else len(rows)
        except Exception as exc:
            logger.debug("reflection stats lookup skipped (run migration 159?): %s", exc)
            return {"total_reflections": 0, "avg_outcome_score": 0, "avg_latency_ms": 0,
                    "most_used_skill": None}

        scores = [r["outcome_score"] for r in rows if r.get("outcome_score") is not None]
        latencies = [r["total_latency_ms"] for r in rows if r.get("total_latency_ms") is not None]
        skill_counts: Dict[str, int] = {}
        for r in rows:
            sk = r.get("primary_skill")
            if sk:
                skill_counts[sk] = skill_counts.get(sk, 0) + 1
        top_skill = max(skill_counts, key=skill_counts.get) if skill_counts else None

        return {
            "total_reflections": total,
            "avg_outcome_score": round(sum(scores) / len(scores), 2) if scores else 0,
            "avg_latency_ms": int(sum(latencies) / len(latencies)) if latencies else 0,
            "most_used_skill": top_skill,
        }

    # -------------------------------------------------------------------------
    # Scoring
    # -------------------------------------------------------------------------

    def _auto_score(self, graph: TaskGraph) -> float:
        """
        Automatically score task outcome without user feedback.

        Factors:
        - Branch success rate
        - Average confidence of completed branches
        - Whether merged output is non-empty and substantive
        - Latency (penalise very slow responses)
        """
        score = 0.5  # Base

        # Branch success rate
        total = len(graph.branches)
        done = len(graph.done_branches)
        if total > 0:
            success_rate = done / total
            score += (success_rate - 0.5) * 0.3  # ±0.15

        # Average confidence
        if graph.done_branches:
            avg_conf = sum(b.confidence for b in graph.done_branches) / len(graph.done_branches)
            score += (avg_conf - 0.5) * 0.3  # ±0.15

        # Output quality (length proxy)
        if graph.merged_output:
            word_count = len(graph.merged_output.split())
            if word_count > 50:
                score += 0.1
            elif word_count < 10:
                score -= 0.15
        else:
            score -= 0.3  # No output = bad

        # Latency penalty (>30s is slow)
        if graph.total_latency_ms > 30000:
            score -= 0.05

        return max(0.0, min(1.0, score))

    def _find_winning_branch(self, graph: TaskGraph):
        """Find the branch that contributed most to the final output."""
        if not graph.done_branches:
            return None
        return max(graph.done_branches, key=lambda b: b.confidence)

    # -------------------------------------------------------------------------
    # Reflection text generation
    # -------------------------------------------------------------------------

    def _generate_reflection_text(
        self,
        graph: TaskGraph,
        score: float,
    ) -> tuple[str, str]:
        """Summarise the outcome WITHOUT an LLM call.

        The reflection store only needs a short note for the routing-hint
        history. Making a separate LLM call here (after every single query)
        doubled inference cost for zero user benefit. We now derive the note
        cheaply from the graph metrics.
        """
        skill = graph.primary_skill
        if score >= 0.75:
            worked = f"{skill} answered well (score {score:.0%}, {graph.merge_strategy.value})."
            nxt = "Keep routing similar questions to this skill."
        elif score >= 0.5:
            worked = f"{skill} produced a usable answer (score {score:.0%})."
            nxt = "Consider richer KB context for this question type."
        else:
            worked = f"{skill} struggled (score {score:.0%})."
            nxt = "Try a different skill or improve the knowledge base."
        return worked, nxt

    # -------------------------------------------------------------------------
    # Persistence
    # -------------------------------------------------------------------------

    def _write_log(self, log: ReflectionLog) -> None:
        """Persist a ReflectionLog to Supabase. Fails open — logged at debug, never raised —
        same convention as every other best-effort learned-data writer on the platform (e.g.
        vula/commerce/order_workflow.get_order_settings)."""
        try:
            _client().table("vula_reflections").insert({
                "tenant_id": log.tenant_id,
                "graph_id": log.graph_id,
                "goal": log.goal[:500],
                "primary_skill": log.skills_used[0] if log.skills_used else None,
                "winning_tier": log.model_tiers_used[0] if log.model_tiers_used else None,
                "outcome_score": log.outcome_score,
                "merge_strategy": log.merge_strategy_used.value,
                "total_latency_ms": log.total_latency_ms,
                "what_worked": log.what_worked,
                "what_to_try_next": log.what_to_try_next,
                "skills_used": log.skills_used,
                "model_tiers_used": log.model_tiers_used,
            }).execute()
        except Exception as exc:
            logger.debug("reflection write skipped (run migration 159?): %s", exc)
