from __future__ import annotations

import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]

TARGET = (
    ROOT
    / "stoney_verify"
    / "startup_guards"
    / "vc_verified_health_check_guard.py"
)
RECOMMEND = (
    ROOT
    / "stoney_verify"
    / "commands_ext"
    / "public_setup_recommend.py"
)
SCOREBOARD = (
    ROOT
    / "stoney_verify"
    / "startup_guards"
    / "setup_feature_health_scoreboard.py"
)
PROTECTION = (
    ROOT
    / "stoney_verify"
    / "startup_guards"
    / "protection_center_invite_simple_flow_guard.py"
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

    return ast.get_source_segment(
        source,
        node,
    ) or ""


def test_legacy_guard_is_deleted() -> None:
    assert not TARGET.exists()


def test_hidden_protection_loader_is_removed() -> None:
    assert (
        "vc_verified_health_check_guard"
        not in _source(PROTECTION)
    )


def test_native_access_helper_owns_legacy_truth() -> None:
    owners = _owners(RECOMMEND)

    assert "_verified_role_voice_access" in owners

    body = _owner_source(
        RECOMMEND,
        "_verified_role_voice_access",
    )

    for marker in (
        "verified_role_id",
        "member_role_id",
        "approved_role_id",
        "vc_verify_channel_id",
        "vc_verify_vc_id",
        "voice_verify_channel_id",
        "permissions_for(role)",
        "view_channel",
        "connect",
        "speak",
        "View Channel",
        "Connect",
        "Speak",
    ):
        assert marker in body


def test_setup_health_uses_native_access_helper() -> None:
    body = _owner_source(
        RECOMMEND,
        "_build_plain_setup_health_embed",
    )

    assert "_verified_role_voice_access(" in body
    assert "if access_ok:" in body
    assert "passing.append(access_text)" in body
    assert "blockers.append(access_text)" in body


def test_guided_readiness_blocks_launch() -> None:
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


def test_continue_setup_opens_permission_instructions() -> None:
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
    assert "Continue Setup" in body
    assert "Fix Next Problem" not in body
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

    assert "_verified_role_voice_access(" in voice_score
    assert "blockers.append(member_access_text)" in voice_score


def test_no_production_health_wrapper_remains() -> None:
    forbidden = (
        "vc_verified_health_check_guard",
        "_ORIGINAL_HEALTH",
        "patched_health",
        "Verified VC Access",
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


def test_has_role_accepts_alias_keys() -> None:
    body = _owner_source(
        RECOMMEND,
        "_has_role",
    )

    assert "*keys: str" in body
    assert "for key in keys:" in body
