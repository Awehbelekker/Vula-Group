"""2026-09-29 (Judy, digg-demo): her questions were answered by llama-3.3-70b although the model
bake-off chose Gemini 2.5 Flash for the agents via CLOUD_MODEL_BY_TASK — no skill passed task_type
to resolve_generation_route(), so the per-task map was never consulted and every cloud call fell
back to model_worker_cloud. Every skill's route call must name its task."""
import ast
import pathlib

from core import llm_router

SKILLS = pathlib.Path(__file__).resolve().parents[1] / "core" / "skills"


def test_every_skill_route_call_names_its_task():
    missing = []
    for path in sorted(SKILLS.glob("*.py")):
        for node in ast.walk(ast.parse(path.read_text())):
            if (isinstance(node, ast.Call) and getattr(node.func, "id", None) == "resolve_generation_route"
                    and not any(k.arg == "task_type" for k in node.keywords)
                    and not any(k.arg == "model" for k in node.keywords)):   # an explicit model is its own choice
                missing.append(f"{path.name}:{node.lineno}")
    assert not missing, f"resolve_generation_route() without task_type: {missing}"


def test_the_per_task_map_picks_the_agents_model(monkeypatch):
    monkeypatch.setattr(llm_router.settings, "cloud_model_by_task",
                        '{"finance_admin": "google/gemini-2.5-flash", "email_admin": "google/gemini-2.5-flash"}')
    monkeypatch.setattr(llm_router.settings, "model_worker_cloud", "meta-llama/llama-3.3-70b-instruct")
    assert llm_router.cloud_model_for("finance_admin") == "google/gemini-2.5-flash"
    assert llm_router.cloud_model_for("email_admin") == "google/gemini-2.5-flash"
    assert llm_router.cloud_model_for(None) == "meta-llama/llama-3.3-70b-instruct"
