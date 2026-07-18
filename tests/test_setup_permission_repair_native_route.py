from __future__ import annotations

import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]

RECOMMEND_PATH = (
    ROOT
    / "stoney_verify"
    / "commands_ext"
    / "public_setup_recommend.py"
)

SERVICE_PATH = (
    ROOT
    / "stoney_verify"
    / "setup_permission_repair_services.py"
)

RECOMMEND = RECOMMEND_PATH.read_text(
    encoding="utf-8"
)

SERVICE = SERVICE_PATH.read_text(
    encoding="utf-8"
)


def _tree() -> ast.Module:
    return ast.parse(
        RECOMMEND,
        filename=str(RECOMMEND_PATH),
    )


def _class(name: str) -> ast.ClassDef:
    matches = [
        node
        for node in _tree().body
        if isinstance(node, ast.ClassDef)
        and node.name == name
    ]

    assert len(matches) == 1
    return matches[0]


def _function(
    name: str,
) -> ast.AsyncFunctionDef:
    matches = [
        node
        for node in _tree().body
        if isinstance(node, ast.AsyncFunctionDef)
        and node.name == name
    ]

    assert len(matches) == 1
    return matches[0]


def test_native_permission_repair_route_exists() -> None:
    node = _function("_open_permission_repair")

    source = (
        ast.get_source_segment(
            RECOMMEND,
            node,
        )
        or ""
    )

    assert (
        "solid._require_setup_permission"
        in source
    )
    assert (
        "setup_permission_repair_services"
        in source
    )
    assert (
        "setup_permission_repair_services."
        "open_permission_repair"
        in source
    )


def test_advanced_options_button_uses_native_route() -> None:
    monitoring = _class("AdvancedMonitoringRepairView")

    methods = [
        node
        for node in monitoring.body
        if isinstance(node, ast.AsyncFunctionDef)
        and node.name == "permission_repair"
    ]

    assert len(methods) == 1

    method = methods[0]
    source = (
        ast.get_source_segment(
            RECOMMEND,
            method,
        )
        or ""
    )

    assert (
        "await _open_permission_repair(interaction)"
        in source
    )

    values: dict[str, object] = {}

    for decorator in method.decorator_list:
        if not (
            isinstance(decorator, ast.Call)
            and isinstance(
                decorator.func,
                ast.Attribute,
            )
            and decorator.func.attr == "button"
        ):
            continue

        for keyword in decorator.keywords:
            if keyword.arg in {
                "label",
                "custom_id",
                "row",
            }:
                values[keyword.arg] = (
                    ast.literal_eval(keyword.value)
                )

    assert values == {
        "label": "Fix Channel Permissions",
        "custom_id": (
            "dank_setup_advanced_monitoring:permission_repair"
        ),
        "row": 1,
    }


def test_advanced_options_describes_repair() -> None:
    assert (
        "🛠️ **Fix Channel Permissions** — check and fix "
        "access to Dank Shield channels."
        in RECOMMEND
    )


def test_manage_setup_rows_are_discord_safe() -> None:
    manage = _class("ManageSetupView")
    rows: dict[int, int] = {}

    for method in manage.body:
        if not isinstance(
            method,
            (
                ast.FunctionDef,
                ast.AsyncFunctionDef,
            ),
        ):
            continue

        for decorator in method.decorator_list:
            if not (
                isinstance(decorator, ast.Call)
                and isinstance(
                    decorator.func,
                    ast.Attribute,
                )
                and decorator.func.attr == "button"
            ):
                continue

            row = 0

            for keyword in decorator.keywords:
                if keyword.arg == "row":
                    row = int(
                        ast.literal_eval(
                            keyword.value
                        )
                    )

            rows[row] = rows.get(row, 0) + 1

    assert rows
    assert all(count <= 5 for count in rows.values())


def test_owned_permission_repair_service_remains() -> None:
    service_tree = ast.parse(
        SERVICE,
        filename=str(SERVICE_PATH),
    )

    names = {
        node.name
        for node in service_tree.body
        if isinstance(
            node,
            (
                ast.FunctionDef,
                ast.AsyncFunctionDef,
                ast.ClassDef,
            ),
        )
    }

    assert "open_permission_repair" in names
    assert "apply_permission_repair" in names
    assert "result_embed" in names
