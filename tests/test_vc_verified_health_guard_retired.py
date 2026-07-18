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

SCOREBOARD = (
    ROOT
    / "stoney_verify"
    / "commands_ext"
    / "public_setup_scoreboard.py"
)


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


def _owner_source(path: Path, name: str) -> str:
    source = _source(path)
    node = _owners(path)[name]
    return ast.get_source_segment(source, node) or ""


def test_native_health_checks_verified_voice_access() -> None:
    body = _owner_source(
        RECOMMEND,
        "_build_plain_setup_health_embed",
    )

    helper_index = body.index(
        "_verified_role_voice_access("
    )
    ready_index = body.index(
        "ready = not blockers"
    )

    assert helper_index < ready_index
    assert "blockers.append(access_text)" in body
    assert "passing.append(access_text)" in body


def test_guided_target_checks_verified_voice_access() -> None:
    body = _owner_source(
        RECOMMEND,
        "_guided_setup_target",
    )

    helper_index = body.index(
        "_verified_role_voice_access("
    )
    ready_index = body.index(
        '"ready",'
    )

    assert helper_index < ready_index
    assert '"verified_voice_access"' in body
    assert (
        "Allow Approved Members Into Voice Verify"
        in body
    )
    assert "if not access_ok:" in body


def test_fix_next_opens_permission_instructions() -> None:
    body = _owner_source(
        RECOMMEND,
        "_open_guided_target",
    )

    assert (
        'requirement_key == "verified_voice_access"'
        in body
    )
    assert "Edit Channel → Permissions" in body
    assert "View Channel, Connect, Speak" in body
    assert "Fix Next Problem" in body
    assert "Fix Next Item" not in body


def test_scoreboard_mirrors_member_access_truth() -> None:
    owners = _owners(SCOREBOARD)

    assert "_verified_role_voice_access" in owners

    helper = _owner_source(
        SCOREBOARD,
        "_verified_role_voice_access",
    )
    voice_score = _owner_source(
        SCOREBOARD,
        "_voice_score",
    )

    for marker in (
        "voice.permissions_for(verified)",
        "view_channel",
        "connect",
        "speak",
        "View Channel",
        "Connect",
        "Speak",
    ):
        assert marker in helper

    assert (
        "await _verified_role_voice_access(guild, cfg)"
        in voice_score
    )
    assert "issues.append(access_text)" in voice_score
    assert "verified_voice_access" in voice_score
