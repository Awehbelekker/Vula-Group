"""
main.py — Universal Soul AI Unlimited

Single entry point. Wires HRM + ThinKMesh + Memory + Mesh together
into a clean async pipeline.

Usage:
    python main.py                    # Interactive mode
    python main.py --demo             # Run a demo task
    python main.py --mesh             # Start with mesh discovery
    python main.py --stats            # Show reflection stats
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from pathlib import Path

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("universal-soul")

# Suppress noisy sub-loggers
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)


async def run_task(goal: str, enable_mesh: bool = False) -> str:
    """
    Full pipeline: goal → HRM → ThinKMesh → merge → reflect → output.
    """
    from core.hrm.orchestrator import HRMOrchestrator
    from core.thinkmesh.executor import ThinKMeshExecutor
    from core.thinkmesh.merger import ThinKMeshMerger
    from core.memory.reflection import ReflectionAgent

    hrm = HRMOrchestrator()
    executor = ThinKMeshExecutor()
    merger = ThinKMeshMerger()
    reflector = ReflectionAgent()

    # Optionally wire mesh state into HRM
    if enable_mesh:
        from mesh.discovery import MeshDiscovery
        discovery = MeshDiscovery(role="desktop", model_tiers=["7b", "14b"])
        await discovery.start()
        graph_hints = {"mesh_state": discovery.mesh_state}

    print(f"\n🧠 Universal Soul — processing: {goal[:80]}")
    print("─" * 60)

    # Step 1: HRM plans the task graph
    from core.thinkmesh.graph import TaskGraph
    graph = hrm.plan(TaskGraph(original_prompt=goal))
    complexity_label = {1: "simple", 2: "moderate", 3: "complex"}.get(graph.complexity, "moderate")
    print(
        f"📊 Complexity: {complexity_label} | "
        f"Skill: {graph.primary_skill} | "
        f"Branches: {len(graph.branches)} | "
        f"Strategy: {graph.merge_strategy.value}"
    )

    # Step 2: ThinKMesh executes branches in parallel
    print("⚡ Running parallel branches...")
    graph = await executor.execute(graph)
    print(
        f"✅ {len(graph.done_branches)}/{len(graph.branches)} branches succeeded"
    )

    # Step 3: Merge branch outputs
    print("🔀 Merging outputs...")
    graph = merger.merge(graph)
    merged = graph.final_answer

    # Step 4: Reflect and learn
    reflection = reflector.reflect(graph)

    print("─" * 60)
    print(f"\n💬 Answer:\n{merged}\n")
    print(
        f"📈 Outcome score: {reflection.outcome_score:.0%} | "
        f"Latency: {graph.total_latency_ms}ms"
    )

    return merged


async def interactive_mode(enable_mesh: bool = False) -> None:
    """REPL-style interactive session."""
    print("\n" + "═" * 60)
    print("  🌌 Universal Soul AI — Unlimited")
    print("  Privacy-first | Mesh-native | Open source")
    print("  Type 'quit' to exit | 'stats' for reflection stats")
    print("═" * 60 + "\n")

    from core.memory.reflection import ReflectionAgent
    reflector = ReflectionAgent()

    while True:
        try:
            goal = input("You: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nGoodbye.")
            break

        if not goal:
            continue
        if goal.lower() in ("quit", "exit", "bye"):
            print("Goodbye.")
            break
        if goal.lower() == "stats":
            stats = reflector.get_stats()
            print(f"\n📊 Reflection Stats:")
            for k, v in stats.items():
                print(f"  {k}: {v}")
            print()
            continue

        try:
            await run_task(goal, enable_mesh=enable_mesh)
        except Exception as e:
            logger.error(f"Task failed: {e}")
            print(f"\n❌ Error: {e}\n")


async def run_demo() -> None:
    """Demo tasks showcasing the full pipeline."""
    demo_tasks = [
        "What is the capital of France?",           # Simple — single branch
        "Explain how ThinKMesh parallel reasoning works.",  # Moderate — 2 branches
        "Analyse the pros and cons of using DeepSeek R1 vs GPT-4 for a privacy-first local AI system, and recommend an architecture.",  # Complex — 3 branches
    ]

    print("\n🚀 Universal Soul AI — Demo Mode")
    print("Running 3 tasks at increasing complexity levels\n")

    for task in demo_tasks:
        await run_task(task)
        print("\n" + "═" * 60 + "\n")
        await asyncio.sleep(1)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Universal Soul AI — Privacy-first mesh-native AI"
    )
    parser.add_argument("--demo", action="store_true", help="Run demo tasks")
    parser.add_argument("--mesh", action="store_true", help="Enable mesh discovery")
    parser.add_argument("--stats", action="store_true", help="Show reflection stats")
    parser.add_argument("--task", type=str, help="Run a single task and exit")
    parser.add_argument("--debug", action="store_true", help="Enable debug logging")
    args = parser.parse_args()

    if args.debug:
        logging.getLogger().setLevel(logging.DEBUG)

    if args.stats:
        from core.memory.reflection import ReflectionAgent
        stats = ReflectionAgent().get_stats()
        print("\n📊 Reflection Stats:")
        for k, v in stats.items():
            print(f"  {k}: {v}")
        return

    if args.demo:
        asyncio.run(run_demo())
    elif args.task:
        asyncio.run(run_task(args.task, enable_mesh=args.mesh))
    else:
        asyncio.run(interactive_mode(enable_mesh=args.mesh))


if __name__ == "__main__":
    main()
