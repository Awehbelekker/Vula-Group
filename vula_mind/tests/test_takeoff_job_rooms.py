"""A takeoff job returns the rooms it read, so the dashboard's room schedule shows the real
plans instead of the demo project's rooms."""
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from vula.takeoff import api


def test_job_project_carries_the_room_list(tmp_path):
    room = SimpleNamespace(name="Boardroom", area=36.04, floor_finish="carpet",
                           ceiling_finish="acoustic_tile", wall_finish="painted_plaster")
    project = SimpleNamespace(title_block=SimpleNamespace(project_name="Site A"), sheets=[1, 2],
                              rooms=[room], gross_floor_area=120.0, confidence_overall=0.8,
                              extraction_notes=[])
    api._jobs["j1"] = {"status": "queued"}
    with patch.object(api, "PlanReader") as PR, patch.object(api, "BOQGenerator") as BG, \
         patch.object(api, "OrderManager", side_effect=RuntimeError("stop after BOQ")):
        PR.return_value.read = AsyncMock(return_value=project)
        BG.return_value.generate = AsyncMock(return_value=MagicMock(to_dict=lambda: {}))
        asyncio.run(api._process_plans("j1", tmp_path / "p.pdf", "t1", 15))
    p = api._jobs["j1"]["project"]
    assert p["rooms"] == 1
    assert p["room_list"] == [{"name": "Boardroom", "area": 36.0, "floor": "carpet",
                               "ceiling": "acoustic_tile", "walls": "painted_plaster"}]
