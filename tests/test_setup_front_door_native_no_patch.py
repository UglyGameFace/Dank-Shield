from __future__ import annotations

import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]

FRESH = (
    ROOT
    / "stoney_verify"
    / "commands_ext"
    / "public_setup_fresh_choice.py"
)
SOLID = (
    ROOT
    / "stoney_verify"
    / "commands_ext"
    / "public_setup_solid.py"
)
REGISTRY = (
    ROOT
    / "stoney_verify"
    / "startup_guards"
    / "__init__.py"
)
TICKET_STYLE = (
    ROOT
    / "stoney_verify"
    / "startup_guards"
    / "setup_ticket_tool_style_setup_guard.py"
)
MAIN = ROOT / "main.py"


def _source(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _owners(path: Path) -> dict[str, ast.AST]:
    source = _source(path)
    tree = ast.parse(source, filename=str(path))

    return {
        node.name: node
        for node in tree.body
        if isinstance(
            node,
            (
                ast.FunctionDef,
                ast.AsyncFunctionDef,
                ast.ClassDef,
            ),
        )
    }


def _owner_source(
    path: Path,
    name: str,
) -> str:
    source = _source(path)
    node = _owners(path)[name]

    return (
        ast.get_source_segment(
            source,
            node,
        )
        or ""
    )


def test_solid_builder_delegates_to_canonical_home() -> None:
    body = _owner_source(
        SOLID,
        "_build_main_setup_payload",
    )

    assert "public_setup_recommend as recommend" in body
    assert "recommend._product_main_setup_payload(" in body

    for marker in (
        "SolidSetupView()",
        "Review / Create Missing Items",
        "Current Setup Snapshot",
    ):
        assert marker not in body


def test_fresh_module_cannot_replace_setup_home() -> None:
    source = _source(FRESH)
    owners = _owners(FRESH)

    assert "_patch" not in owners
    assert "_PATCHED" not in source

    for marker in (
        "DANK_ENABLE_LEGACY_SETUP_CHOICE_HOME",
        "solid._build_main_setup_payload =",
        "recovery._ORIGINAL_BUILD_MAIN =",
        "FreshChoiceHomeView =",
        "FreshServerChoiceView =",
    ):
        assert marker not in source



def test_custom_home_returns_to_canonical_home() -> None:
    body = _owner_source(
        FRESH,
        "CustomServiceModeView",
    )
    home_index = body.index("async def home(")
    home = body[home_index:]

    assert "await recommend._home_edit(interaction)" in home


def test_setup_choices_and_guided_routes_remain() -> None:
    source = _source(FRESH)

    for marker in (
        "class SetupTypeChoiceView(",
        "class CustomServiceModeView(",
        "Continue Guided Setup",
        "recommend._open_guided_setup(",
        "recommend._open_manage_setup(",
        "id_verify_allowed_for_guild",
    ):
        assert marker in source


def test_ticket_style_payload_guard_is_retired() -> None:
    assert not TICKET_STYLE.exists()

    needle = "setup_ticket_tool_style_setup_guard"

    assert needle not in _source(REGISTRY)
    assert needle not in _source(MAIN)



def test_no_startup_guard_mutates_setup_home_payloads() -> None:
    guard_dir = (
        ROOT
        / "stoney_verify"
        / "startup_guards"
    )
    forbidden_attributes = {
        "_build_main_setup_payload",
        "FreshChoiceHomeView",
        "FreshServerChoiceView",
    }

    found: list[str] = []

    for path in guard_dir.glob("*.py"):
        source = _source(path)
        tree = ast.parse(
            source,
            filename=str(path),
        )

        for node in ast.walk(tree):
            targets: list[ast.AST] = []

            if isinstance(node, ast.Assign):
                targets.extend(node.targets)
            elif isinstance(node, ast.AnnAssign):
                targets.append(node.target)

            for target in targets:
                if (
                    isinstance(target, ast.Attribute)
                    and target.attr
                    in forbidden_attributes
                ):
                    found.append(
                        f"{path.name}:{node.lineno}:"
                        f"{target.attr}"
                    )

    assert found == []
