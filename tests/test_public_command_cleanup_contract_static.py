from __future__ import annotations

from pathlib import Path

from stoney_verify.command_surface_contract import PUBLIC_DANK_CHILDREN

SOURCE = Path("stoney_verify/startup_guards/slash_command_cleanup.py").read_text(encoding="utf-8")


def test_slash_cleanup_consumes_canonical_public_dank_contract() -> None:
    assert PUBLIC_DANK_CHILDREN
    assert "from stoney_verify.command_surface_contract import PUBLIC_DANK_CHILDREN" in SOURCE
    assert "ALLOWED_DANK_CHILDREN = set(PUBLIC_DANK_CHILDREN)" in SOURCE
    assert 'COMMAND_CLEANUP_EPOCH = "2026-07-19-public-command-contract-v1"' in SOURCE
    assert 'ALLOWED_DANK_CHILDREN = {\n    "setup",' not in SOURCE
