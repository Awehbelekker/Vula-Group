"""tools/check_client_secrets.py — no secret in code shipped to browsers or phones."""
import importlib.util
from pathlib import Path

spec = importlib.util.spec_from_file_location(
    "check_client_secrets", Path(__file__).resolve().parents[1] / "tools" / "check_client_secrets.py")
ccs = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ccs)


def _app(tmp_path, code):
    f = tmp_path / "vula_mobile" / "src" / "api.js"
    f.parent.mkdir(parents=True)
    f.write_text(code)
    return tmp_path


def test_a_baked_in_key_is_caught(tmp_path):
    root = _app(tmp_path, 'const K = "0d409e634bd4e81a0d7dd0764264db6cac20721e2ce43915a43cd1997a019ca5";')
    assert ccs.scan(root) == [("vula_mobile/src/api.js", 1, "long hex key")]


def test_a_secret_build_variable_is_caught_but_the_anon_key_is_not(tmp_path):
    root = _app(tmp_path, "const a = import.meta.env.VITE_API_KEY;\nconst b = import.meta.env.VITE_SUPABASE_ANON_KEY;")
    assert ccs.scan(root) == [("vula_mobile/src/api.js", 1, "secret build variable")]


def test_labels_and_short_ids_pass(tmp_path):
    root = _app(tmp_path, 'const labels = { api_key: "API key" }; const id = "a1b2c3";')
    assert ccs.scan(root) == []


def test_the_repo_is_clean():
    assert ccs.scan() == []
