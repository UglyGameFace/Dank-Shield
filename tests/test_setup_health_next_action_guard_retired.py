from __future__ import annotations

import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]

TARGET = (
    ROOT
    / "stoney_verify"
    / "startup_guards"
    / "setup_health_next_action_guard.py"
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
MAIN = ROOT / "main.py"
RECOMMEND = (
    ROOT
    / "stoney_verify"
    / "commands_ext"
    / "public_setup_recommend.py"
)


def _source(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _owners(path: Path) -> dict[str, ast.AST]:
    tree = ast.parse(
        _source(path),
        filename=str(path),
    )

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


def test_obsolete_guard_is_deleted() -> None:
    assert not TARGET.exists()


def test_startup_references_are_removed() -> None:
    marker = "setup_health_next_action_guard"

    assert marker not in _source(REGISTRY)
    assert marker not in _source(SELF_CHECK)
    assert marker not in _source(MAIN)


def test_obsolete_wrapper_markers_are_gone() -> None:
    forbidden = (
        "_health_next_action_wrapped",
        "_ORIGINAL_BUILD_HEALTH_EMBED",
        "_replace_next_action",
        "Recommended Next Click",
    )

    roots = (
        ROOT / "stoney_verify" / "commands_ext",
        ROOT / "stoney_verify" / "startup_guards",
    )

    for root in roots:
        for path in root.rglob("*.py"):
            source = _source(path)

            for marker in forbidden:
                assert marker not in source, (
                    f"{marker!r} remains in {path}"
                )


def test_native_feature_aware_health_owner_exists() -> None:
    owners = _owners(RECOMMEND)

    required = {
        "_build_plain_setup_health_embed",
        "_open_health_check",
        "SetupReviewView",
        "SetupReviewFixNextButton",
        "SetupReviewLaunchButton",
        "SetupReviewHomeButton",
    }

    assert required.issubset(owners)


def test_native_setup_check_owns_plain_next_step() -> None:
    source = _source(RECOMMEND)
    owner = _owners(RECOMMEND)[
        "_build_plain_setup_health_embed"
    ]
    body = ast.get_source_segment(
        source,
        owner,
    ) or ""

    assert 'name="What to press"' in body
    assert "Continue Setup" in body
    assert "Test & Launch" in body
    assert "Start / Continue Setup" not in body
    assert "Test / Launch" not in body
    assert "Use Existing Roles/Channels" not in body
    assert "Start Setup / Fix Missing" not in body


def test_native_review_owns_one_correct_main_action() -> None:
    source = _source(RECOMMEND)
    owner = _owners(RECOMMEND)["SetupReviewView"]
    body = ast.get_source_segment(
        source,
        owner,
    ) or ""

    assert "if ready:" in body
    assert "SetupReviewLaunchButton()" in body
    assert "SetupReviewFixNextButton()" in body
    assert "SetupReviewHomeButton()" in body
    assert "SetupReviewAdvancedButton()" not in body
    assert "SetupReviewChangeTypeButton()" not in body


def test_no_startup_guard_recreates_next_action_wrapper() -> None:
    for path in (
        ROOT
        / "stoney_verify"
        / "startup_guards"
    ).glob("*.py"):
        source = _source(path)

        assert "_health_next_action_wrapped" not in source
        assert "_replace_next_action" not in source
