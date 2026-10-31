"""Licence guard for the OpenTakeoff sidecar. OpenTakeoff relicensed to AGPL after tag
apache-2.0-final. Vula may only ever build that exact Apache-2.0 commit, and must ship its licence
files. If this test fails, someone moved the pin: read services/opentakeoff/README.md first."""
import re
from pathlib import Path

SIDECAR = Path(__file__).resolve().parents[2] / "services" / "opentakeoff"
COMMIT = "84c7f48e458f6cd74f694e3a970dd011c73a8f2e"


def _dockerfile() -> str:
    return (SIDECAR / "Dockerfile").read_text()


def test_sidecar_is_pinned_to_the_last_apache_commit():
    d = _dockerfile()
    assert f"ARG OT_COMMIT={COMMIT}" in d
    assert "ARG OT_TAG=apache-2.0-final" in d
    # Every 40-hex commit named in the file is the pinned one: no second ref sneaks in.
    assert set(re.findall(r"\b[0-9a-f]{40}\b", d)) == {COMMIT}
    # The build proves what it fetched is the pin, and checks the licence.
    assert 'test "$(git rev-parse HEAD)" = "$OT_COMMIT"' in d
    assert 'grep -q "Apache License" LICENSE' in d


def test_sidecar_never_builds_a_moving_branch():
    d = _dockerfile()
    for moving in ("main", "master", "HEAD:", "--branch", "latest"):
        assert not re.search(rf"(fetch|clone|checkout)[^\n]*\b{re.escape(moving)}\b", d), moving
    assert "opentakeoff-mcp@" not in d and "npx" not in d      # no unpinned npm package either


def test_upstream_licence_files_ship_in_the_image():
    d = _dockerfile()
    assert "/src/LICENSE /src/NOTICE /src/THIRD-PARTY-NOTICES.md /app/licenses/opentakeoff/" in d


def test_no_upstream_source_is_vendored_into_the_repo():
    # Only Vula's own files live here. Upstream code is fetched at build time from the pinned commit.
    assert sorted(p.name for p in SIDECAR.iterdir()) == ["Dockerfile", "README.md", "bridge.mjs"]
