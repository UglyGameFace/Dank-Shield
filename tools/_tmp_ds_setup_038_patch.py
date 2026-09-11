from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def replace(path: str, old: str, new: str, *, count: int = 1) -> None:
    target = ROOT / path
    text = target.read_text(encoding="utf-8")
    actual = text.count(old)
    if actual != count:
        raise RuntimeError(f"{path}: expected {count} occurrences, found {actual}: {old!r}")
    target.write_text(text.replace(old, new, count), encoding="utf-8")


# Preserve the already-registered canonical setup command across final compaction.
replace(
    "stoney_verify/commands_ext/public_command_surface_v2.py",
    'def _compact_dank_children(tree: Any) -> int:\n    for item in list(getattr(dank_group, "commands", []) or []):',
    'def _compact_dank_children(tree: Any) -> int:\n'
    '    setup_command = dank_group.get_command("setup")\n'
    '    if not isinstance(setup_command, app_commands.Command):\n'
    '        raise RuntimeError("canonical /dank setup is unavailable before public compaction")\n\n'
    '    for item in list(getattr(dank_group, "commands", []) or []):',
)
replace(
    "stoney_verify/commands_ext/public_command_surface_v2.py",
    '    dank_group.add_command(_standalone("home", "Open the complete Dank Shield control center.", open_compact_dank_home))\n'
    '    upload = app_commands.Command(',
    '    dank_group.add_command(_standalone("home", "Open the complete Dank Shield control center.", open_compact_dank_home))\n'
    '    # Re-add the exact canonical setup command object. This preserves its existing\n'
    '    # permission checks, callback, and single-owner setup implementation.\n'
    '    dank_group.add_command(setup_command)\n'
    '    upload = app_commands.Command(',
)
replace(
    "stoney_verify/commands_ext/public_command_surface_v2.py",
    '    if dank_children != ["home", "upload"]:',
    '    if dank_children != ["home", "setup", "upload"]:',
)
replace(
    "stoney_verify/commands_ext/public_command_surface_v2.py",
    '            "`/dank home` is the main doorway. `/mod`, `/ticket`, `/tickets`, and `/verify` are optional "\n'
    '            "fast doorways to the same guided centers. `/dank upload` exists only because Discord "',
    '            "`/dank home` is the main doorway. New server owners can use `/dank setup` directly. "\n'
    '            "`/mod`, `/ticket`, `/tickets`, and `/verify` are optional fast doorways. `/dank upload` exists only because Discord "',
)

# Final public surface also restores purge after compact-v2.
replace(
    "stoney_verify/commands_ext/public_exit_compact_surface.py",
    '    expected_children = ["home", "purge", "upload"]',
    '    expected_children = ["home", "purge", "setup", "upload"]',
)

# Canonical contract: setup is a deliberate first-run/admin doorway, not a retired alias.
replace(
    "stoney_verify/command_surface_contract.py",
    '# button cannot provide an attachment field. Purge is the intentional direct\n'
    '# destructive-action exception so staff can reach both message purge and\n'
    '# inactive-member purge without digging through nested menus.\n'
    'PUBLIC_DANK_CHILDREN: frozenset[str] = frozenset({"home", "purge", "upload"})',
    '# button cannot provide an attachment field. Purge is the intentional direct\n'
    '# destructive-action exception so staff can reach both message purge and\n'
    '# inactive-member purge without digging through nested menus. Setup is the\n'
    '# intentional first-run/admin doorway for server owners onboarding the bot.\n'
    'PUBLIC_DANK_CHILDREN: frozenset[str] = frozenset({"home", "purge", "setup", "upload"})',
)
replace(
    "stoney_verify/command_surface_contract.py",
    '        # Former normal-product shortcuts now owned by the Home mega menu.\n'
    '        "setup",\n'
    '        "overview",',
    '        # Former normal-product shortcuts now owned by the Home mega menu.\n'
    '        "overview",',
)

# Update the authoritative audits and existing regression expectations.
final_set_files = [
    "tools/audit_public_command_surface.py",
    "tools/audit_public_command_friction.py",
    "tools/test_public_status_command_surface_static.py",
    "tests/test_community_tools_surface.py",
    "tests/test_public_command_cleanup_contract_static.py",
    "tests/test_command_ux_024_reassertion.py",
    "tests/test_welcome_card_live_command_tree.py",
]
for path in final_set_files:
    target = ROOT / path
    text = target.read_text(encoding="utf-8")
    old = '{"home", "purge", "upload"}'
    if old not in text:
        raise RuntimeError(f"{path}: final /dank set expectation not found")
    target.write_text(text.replace(old, '{"home", "purge", "setup", "upload"}'), encoding="utf-8")

compact_marker_files = [
    "tools/audit_public_command_surface.py",
    "tools/test_community_tools_static.py",
    "tests/test_community_tools_surface.py",
    "tests/test_ui_first_dank_command_surface.py",
]
for path in compact_marker_files:
    target = ROOT / path
    text = target.read_text(encoding="utf-8")
    old = 'dank_children != [\\"home\\", \\"upload\\"]' if "'dank_children" in text else 'dank_children != ["home", "upload"]'
    # Source files themselves contain ordinary quotes; tests/audits may contain the
    # same text inside a Python string. Replace the plain source fragment globally.
    plain_old = 'dank_children != ["home", "upload"]'
    if plain_old not in text:
        raise RuntimeError(f"{path}: compact child marker not found")
    target.write_text(text.replace(plain_old, 'dank_children != ["home", "setup", "upload"]'), encoding="utf-8")

