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
import time
from pathlib import Path
from typing import List, Dict, Any, Optional

import httpx

from core.thinkmesh.graph import TaskGraph, ReflectionLog, MergeStrategy

logger = logging.getLogger(__name__)

OLLAMA_BASE = "http://localhost:11434"
REFLECTION_MODEL = "deepseek-r1:1.5b"   # Lightweight — reflection is cheap
DB_PATH = Path.home() / ".universal-soul" / "reflection.db"


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
            skills_used=list({b.skill for b in graph.branches}),
            model_tiers_used=list({b.model_tier.value for b in graph.done_branches}),
            winning_branch_id=winning_branch.branch_id if winning_branch else None,
            total_latency_ms=graph.total_latency_ms,
            what_worked=what_worked,
            what_to_try_next=what_to_try_next,
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

    def get_routing_hints(self, goal: str, limit: int = 5) -> List[Dict[str, Any]]:
        """
        Query stored reflection logs for similar past tasks.
        Returns routing hints HRM can use to improve branch assignments.
        """
        conn = sqlite3.connect(self.db_path)
        try:
            # Simple keyword match — replace with Qdrant semantic search in v2
            keywords = [w for w in goal.lower().split() if len(w) > 4]
            if not keywords:
                return []

            placeholders = " OR ".join([f"goal LIKE ?" for _ in keywords])
            params = [f"%{kw}%" for kw in keywords] + [limit]

            rows = conn.execute(
                f"""
                SELECT goal, primary_skill, winning_tier, outcome_score,
                       merge_strategy, total_latency_ms, what_worked
                FROM reflections
                WHERE ({placeholders}) AND outcome_score > 0.6
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

    def get_stats(self) -> Dict[str, Any]:
        """Summary statistics across all reflections."""
        conn = sqlite3.connect(self.db_path)
        try:
            total = conn.execute("SELECT COUNT(*) FROM reflections").fetchone()[0]
            avg_score = conn.execute("SELECT AVG(outcome_score) FROM reflections").fetchone()[0]
            avg_latency = conn.execute("SELECT AVG(total_latency_ms) FROM reflections").fetchone()[0]
            top_skill = conn.execute(
                "SELECT primary_skill, COUNT(*) as c FROM reflections GROUP BY primary_skill ORDER BY c DESC LIMIT 1"
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
        """Generate what_worked and what_to_try_next using the reflection model."""

        # Build a brief summary for the model
        branch_summary = "\n".join([
            f"- Branch {b.branch_id[:6]}: tier={b.model_tier.value} "
            f"confidence={b.confidence:.0%} latency={b.latency_ms}ms "
            f"status={b.status.value}"
            for b in graph.branches
        ])

        prompt = (
            f"A task was completed with outcome score {score:.0%}.\n"
            f"Goal: {graph.goal[:150]}\n"
            f"Skill used: {graph.primary_skill}\n"
            f"Merge strategy: {graph.merge_strategy.value}\n"
            f"Branches:\n{branch_summary}\n\n"
            f"In 1-2 sentences each, answer:\n"
            f"1. What worked well?\n"
            f"2. What should be tried differently next time?\n"
            f"Format as:\nWORKED: ...\nNEXT: ..."
        )

        try:
            response = self._ollama_generate(prompt, max_tokens=200)
            worked_match = response.split("NEXT:")[0].replace("WORKED:", "").strip()
            next_match = response.split("NEXT:")[-1].strip() if "NEXT:" in response else ""
            return worked_match or "Branches executed successfully.", next_match or "No changes needed."
        except Exception as e:
            logger.warning(f"Reflection text generation failed: {e}")
            return "Task completed.", "Monitor for similar patterns."

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
                timestamp REAL
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_goal ON reflections(goal)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_score ON reflections(outcome_score)")
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
                     skills_used, tiers_used, timestamp)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
