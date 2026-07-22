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


def test_each_guided_target_has_an_exact_requirement_key():
    builder = _function("_guided_setup_target")

    returns = [
        node
        for node in ast.walk(builder)
        if isinstance(node, ast.Return)
        and isinstance(node.value, ast.Tuple)
    ]

    assert returns
    assert all(len(node.value.elts) == 4 for node in returns)

    title_to_key = {
        node.value.elts[1].value: node.value.elts[3].value
        for node in returns
    }

    assert title_to_key["Choose the Ticket Staff Role"] == (
        "ticket_staff_role"
    )
    assert title_to_key["Choose the New-Ticket Folder"] == (
        "ticket_folder"
    )
    assert title_to_key["Choose the Verification Channel"] == (
        "verification_channel"
    )
    assert title_to_key["Choose the Approved-Member Role"] == (
        "verified_role"
    )
    assert title_to_key["Set Up the Private Voice Verify Room"] == (
        "voice_verify_channel"
    )
    assert title_to_key[
        "Set Up Voice Verify Staff Requests"
    ] == "voice_verify_staff_channel"
    assert title_to_key["Choose the Moderation Log Channel"] == (
        "modlog_channel"
    )


def test_guided_opener_passes_the_exact_requirement_key():
    opener = _function("_open_guided_setup")

    unpackings = [
        node.targets[0]
        for node in ast.walk(opener)
        if isinstance(node, ast.Assign)
        and len(node.targets) == 1
        and isinstance(node.targets[0], ast.Tuple)
        and isinstance(node.value, ast.Await)
        and isinstance(node.value.value, ast.Call)
        and _call_leaf(node.value.value)
        == "_guided_setup_target"
    ]

    assert len(unpackings) == 1
    assert [
        element.id
        for element in unpackings[0].elts
    ] == [
        "target",
        "title",
        "explanation",
        "requirement_key",
    ]

    view_calls = [
        node
        for node in ast.walk(opener)
        if isinstance(node, ast.Call)
        and _call_leaf(node) == "ContinueSetupView"
    ]

    assert len(view_calls) == 1

    keywords = {
        keyword.arg: ast.unparse(keyword.value)
        for keyword in view_calls[0].keywords
    }

    assert keywords["requirement_key"] == "requirement_key"


def test_continue_view_stores_and_forwards_requirement_key():
    view = _class("ContinueSetupView")
    methods = {
        node.name: node
        for node in view.body
        if isinstance(
            node,
            (ast.FunctionDef, ast.AsyncFunctionDef),
        )
    }
