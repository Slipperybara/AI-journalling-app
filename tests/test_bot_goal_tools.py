"""Bot goal tool-calling tests.

Verifies the tool dispatch layer in `app.bot._dispatch_goal_tool` invokes the
right `app.goals.*` helper and surfaces structured errors back to the model.
"""
from unittest.mock import patch

from app.bot import _dispatch_goal_tool, _goal_tools


def test_tools_definitions_present():
    names = [t["function"]["name"] for t in _goal_tools()]
    assert names == ["add_goal", "fulfill_goal", "remove_goal", "rename_goal"]


def test_dispatch_add_goal_invokes_helper():
    with patch("app.goals.add_user_goal", return_value={"name": "X", "status": "active"}) as m:
        result = _dispatch_goal_tool("add_goal", '{"name": "X"}')
    assert result == {"ok": True, "goal": {"name": "X", "status": "active"}}
    m.assert_called_once_with("X")


def test_dispatch_add_goal_at_cap_returns_error():
    from app.goals import GoalCapReachedError
    with patch("app.goals.add_user_goal", side_effect=GoalCapReachedError("X")):
        result = _dispatch_goal_tool("add_goal", '{"name": "X"}')
    assert result == {
        "error": "cap_reached",
        "detail": "3 active goals already; fulfill or remove one first",
    }


def test_dispatch_add_goal_already_exists_returns_error():
    from app.goals import GoalExistsError
    with patch("app.goals.add_user_goal", side_effect=GoalExistsError("X")):
        result = _dispatch_goal_tool("add_goal", '{"name": "X"}')
    assert result["error"] == "already_exists"


def test_dispatch_fulfill_invokes_mark_fulfilled():
    with patch("app.goals.mark_fulfilled", return_value={"name": "X", "status": "fulfilled"}) as m:
        result = _dispatch_goal_tool("fulfill_goal", '{"name": "X"}')
    assert result["ok"] is True
    m.assert_called_once_with("X")


def test_dispatch_remove_invokes_mark_removed():
    with patch("app.goals.mark_removed", return_value={"name": "X", "status": "removed"}) as m:
        result = _dispatch_goal_tool("remove_goal", '{"name": "X"}')
    assert result["ok"] is True
    m.assert_called_once_with("X")


def test_dispatch_remove_unknown_returns_not_found():
    from app.goals import GoalNotFoundError
    with patch("app.goals.mark_removed", side_effect=GoalNotFoundError("X")):
        result = _dispatch_goal_tool("remove_goal", '{"name": "X"}')
    assert result["error"] == "not_found"


def test_dispatch_rename_invokes_rename_goal():
    with patch("app.goals.rename_goal", return_value={"name": "B", "status": "active"}) as m:
        result = _dispatch_goal_tool("rename_goal", '{"old_name": "A", "new_name": "B"}')
    assert result["ok"] is True
    m.assert_called_once_with("A", "B")


def test_dispatch_unknown_tool_returns_error():
    result = _dispatch_goal_tool("nonexistent_tool", "{}")
    assert result["error"] == "unknown_tool"


def test_dispatch_bad_json_returns_error():
    result = _dispatch_goal_tool("add_goal", "{not-json")
    assert result["error"] == "invalid_arguments"
