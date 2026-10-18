from __future__ import annotations

from pathlib import Path

from stoney_verify.command_surface_contract import (
    PUBLIC_DANK_CHILDREN,
    PUBLIC_HIDDEN_DANK_CHILDREN,
)

ROOT = Path(__file__).resolve().parents[1]


def test_public_setup_is_a_canonical_direct_dank_child() -> None:
    assert {"home", "purge", "setup", "upload", "protection", "voice-safety", "diagnostics", "help", "cheers"} <= PUBLIC_DANK_CHILDREN
    assert len(PUBLIC_DANK_CHILDREN) == 18
    assert "setup" not in PUBLIC_HIDDEN_DANK_CHILDREN


def test_compactor_preserves_exact_canonical_setup_command() -> None:
    source = (ROOT / "stoney_verify/commands_ext/public_command_surface_v2.py").read_text(encoding="utf-8")
    assert 'setup_command = dank_group.get_command("setup")' in source
    assert 'dank_group.add_command(setup_command)' in source
    assert 'canonical /dank setup is unavailable before public compaction' in source
    assert 'expected_children = sorted(["home", "setup", "upload", "cheers"] + [name for name, _, _ in DANK_SHORTCUTS])' in source


def test_final_surface_keeps_setup_purge_and_curated_shortcuts() -> None:
    source = (ROOT / "stoney_verify/commands_ext/public_exit_compact_surface.py").read_text(encoding="utf-8")
    assert 'expected_children = sorted(PUBLIC_DANK_CHILDREN)' in source


def test_advanced_setup_aliases_stay_hidden() -> None:
    for name in (
        "setup-access",
        "setup-assistant",
        "setup-by-id",
        "setup-defaults",
        "setup-find",
        "setup-logs",
        "setup-picker",
        "setup-review",
        "setup-start",
        "setup-status",
        "setup-tickets",
        "setup-verify",
        "setup-verify-ids",
    ):
        assert name in PUBLIC_HIDDEN_DANK_CHILDREN
        assert name not in PUBLIC_DANK_CHILDREN
