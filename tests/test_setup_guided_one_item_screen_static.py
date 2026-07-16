from __future__ import annotations

import ast
from pathlib import Path


SOURCE = Path(
    "stoney_verify/commands_ext/public_setup_recommend.py"
)
TEXT = SOURCE.read_text(encoding="utf-8")
TREE = ast.parse(TEXT, filename=str(SOURCE))


def _function(name: str):
    matches = [
        node
        for node in ast.walk(TREE)
        if isinstance(
            node,
            (ast.FunctionDef, ast.AsyncFunctionDef),
        )
        and node.name == name
    ]
    assert len(matches) == 1
    return matches[0]


def _class(name: str):
    matches = [
        node
        for node in TREE.body
        if isinstance(node, ast.ClassDef)
        and node.name == name
    ]
    assert len(matches) == 1
    return matches[0]


def _call_leaf(call: ast.Call) -> str:
    if isinstance(call.func, ast.Name):
        return call.func.id
    if isinstance(call.func, ast.Attribute):
        return call.func.attr
    return ""


def test_one_item_screen_has_both_plain_choices():
    assert "Choose one I already have" in TEXT
    assert "Create this for me" in TEXT
    assert "Back to Guided Setup" in TEXT

    _class("GuidedOneItemView")
    _class("GuidedExistingRoleSelect")
    _class("GuidedExistingChannelSelect")
    _class("GuidedCreateItemButton")


def test_exact_guided_items_use_the_one_item_screen():
    dispatcher = _function("_open_guided_target")
    source = ast.get_source_segment(TEXT, dispatcher) or ""

    assert "requirement_key in _GUIDED_ONE_ITEM_SPECS" in source
    assert "_open_guided_one_item" in source


def test_all_seven_exact_requirements_are_supported():
    assignment = next(
        node
        for node in TREE.body
        if isinstance(node, ast.AnnAssign)
        and isinstance(node.target, ast.Name)
        and node.target.id == "_GUIDED_ONE_ITEM_SPECS"
    )

    assert isinstance(assignment.value, ast.Dict)

    keys = {
        key.value
        for key in assignment.value.keys
        if isinstance(key, ast.Constant)
    }

    assert keys == {
        "ticket_staff_role",
        "ticket_folder",
        "verification_channel",
        "verified_role",
        "voice_verify_channel",
        "voice_verify_staff_channel",
        "modlog_channel",
    }


def test_voice_staff_selection_saves_all_supported_aliases():
    assert '"vc_verify_queue_channel_id"' in TEXT
    assert '"vc_queue_channel_id"' in TEXT
    assert '"vc_request_channel_id"' in TEXT
    assert '"vc_verify_requests_channel_id"' in TEXT


def test_selection_saves_and_advances_automatically():
    function = _function("_guided_save_existing_item")
    calls = {
        _call_leaf(call)
        for call in ast.walk(function)
        if isinstance(call, ast.Call)
    }

    assert "_save_config" in calls
    assert "_open_guided_setup" in calls
    assert "_guided_step_is_current" in calls


def test_creation_reuses_existing_default_helpers():
    function = _function("_guided_create_exact_item")
    calls = {
        _call_leaf(call)
        for call in ast.walk(function)
        if isinstance(call, ast.Call)
    }

    assert "_ensure_role" in calls
    assert "_ensure_category" in calls
    assert "_ensure_text" in calls
    assert "_ensure_voice" in calls

    function_source = ast.get_source_segment(TEXT, function) or ""

    assert "guild.create_role(" not in function_source
    assert "guild.create_category(" not in function_source
    assert "guild.create_text_channel(" not in function_source
    assert "guild.create_voice_channel(" not in function_source


def test_creation_is_stale_screen_and_feature_safe():
    function = _function("_guided_create_item")
    calls = {
        _call_leaf(call)
        for call in ast.walk(function)
        if isinstance(call, ast.Call)
    }

    assert "_guided_step_is_current" in calls
    assert "_save_config" in calls
    assert "_open_guided_setup" in calls


def test_broad_advanced_picker_routes_still_exist():
    dispatcher = _function("_open_guided_target")
    source = ast.get_source_segment(TEXT, dispatcher) or ""

    assert 'target == "roles"' in source
    assert 'target == "folders"' in source
    assert 'target == "channels"' in source
    assert 'target == "logs"' in source
