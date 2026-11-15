"""Copy the knowledge base to a new Qdrant cluster (13 Nov 2026: São Paulo → EU).

Each collection on the current cluster is snapshotted, uploaded to the target with
priority=snapshot, and the point counts compared. Nothing is deleted from the source; a target
equal to the current cluster does nothing.
"""
import json

import httpx
import pytest

from vula.integrations import qdrant_migrate as qm

SRC, DST = "https://sp.example:6333", "https://eu.example:6333"


def _clusters(src_points, dst_after=None, calls=None, fail_upload=()):
    dst_points = {}

    def handler(req: httpx.Request) -> httpx.Response:
        url = str(req.url)
        if calls is not None:
            calls.append((req.method, url.split("?")[0], req.headers.get("api-key")))
        if req.method == "DELETE" and url.startswith(SRC) and "/snapshots/" not in url:
            raise AssertionError("never delete a collection on the source")
        if url.startswith(SRC):
            if req.url.path == "/collections":
                return httpx.Response(200, json={"result": {"collections": [{"name": n} for n in src_points]}})
            parts = req.url.path.strip("/").split("/")
            name = parts[1]
            if len(parts) == 2:
                return httpx.Response(200, json={"result": {"points_count": src_points[name]}})
            if req.method == "POST":
                return httpx.Response(200, json={"result": {"name": f"{name}-snap"}})
            if req.method == "GET":
                return httpx.Response(200, content=b"SNAPSHOT:" + name.encode())
            return httpx.Response(200, json={"result": True})
        parts = req.url.path.strip("/").split("/")
        name = parts[1]
        if req.method == "POST" and parts[-1] == "upload":
            assert req.url.params["priority"] == "snapshot"
            if name in fail_upload:
                return httpx.Response(500, json={"status": {"error": "disk full"}})
            dst_points[name] = (dst_after or {}).get(name, src_points[name])
            return httpx.Response(200, json={"result": True})
        if name not in dst_points:
            return httpx.Response(404, json={})
        return httpx.Response(200, json={"result": {"points_count": dst_points[name]}})
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


@pytest.fixture(autouse=True)
def _settings(monkeypatch):
    monkeypatch.setattr(qm.settings, "qdrant_base", SRC)
    monkeypatch.setattr(qm.settings, "qdrant_api_key", "src-key")
    monkeypatch.setattr(qm.settings, "qdrant_migrate_to_base", DST + "/")
    monkeypatch.setattr(qm.settings, "qdrant_migrate_to_api_key", "dst-key")
    rows = []
    monkeypatch.setattr(qm, "_record", rows.append)
    return rows


@pytest.mark.asyncio
async def test_every_collection_is_copied_and_counted(_settings):
    calls = []
    async with _clusters({"vula_digg-demo": 1200, "vula_off-the-hook": 85}, calls=calls) as c:
        out = await qm.migrate_all(c)
    assert {n: r["ok"] for n, r in out.items()} == {"vula_digg-demo": True, "vula_off-the-hook": True}
    assert out["vula_digg-demo"]["target_points"] == 1200
    uploads = [(u, k) for m, u, k in calls if u.endswith("/snapshots/upload")]
    assert uploads == [(f"{DST}/collections/vula_digg-demo/snapshots/upload", "dst-key"),
                       (f"{DST}/collections/vula_off-the-hook/snapshots/upload", "dst-key")]
    assert all(k == "src-key" for m, u, k in calls if u.startswith(SRC))       # keys never mixed
    assert len(_settings) == 2 and all(r["ok"] for r in _settings)
    assert not any("SNAPSHOT" in json.dumps(r) for r in _settings)            # no content recorded


@pytest.mark.asyncio
async def test_a_count_mismatch_or_failed_upload_is_a_failure(_settings):
    async with _clusters({"a": 10, "b": 20, "c": 5}, dst_after={"a": 9}, fail_upload=("b",)) as c:
        out = await qm.migrate_all(c)
    assert out["a"]["ok"] is False and "differ" in out["a"]["error"]
    assert out["b"]["ok"] is False and "HTTPStatusError" in out["b"]["error"]
    assert out["c"]["ok"] is True                                     # one failure doesn't stop the rest


@pytest.mark.asyncio
async def test_nothing_happens_once_the_target_is_the_current_cluster(monkeypatch):
    monkeypatch.setattr(qm.settings, "qdrant_base", DST)
    assert qm.wanted() is False
    assert await qm.migrate_all() == {}
    monkeypatch.setattr(qm.settings, "qdrant_migrate_to_base", "")
    assert qm.wanted() is False
    await qm.run_once_after_boot(delay_s=0)        # returns at once, no network
