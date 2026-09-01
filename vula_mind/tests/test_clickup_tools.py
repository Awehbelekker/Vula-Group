"""Comments and assignment from WhatsApp — the gaps in ClickUp coverage (2026-09-01).

The team could create tasks, list them, change status and due dates, but couldn't comment,
read a discussion, or assign an existing task without opening ClickUp itself. All three act on
an EXISTING task matched by title fragment, following update_task_status_by_name's
resolve-first pattern so a typo'd title says so plainly instead of silently doing nothing.
"""
from unittest.mock import AsyncMock, patch

import pytest

from core.skills.clickup_admin import ClickUpAdminSkill, TOOL_SPECS


def _tool(name):
    return next((t for t in TOOL_SPECS if t["function"]["name"] == name), None)


def test_the_new_tools_are_registered():
    for n in ("add_comment", "list_comments", "assign_task"):
        assert _tool(n), f"{n} should be available to the agent"


def test_assign_tool_warns_against_assuming_success():
    desc = _tool("assign_task")["function"]["description"]
    assert "never assume" in desc.lower()


@pytest.mark.asyncio
async def test_add_comment_posts_to_the_matched_task():
    skill = ClickUpAdminSkill()
    with patch("vula.clickup.service.find_task",
               AsyncMock(return_value={"id": "t9", "name": "Site inspection"})), \
         patch("vula.clickup.service.add_comment",
               AsyncMock(return_value={"id": "c1", "task_id": "t9"})) as add:
        out = await skill._dispatch_tool("add_comment",
                                         {"title": "site insp", "comment": "Client rescheduled"},
                                         {"tenant_id": "digg-demo"})
    add.assert_awaited_once_with("digg-demo", "t9", "Client rescheduled")
    assert out["task"] == "Site inspection"


@pytest.mark.asyncio
async def test_add_comment_on_a_missing_task_says_so():
    skill = ClickUpAdminSkill()
    with patch("vula.clickup.service.find_task", AsyncMock(return_value=None)), \
         patch("vula.clickup.service.add_comment", AsyncMock()) as add:
        out = await skill._dispatch_tool("add_comment", {"title": "nope", "comment": "hi"},
                                         {"tenant_id": "digg-demo"})
    assert "No ClickUp task found" in out["error"]
    add.assert_not_awaited(), "must not comment on some other task"


@pytest.mark.asyncio
async def test_add_comment_refuses_an_empty_note():
    skill = ClickUpAdminSkill()
    with patch("vula.clickup.service.find_task",
               AsyncMock(return_value={"id": "t9", "name": "X"})), \
         patch("vula.clickup.service.add_comment", AsyncMock()) as add:
        out = await skill._dispatch_tool("add_comment", {"title": "x", "comment": "   "},
                                         {"tenant_id": "digg-demo"})
    assert "error" in out
    add.assert_not_awaited()


@pytest.mark.asyncio
async def test_list_comments_returns_the_discussion():
    skill = ClickUpAdminSkill()
    comments = [{"id": "c1", "text": "Moved to Thursday", "by": "Judy", "at": "1"}]
    with patch("vula.clickup.service.find_task",
               AsyncMock(return_value={"id": "t9", "name": "Site inspection"})), \
         patch("vula.clickup.service.list_comments", AsyncMock(return_value=comments)):
        out = await skill._dispatch_tool("list_comments", {"title": "site"}, {"tenant_id": "digg-demo"})
    assert out["count"] == 1
    assert out["comments"][0]["text"] == "Moved to Thursday"


@pytest.mark.asyncio
async def test_assign_task_routes_to_the_service():
    skill = ClickUpAdminSkill()
    with patch("vula.clickup.service.find_task",
               AsyncMock(return_value={"id": "t9", "name": "Site inspection"})), \
         patch("vula.clickup.service.assign_task",
               AsyncMock(return_value={"task_id": "t9", "assigned_to": "Judy"})) as assign:
        out = await skill._dispatch_tool("assign_task", {"title": "site", "assignee": "Judy"},
                                         {"tenant_id": "digg-demo"})
    assign.assert_awaited_once_with("digg-demo", "t9", "Judy")
    assert out["assigned_to"] == "Judy"


@pytest.mark.asyncio
async def test_assigning_to_an_unknown_person_reports_who_is_available():
    """Never leave a task quietly unassigned because a name didn't match."""
    from vula.clickup import service
    with patch.object(service, "_creds_or_raise", lambda t: {"token": "x", "team_id": "1"}), \
         patch.object(service, "resolve_assignee", AsyncMock(return_value=None)), \
         patch.object(service, "list_team_members",
                      AsyncMock(return_value=[{"id": 1, "username": "Judy"},
                                              {"id": 2, "username": "Nolo"}])):
        out = await service.assign_task("digg-demo", "t9", "Bob")
    assert "No ClickUp member matching 'Bob'" in out["error"]
    assert "Judy" in out["error"] and "Nolo" in out["error"]
