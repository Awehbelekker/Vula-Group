"""Shared pytest fixtures for the Vula test suite."""
import os
import tempfile

import pytest

# Point at test doubles before any app imports
os.environ.setdefault("OLLAMA_BASE", "http://localhost:11434")
os.environ.setdefault("QDRANT_BASE", "http://localhost:6333")
os.environ.setdefault("API_KEY", "")
os.environ.setdefault("DEBUG", "true")

# Isolate persistent SQLite stores (tenants.db, ingestion_log.db, reflection.db)
# in a throwaway directory so tests never pollute the developer's ~/.vula data
# or each other. Honour an explicitly provided DATA_DIR if the caller set one.
os.environ.setdefault("DATA_DIR", tempfile.mkdtemp(prefix="vula_test_data_"))


@pytest.fixture(autouse=True)
def _isolate_tenant_db(tmp_path, monkeypatch):
    """Give every test its own empty tenant registry.

    The tenant DB is a process-wide singleton backed by SQLite on disk. Without
    this, a test that onboards a tenant (writing a phone→tenant row) leaks that
    row into later tests in the same session (e.g. the WhatsApp tenant-lookup
    test). Pointing the singleton at a per-test temp file keeps them isolated.
    """
    import vula.models.tenants as tenants_mod

    monkeypatch.setattr(
        tenants_mod, "_db", tenants_mod.LocalTenantDB(db_path=tmp_path / "tenants.db")
    )
    yield


@pytest.fixture(autouse=True)
def _no_real_whatsapp_creds_by_default(monkeypatch):
    """No test should be able to reach the real Meta Graph API just by exercising the WhatsApp
    webhook without explicitly mocking credentials.

    2026-09-08: _mark_read_and_typing's call site was changed from `await`ed inline to
    `asyncio.create_task(...)` (fire-and-forget, to stop it adding latency to every real inbound
    message). That decoupled it from the request/test lifecycle it used to share — a test that
    exercises the webhook route without mocking WhatsApp credentials (most of them; they only
    care about the handler dispatch, not this cosmetic side effect) used to have this call
    resolve and fail safely INSIDE its own synchronous `await`, within the test's own call
    stack. Now it can keep running as an orphaned background task after the test function has
    already returned — attempting a REAL network call to graph.facebook.com with whatever real
    global WHATSAPP_TOKEN/WHATSAPP_PHONE_ID happen to be set in the dev environment's own .env,
    landing at some arbitrary point during a LATER, unrelated test. Defaulting both the
    per-tenant lookup and the global env fallback to "not configured" here closes that off for
    every test at once; a test that specifically wants to exercise real-looking WhatsApp-send
    behaviour (e.g. tests/test_typing_indicator.py) already patches these explicitly per-test,
    which correctly overrides this default for the scope of its own `with patch(...)` block.
    """
    import vula.api.whatsapp as wa_mod
    from unittest.mock import AsyncMock

    monkeypatch.setattr(wa_mod, "_get_tenant_wa_creds", AsyncMock(return_value=None))
    # The per-sender flood guard is process-global; many tests post from the same number within
    # a second of each other, so give each test a clean window.
    wa_mod._sender_hits.clear()
    wa_mod._sender_warned_at.clear()
    wa_mod._sent_keys.clear()
    monkeypatch.setattr(wa_mod.settings, "whatsapp_token", "")
    monkeypatch.setattr(wa_mod.settings, "whatsapp_phone_id", "")
    # The inbound-file dedup cache (2026-09-10) is a module-level dict — a real process cache,
    # but between tests it would let one test's filename claim suppress the next test's identical
    # upload. Clear it per test.
    wa_mod._media_claims_local.clear()
    # The team cache (vula.team_index) is process-wide; each test mocks its own team table.
    from vula import team_index
    team_index.invalidate()
    yield
    wa_mod._media_claims_local.clear()
    team_index.invalidate()


@pytest.fixture(autouse=True)
def _no_real_llm_calls_by_default(monkeypatch):
    """No test should be able to reach a REAL LLM — local Ollama or cloud — just by exercising a
    code path that forgot to mock litellm.acompletion.

    2026-10-24: a full suite run on THIS specific developer machine made real, unmocked
    litellm.acompletion calls to the local Ollama instance that ALSO serves a live trading bot's
    inference on the same GPU. config.py defaults OLLAMA_BASE to http://localhost:11434 — on a
    clean CI runner nothing listens there, so an accidentally-unmocked call just fails safely with
    a connection error, which is why this went unnoticed for a long time. But on a machine that
    happens to run a real local Ollama for other reasons, that exact same "safety net" becomes the
    mechanism of harm: the call actually succeeds, loading whatever MODEL_WORKER the local .env
    configures at litellm's default num_ctx (131072 for several models) — the very memory-eviction
    bug core/llm_router.py's install_ollama_auth wrapper exists to cap — repeatedly evicting
    whatever else that GPU had resident. Same shape of bug _no_real_whatsapp_creds_by_default above
    already guards against for WhatsApp; this is the equivalent for litellm. A test that
    deliberately wants to exercise the real acompletion path (most of them do, correctly — they
    patch it explicitly, typically with unittest.mock.patch("litellm.acompletion", ...)) overrides
    this default for the scope of its own patch, same as test_typing_indicator.py already does for
    the WhatsApp guard; nothing about that pattern changes here.
    """
    try:
        import litellm
    except Exception:
        return

    async def _guard(*args, **kwargs):
        raise AssertionError(
            "A test made a REAL litellm.acompletion call instead of mocking it — "
            f"model={kwargs.get('model')!r} api_base={kwargs.get('api_base')!r}. On this machine "
            "that could hit a real local Ollama shared with other live infrastructure (or a real "
            "cloud provider, if a real API key happens to be set). Mock litellm.acompletion, or "
            "whatever resolves the generation route, in this test."
        )

    monkeypatch.setattr(litellm, "acompletion", _guard)


@pytest.fixture
def sample_goal() -> str:
    return "What is the capital of South Africa?"


@pytest.fixture
def complex_goal() -> str:
    return "Analyse and compare the pros and cons of using DeepSeek R1 vs Qwen 2.5 for a privacy-first local AI system."
