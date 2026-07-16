from __future__ import annotations

import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]

RECOMMEND = (
    ROOT
    / "stoney_verify"
    / "commands_ext"
    / "public_setup_recommend.py"
)
GUARD = (
    ROOT
    / "stoney_verify"
    / "startup_guards"
    / "setup_health_action_buttons_guard.py"
)
REGISTRY = (
    ROOT
    / "stoney_verify"
    / "startup_guards"
    / "__init__.py"
)
SELF_CHECK = (
    ROOT
    / "stoney_verify"
    / "startup_guards"
    / "setup_guided_flow_self_check.py"
)
SAVE_GUARD = (
    ROOT
    / "stoney_verify"
    / "startup_guards"
    / "setup_save_next_step_guard.py"
)
MAIN = ROOT / "main.py"


def _source(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _tree(path: Path) -> ast.Module:
    return ast.parse(
        _source(path),
        filename=str(path),
    )


def _owners(path: Path) -> dict[str, ast.AST]:
    return {
        node.name: node
        for node in _tree(path).body
        if isinstance(
            node,
            (
                ast.FunctionDef,
                ast.AsyncFunctionDef,
                ast.ClassDef,
            ),
        )
    }


def _method(
    class_node: ast.ClassDef,
    name: str,
) -> ast.FunctionDef | ast.AsyncFunctionDef:
    matches = [
        node
        for node in class_node.body
        if isinstance(
            node,
            (
                ast.FunctionDef,
                ast.AsyncFunctionDef,
            ),
        )
        and node.name == name
    ]

    assert len(matches) == 1
    return matches[0]


def _button_decorator(
    method: ast.FunctionDef | ast.AsyncFunctionDef,
) -> ast.Call:
    calls = [
        decorator
        for decorator in method.decorator_list
        if isinstance(decorator, ast.Call)
    ]

    assert len(calls) == 1
    return calls[0]


def _keyword_value(
    call: ast.Call,
    name: str,
):
    matches = [
        keyword
        for keyword in call.keywords
        if keyword.arg == name
    ]

    assert len(matches) == 1
    return ast.literal_eval(matches[0].value)


def test_obsolete_guard_is_deleted() -> None:
    assert not GUARD.exists()


def test_startup_references_are_removed() -> None:
    marker = "setup_health_action_buttons_guard"

    assert marker not in _source(REGISTRY)
    assert marker not in _source(SELF_CHECK)
    assert marker not in _source(MAIN)
    assert (
        "_health_action_buttons_wrapped"
        not in _source(SELF_CHECK)
    )
    assert (
        "health_action_buttons_not_wrapped"
        not in _source(SELF_CHECK)
    )


def test_stale_health_action_comment_is_removed() -> None:
    source = _source(SAVE_GUARD)

    assert "HealthActionView" not in source
    assert (
        "canonical guided Setup Review owns "
        "the next-action view"
    ) in source


def test_native_test_ticket_owners_exist() -> None:
    owners = _owners(RECOMMEND)

    required = {
        "_setup_test_ticket_channel_id",
        "_resolve_setup_test_ticket_channel",
        "_create_setup_test_ticket",
        "LaunchTestView",
    }

    assert required.issubset(owners)


def test_native_test_ticket_is_collision_safe() -> None:
    source = _source(RECOMMEND)
    node = _owners(RECOMMEND)[
        "_create_setup_test_ticket"
    ]
    body = ast.get_source_segment(
        source,
        node,
    ) or ""

    required = (
        "_SETUP_TEST_TICKET_LOCKS",
        "asyncio.Lock()",
        "if lock.locked():",
        "find_open_ticket_for_owner",
        "_resolve_setup_test_ticket_channel",
        'existing_category == "setup_test"',
        "Open Ticket Already Exists",
        "create_ticket_channel(",
        'category="setup_test"',
        'source="setup_health_test_ticket"',
        'matched_category_slug="setup_test"',
        "category_override=True",
    )

    for marker in required:
        assert marker in body


def test_test_ticket_obeys_native_setup_state() -> None:
    source = _source(RECOMMEND)
    node = _owners(RECOMMEND)[
        "_create_setup_test_ticket"
    ]
    body = ast.get_source_segment(
        source,
        node,
    ) or ""

    assert "await _launch_state(guild)" in body
    assert 'state.get("tickets")' in body
    assert "await _guided_setup_target(guild)" in body
    assert 'target != "ready"' in body
    assert "await _open_health_check(interaction)" in body


def test_launch_view_owns_test_ticket_button() -> None:
    view = _owners(RECOMMEND)["LaunchTestView"]

    assert isinstance(view, ast.ClassDef)

    method = _method(
        view,
        "create_test_ticket",
    )
    decorator = _button_decorator(method)

    assert (
        _keyword_value(decorator, "label")
        == "Create Test Ticket"
    )
    assert (
        _keyword_value(decorator, "custom_id")
        == "dank_setup_launch:create_test_ticket"
    )
    assert _keyword_value(decorator, "row") == 0

    body = ast.get_source_segment(
        _source(RECOMMEND),
        method,
    ) or ""

    assert (
        "await _create_setup_test_ticket(interaction)"
        in body
    )


def test_launch_disables_test_ticket_when_tickets_off() -> None:
    view = _owners(RECOMMEND)["LaunchTestView"]

    assert isinstance(view, ast.ClassDef)

    init = _method(
        view,
        "__init__",
    )
    body = ast.get_source_segment(
        _source(RECOMMEND),
        init,
    ) or ""

    assert (
        "self.create_test_ticket.disabled"
        in body
    )
    assert 'self.state.get("tickets")' in body


def test_global_health_view_hijack_is_gone() -> None:
    roots = (
        ROOT / "stoney_verify" / "commands_ext",
        ROOT / "stoney_verify" / "startup_guards",
    )

    forbidden = (
        "_health_action_buttons_wrapped",
        "_wrapped_edit_or_followup",
        "HealthActionView",
    )

    for root in roots:
        for path in root.rglob("*.py"):
            source = _source(path)

            for marker in forbidden:
                assert marker not in source, (
                    f"{marker!r} remains in {path}"
                )
