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
"""

from __future__ import annotations

import json
import logging
import sqlite3
from pathlib import Path
from typing import List, Dict, Any, Optional

import httpx

from config import settings
from core.thinkmesh.graph import TaskGraph, ReflectionLog

logger = logging.getLogger(__name__)

OLLAMA_BASE = settings.ollama_base
REFLECTION_MODEL = settings.reflection_model
DB_PATH = settings.reflection_db


class ReflectionAgent:
    """
    Post-task reflection and learning delta writer.

    The reflection loop is what separates Universal Soul from static
    agent systems. Every task outcome is scored and stored. HRM consults
    this store when routing similar future tasks, gradually learning
    which model tiers, skills, and strategies work best for each task type.
    """

    def __init__(
        self,
        ollama_base: str = OLLAMA_BASE,
        reflection_model: str = REFLECTION_MODEL,
        db_path: Path = DB_PATH,
    ):
        self.ollama_base = ollama_base
        self.reflection_model = reflection_model
        self.db_path = db_path
        self._init_db()

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

        # Persist to SQLite
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
        """
        conn = sqlite3.connect(self.db_path)
        try:
            # Simple keyword match — replace with Qdrant semantic search in v2
            keywords = [w for w in goal.lower().split() if len(w) > 4]
            if not keywords:
                return []

            placeholders = " OR ".join(["goal LIKE ?" for _ in keywords])
            params = [tenant_id] + [f"%{kw}%" for kw in keywords] + [limit]

            rows = conn.execute(
                f"""
                SELECT goal, primary_skill, winning_tier, outcome_score,
                       merge_strategy, total_latency_ms, what_worked
                FROM reflections
                WHERE tenant_id = ? AND ({placeholders}) AND outcome_score > 0.6
                ORDER BY outcome_score DESC, timestamp DESC
                LIMIT ?
                """,
                params,
            ).fetchall()

            return [
                {
                    "goal_preview": row[0][:80],
                    "skill": row[1],
                    "winning_tier": row[2],
                    "score": row[3],
                    "merge_strategy": row[4],
                    "latency_ms": row[5],
                    "what_worked": row[6],
                }
                for row in rows
            ]
        finally:
            conn.close()

    def get_stats(self, tenant_id: Optional[str] = None) -> Dict[str, Any]:
        """Summary statistics across reflections.

        tenant_id=None (the default) is deliberate here, unlike get_routing_hints: this backs
        operator-facing views only (/metrics, /agent/stats — both require the master API key,
        neither is scoped to a tenant request), so a platform-wide total is the correct default.
        Pass tenant_id to drill into one tenant's own learning stats instead."""
        where = "WHERE tenant_id = ?" if tenant_id else ""
        params = (tenant_id,) if tenant_id else ()
        conn = sqlite3.connect(self.db_path)
        try:
            total = conn.execute(f"SELECT COUNT(*) FROM reflections {where}", params).fetchone()[0]
            avg_score = conn.execute(
                f"SELECT AVG(outcome_score) FROM reflections {where}", params).fetchone()[0]
            avg_latency = conn.execute(
                f"SELECT AVG(total_latency_ms) FROM reflections {where}", params).fetchone()[0]
            top_skill = conn.execute(
                f"SELECT primary_skill, COUNT(*) as c FROM reflections {where} "
                f"GROUP BY primary_skill ORDER BY c DESC LIMIT 1", params
            ).fetchone()

            return {
                "total_reflections": total,
                "avg_outcome_score": round(avg_score or 0, 2),
                "avg_latency_ms": int(avg_latency or 0),
                "most_used_skill": top_skill[0] if top_skill else None,
            }
        finally:
            conn.close()

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

    def _init_db(self) -> None:
        """Initialise SQLite reflection store."""
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.db_path)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS reflections (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                graph_id TEXT NOT NULL,
                goal TEXT NOT NULL,
                primary_skill TEXT,
                winning_tier TEXT,
                outcome_score REAL,
                merge_strategy TEXT,
                total_latency_ms INTEGER,
                what_worked TEXT,
                what_to_try_next TEXT,
                skills_used TEXT,
                tiers_used TEXT,
                tenant_id TEXT NOT NULL DEFAULT 'default',
                timestamp REAL
            )
        """)
        # A store that predates 2026-09-15 has this table without tenant_id — ALTER TABLE ADD
        # COLUMN onto it (SQLite has no "IF NOT EXISTS" for ADD COLUMN, so probe by error
        # instead). Existing rows backfill to 'default' rather than NULL, so a pre-fence row
        # is at least self-consistently scoped rather than invisible to every tenant query.
        try:
            conn.execute("ALTER TABLE reflections ADD COLUMN tenant_id TEXT NOT NULL DEFAULT 'default'")
        except sqlite3.OperationalError as exc:
            if "duplicate column" not in str(exc).lower():
                raise
        conn.execute("CREATE INDEX IF NOT EXISTS idx_goal ON reflections(goal)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_score ON reflections(outcome_score)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_tenant ON reflections(tenant_id)")
        conn.commit()
        conn.close()

    def _write_log(self, log: ReflectionLog) -> None:
        """Persist a ReflectionLog to SQLite."""
        conn = sqlite3.connect(self.db_path)
        try:
            conn.execute(
                """
                INSERT INTO reflections
                    (graph_id, goal, primary_skill, winning_tier, outcome_score,
                     merge_strategy, total_latency_ms, what_worked, what_to_try_next,
                     skills_used, tiers_used, tenant_id, timestamp)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    log.graph_id,
                    log.goal[:500],
                    log.skills_used[0] if log.skills_used else None,
                    log.model_tiers_used[0] if log.model_tiers_used else None,
                    log.outcome_score,
                    log.merge_strategy_used.value,
                    log.total_latency_ms,
                    log.what_worked,
                    log.what_to_try_next,
                    json.dumps(log.skills_used),
                    json.dumps(log.model_tiers_used),
                    log.tenant_id,
                    log.timestamp,
                ),
            )
            conn.commit()
        finally:
            conn.close()

    def _ollama_generate(self, prompt: str, max_tokens: int = 500) -> str:
        """Lightweight sync Ollama call for reflection."""
        payload = {
            "model": self.reflection_model,
            "prompt": prompt,
            "stream": False,
            "options": {"num_predict": max_tokens, "temperature": 0.2},
        }
        with httpx.Client(timeout=20.0) as client:
            resp = client.post(f"{self.ollama_base}/api/generate", json=payload)
            resp.raise_for_status()
            return resp.json().get("response", "")
