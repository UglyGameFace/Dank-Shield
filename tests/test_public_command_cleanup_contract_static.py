from __future__ import annotations

from pathlib import Path

from stoney_verify.command_surface_contract import PUBLIC_DANK_CHILDREN

SOURCE = Path("stoney_verify/startup_guards/slash_command_cleanup.py").read_text(encoding="utf-8")


def test_slash_cleanup_consumes_canonical_public_dank_contract() -> None:
    assert PUBLIC_DANK_CHILDREN == frozenset({"home", "upload"})
    assert "from stoney_verify.command_surface_contract import PUBLIC_DANK_CHILDREN" in SOURCE
    assert "ALLOWED_DANK_CHILDREN = set(PUBLIC_DANK_CHILDREN)" in SOURCE
    assert 'COMMAND_CLEANUP_EPOCH = "2026-08-08-public-command-contract-v2-mega-menu"' in SOURCE
    assert 'ALLOWED_DANK_CHILDREN = {\n    "setup",' not in SOURCE
    for retired_root in ("ticket-intake", "ticket-category", "ticket-panel"):
        assert f'"{retired_root}"' in SOURCE
