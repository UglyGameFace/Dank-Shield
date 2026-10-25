"""Public launch documentation must reflect the final Dank Shield command tree.

Avoid loading the Discord bot or patching the runtime to check a read-only audit.
"""
from __future__ import annotations

from pathlib import Path
from runpy import run_path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "tools" / "audit_public_launch_readiness.py"
CONTRACT = ROOT / "stoney_verify" / "command_surface_contract.py"
CHECKLIST = ROOT / "docs" / "PUBLIC_LAUNCH_CHECKLIST.md"
README = ROOT / "README.md"


def _audit():
    return run_path(str(SCRIPT))


def test_readiness_script_uses_canonical_slash_and_context_commands() -> None:
    audit = _audit()
    contract = run_path(str(CONTRACT))
    expected = tuple(
        "/" + name for name in contract["PUBLIC_GLOBAL_COMMAND_NAMES"]
        if name != "View Dank Profile"
    )
    assert len(expected) == 8
    assert audit["REQUIRED_COMMANDS"] == expected
    assert audit["REQUIRED_CONTEXT_MENU"] == "View Dank Profile"
    assert audit["REQUIRED_DANK_CHILDREN"] == tuple(sorted(contract["PUBLIC_DANK_CHILDREN"]))
    assert "/ticket-panel" not in expected
    assert "/ticket-category" not in expected
    assert "/stoney" not in expected
    assert all("STONEY_" not in marker for marker in audit["REQUIRED_ENV"])


def test_current_launch_checklist_has_exact_public_command_blocks() -> None:
    audit = _audit()
    failures: list[str] = []
    audit["verify_documented_command_surface"](
        CHECKLIST.read_text(encoding="utf-8"), failures
    )
    assert failures == []


def test_readiness_audit_passes_present_contract_and_docs(capsys) -> None:
    assert _audit()["main"]() == 0
    assert "Public launch readiness audit passed" in capsys.readouterr().out


def test_stale_or_missing_slash_root_is_rejected() -> None:
    audit = _audit()
    checklist = CHECKLIST.read_text(encoding="utf-8")
    # Always corrupt a command from the *current* canonical surface.
    # /cinema and /movie were removed when Cinema became independent.
    current_root = audit["REQUIRED_COMMANDS"][0]
    assert current_root == "/dank"
    bad = checklist.replace(f"\n{current_root}\n", "\n/ticket-panel\n", 1)
    assert bad != checklist
    failures: list[str] = []
    audit["verify_documented_command_surface"](bad, failures)
    assert any("slash roots drifted" in issue for issue in failures)


def test_hidden_direct_dank_command_is_rejected() -> None:
    audit = _audit()
    checklist = CHECKLIST.read_text(encoding="utf-8")
    bad = checklist.replace("\nprotection\n", "\nspam\n", 1)
    assert bad != checklist
    failures: list[str] = []
    audit["verify_documented_command_surface"](bad, failures)
    assert any("direct /dank children drifted" in issue for issue in failures)


def test_cannot_forbid_main_public_root() -> None:
    audit = _audit()
    checklist = CHECKLIST.read_text(encoding="utf-8")
    bad = checklist.replace("\n/stoney\n", "\n/dank\n", 1)
    assert bad != checklist
    failures: list[str] = []
    audit["verify_documented_command_surface"](bad, failures)
    assert any("incorrectly forbidden" in issue for issue in failures)


def test_readiness_fails_when_real_required_env_value_disappears(tmp_path, capsys) -> None:
    audit = _audit()
    main = audit["main"]
    env_text = (ROOT / ".env.example").read_text(encoding="utf-8")
    assert "DANK_COMMAND_PROFILE=public" in env_text
    broken = tmp_path / ".env.example"
    broken.write_text(
        env_text.replace("DANK_COMMAND_PROFILE=public", "DANK_COMMAND_PROFILE=legacy"),
        encoding="utf-8",
    )
    main.__globals__["ENV_EXAMPLE"] = broken
    assert main() == 1
    assert ".env.example missing marker: DANK_COMMAND_PROFILE=public" in capsys.readouterr().out


def test_owner_docs_use_real_public_ticket_panel_manager() -> None:
    checklist = CHECKLIST.read_text(encoding="utf-8")
    readme = README.read_text(encoding="utf-8")
    assert "/tickets → Ticket Operations → Public Ticket Panel → Post / Update Panel" in checklist
    assert "/ticket-panel post" not in checklist
    assert "/ticket-panel post" not in readme
    assert "/dank spam panel" not in readme
    assert "/dank cleanup status" not in readme