final_list_files = [
    "tools/audit_public_command_surface.py",
    "tools/audit_public_command_friction.py",
]
for path in final_list_files:
    target = ROOT / path
    text = target.read_text(encoding="utf-8")
    old = 'expected_children = ["home", "purge", "upload"]'
    if old not in text:
        raise RuntimeError(f"{path}: final child list marker not found")
    target.write_text(text.replace(old, 'expected_children = ["home", "purge", "setup", "upload"]'), encoding="utf-8")

# Make the UI-first static audit explicitly require the public setup command preservation.
replace(
    "tests/test_ui_first_dank_command_surface.py",
    "        '_standalone(\"home\",',\n        'name=\"upload\",',",
    "        '_standalone(\"home\",',\n        'setup_command = dank_group.get_command(\"setup\")',\n        'dank_group.add_command(setup_command)',\n        'name=\"upload\",',",
)

# Dedicated regression keeps setup public while advanced setup aliases remain hidden.
(ROOT / "tests/test_public_dank_setup_entrypoint_199.py").write_text(
    '''from __future__ import annotations\n\nfrom pathlib import Path\n\nfrom stoney_verify.command_surface_contract import (\n    PUBLIC_DANK_CHILDREN,\n    PUBLIC_HIDDEN_DANK_CHILDREN,\n)\n\nROOT = Path(__file__).resolve().parents[1]\n\n\ndef test_public_setup_is_a_canonical_direct_dank_child() -> None:\n    assert PUBLIC_DANK_CHILDREN == frozenset({"home", "purge", "setup", "upload"})\n    assert "setup" not in PUBLIC_HIDDEN_DANK_CHILDREN\n\n\ndef test_compactor_preserves_exact_canonical_setup_command() -> None:\n    source = (ROOT / "stoney_verify/commands_ext/public_command_surface_v2.py").read_text(encoding="utf-8")\n    assert 'setup_command = dank_group.get_command("setup")' in source\n    assert 'dank_group.add_command(setup_command)' in source\n    assert 'canonical /dank setup is unavailable before public compaction' in source\n    assert 'dank_children != ["home", "setup", "upload"]' in source\n\n\ndef test_final_surface_keeps_setup_plus_direct_purge_only() -> None:\n    source = (ROOT / "stoney_verify/commands_ext/public_exit_compact_surface.py").read_text(encoding="utf-8")\n    assert 'expected_children = ["home", "purge", "setup", "upload"]' in source\n\n\ndef test_advanced_setup_aliases_stay_hidden() -> None:\n    for name in (\n        "setup-access",\n        "setup-assistant",\n        "setup-by-id",\n        "setup-defaults",\n        "setup-find",\n        "setup-logs",\n        "setup-picker",\n        "setup-review",\n        "setup-start",\n        "setup-status",\n        "setup-tickets",\n        "setup-verify",\n        "setup-verify-ids",\n    ):\n        assert name in PUBLIC_HIDDEN_DANK_CHILDREN\n        assert name not in PUBLIC_DANK_CHILDREN\n''',
    encoding="utf-8",
)

# Record the real active task instead of leaving the already-merged category repair active.
(ROOT / "ACTIVE_TASK.md").write_text(
    '''# ACTIVE TASK\n\n## DS-SETUP-038 — Restore public `/dank setup` onboarding entrypoint\n\n**Status:** IMPLEMENTED / VALIDATION PENDING\n**Branch:** `fix/public-dank-setup-entrypoint-199`\n**Base:** `9dafb8b8371dfb7959f0bd0abf1b985464865fb2` (`main`, squash merge of PR #198)\n**Started:** 2026-09-11\n\n## User-visible failure\n\nNew server owners have no visible `/dank setup` command even though Dank Shield has a complete canonical setup hub. The setup command is registered correctly, then the final public command compactor removes every `/dank` child and only restores Home and Upload. The final Exit layer later restores Purge, leaving the deployed surface as `home`, `purge`, `upload`.\n\n## Authoritative root cause\n\n`stoney_verify/commands_ext/public_setup_group.py` owns the existing unified setup flow. `stoney_verify/commands_ext/public_command_surface_v2.py` was deleting that command object during final compaction, while `stoney_verify/command_surface_contract.py` explicitly classified `setup` as hidden. This was command-surface policy drift, not missing setup functionality.\n\n## Repair\n\n- Capture the already-registered canonical `setup` command before compact-v2 removes children.\n- Re-add that exact command object after Home, preserving its existing callback, permissions, and setup ownership.\n- Keep the final public `/dank` surface limited to `home`, `purge`, `setup`, `upload`.\n- Remove only the unified `setup` command from the hidden-child contract.\n- Keep advanced/internal aliases such as `setup-tickets`, `setup-verify`, `setup-logs`, `setup-status`, and repair/migration setup commands hidden.\n- Keep the existing `/dank home` Setup & Settings button and all underlying setup modules unchanged.\n\n## Validation required\n\n- focused command-surface/setup regressions and audits;\n- Python compile and diff check;\n- full repository pytest/static audit gate on one exact head;\n- final scope/review/base-drift check;\n- squash merge only after exact-head green;\n- post-merge main CI and Discloud exact-commit deployment success;\n- production acceptance: `/dank setup` appears and opens the canonical setup hub for an authorized server owner without exposing retired setup aliases.\n''',
    encoding="utf-8",
)

print("DS-SETUP-038 patch applied")
