from __future__ import annotations

import ast
from pathlib import Path
from types import SimpleNamespace

import pytest

from stoney_verify.startup_guards import (
    setup_feature_health_scoreboard as scoreboard,
)


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
            (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef),
        )
    }


def _owner_source(path: Path, name: str) -> str:
    source = _source(path)
    return ast.get_source_segment(source, _owners(path)[name]) or ""


def test_legacy_guard_is_deleted() -> None:
    assert not TARGET.exists()


def test_hidden_protection_loader_is_removed() -> None:
    assert "vc_verified_health_check_guard" not in _source(PROTECTION)


def test_canonical_setup_uses_session_access_not_approved_role_access() -> None:
    owners = _owners(RECOMMEND)
    assert "_verified_role_voice_access" not in owners

    health = _owner_source(RECOMMEND, "_build_plain_setup_health_embed")
    guided = _owner_source(RECOMMEND, "_guided_setup_target")
    dispatcher = _owner_source(RECOMMEND, "_open_guided_target")

    combined = "\n".join((health, guided, dispatcher))
    for forbidden in (
        "verified_voice_access",
        "Allow Approved Members Into Voice Verify",
        "Edit Channel → Permissions",
        "View Channel, Connect, Speak",
    ):
        assert forbidden not in combined

    assert "_has_typed_channel" in health
    assert "session-based" in health
    assert "active requester" in health
    assert "assigned staff" in health


def test_scoreboard_voice_health_uses_session_contract(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    voice = SimpleNamespace(name="Voice Verification")
    queue = SimpleNamespace(name="vc-verify-queue")
    staff = SimpleNamespace(name="Support Team")

    monkeypatch.setattr(scoreboard, "_voice_channel", lambda *_args: voice)
    monkeypatch.setattr(scoreboard, "_text_channel", lambda *_args: queue)
    monkeypatch.setattr(scoreboard, "_role", lambda *_args: staff)
    monkeypatch.setattr(scoreboard, "_can_use_channel", lambda *_args, **_kwargs: True)

    health = scoreboard._voice_score(
        SimpleNamespace(),
        {
            "vc_verify_channel_id": "101",
            "vc_verify_queue_channel_id": "202",
            # Deliberately no verified/member/approved role ID.
        },
        True,
    )

    assert health.status == "ready"
    assert "session-based" in health.summary
    assert "assigned-staff" in health.summary


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
                assert marker not in source, f"{marker!r} remains in {path}"


def test_has_role_accepts_alias_keys() -> None:
    body = _owner_source(RECOMMEND, "_has_role")
    assert "*keys: str" in body
    assert "for key in keys:" in body
