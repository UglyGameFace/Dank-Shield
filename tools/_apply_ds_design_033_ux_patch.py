from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def read(rel: str) -> str:
    return (ROOT / rel).read_text(encoding="utf-8")


def write(rel: str, text: str) -> None:
    (ROOT / rel).write_text(text, encoding="utf-8")


def once(text: str, old: str, new: str, name: str) -> str:
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{name}: expected 1 match, found {count}")
    return text.replace(old, new, 1)


def regex_once(text: str, pattern: str, repl: str, name: str, *, flags: int = re.S) -> str:
    out, count = re.subn(pattern, repl, text, count=1, flags=flags)
    if count != 1:
        raise RuntimeError(f"{name}: expected 1 regex match, found {count}")
    return out


# ---------------------------------------------------------------------------
# 1. One native command owner. No startup validator or registry mutation.
# ---------------------------------------------------------------------------
startup_rel = "stoney_verify/startup_guards/__init__.py"
startup = read(startup_rel)
startup = once(
    startup,
    '    "stoney_verify.startup_guards.server_design_command_module_guard",\n',
    "",
    "remove design command startup guard registration",
)
startup = once(
    startup,
    '    "✅ server_design_command_module_guard active",\n',
    "",
    "remove design command startup guard log allowlist",
)
write(startup_rel, startup)

group_rel = "stoney_verify/commands_ext/public_design_group.py"
group = read(group_rel)
group = once(group, "    import stoney_verify.commands_ext as commands_ext\n", "", "remove unused commands_ext import")
group = once(
    group,
    '    # `design` is already part of the canonical public registry. Keep this tiny\n'
    '    # compatibility assertion local to registration instead of mutating command\n'
    '    # profiles through a startup guard.\n'
    '    allowed = set(getattr(commands_ext, "_ALLOWED_DANK_CHILDREN", set()) or set())\n'
    '    allowed.add("design")\n'
    '    commands_ext._ALLOWED_DANK_CHILDREN = allowed\n\n',
    '    # Command/profile ownership is declarative in commands_ext.__init__.py.\n'
    '    # Registration must not rewrite that registry at runtime.\n\n',
    "remove runtime allowed-child mutation",
)
write(group_rel, group)


# ---------------------------------------------------------------------------
# 2. Consolidated Studio is the only public registrar/open owner.
# ---------------------------------------------------------------------------
v2_rel = "stoney_verify/commands_ext/public_design_studio_v2.py"
v2 = read(v2_rel)
v2 = once(v2, "_PATCHED = False\n", "", "remove obsolete v2 patched flag")
v2 = once(
    v2,
    '        _analysis, repair_options, _summary = legacy._infer_live_majority_context(guild, options)  # type: ignore[attr-defined]\n'
    '        current = _safe_str(repair_options.get("separator_id"), "none")\n'
    '        selected = "bar_heavy" if current == "none" else current\n',
    '        theme = legacy._theme_from_options(options)  # type: ignore[attr-defined]\n'
    '        current = rule_service.effective_draft_separator(\n'
    '            options,\n'
    '            theme_separator=_safe_str(getattr(theme, "channel_separator", "none"), "none"),\n'
    '        )\n'
    '        selected = "bar_heavy" if current == "none" else current\n',
    "separator chooser uses authoritative saved draft",
)
v2 = regex_once(
    v2,
    r'\n\ndef register_public_design_studio_command\(bot: Any = None, tree: Any = None\) -> bool:\n.*?(?=\n\n__all__ = \[)',
    "",
    "remove duplicate v2 command registrar",
)
v2 = once(v2, '    "register_public_design_studio_command",\n', "", "remove v2 registrar export")
write(v2_rel, v2)

legacy_rel = "stoney_verify/commands_ext/public_design_studio.py"
legacy = read(legacy_rel)
legacy = once(legacy, "_PATCHED = False\n", "", "remove obsolete legacy patched flag")
legacy = regex_once(
    legacy,
    r'\n\nasync def open_design_studio\(interaction: discord\.Interaction\) -> None:\n.*?(?=\n\n__all__ = \[)',
    "",
    "remove duplicate legacy open and registrar",
)
legacy = once(legacy, '    "register_public_design_studio_command",\n', "", "remove legacy registrar export")
legacy = once(legacy, '    "open_design_studio",\n', "", "remove legacy open export")
write(legacy_rel, legacy)


# ---------------------------------------------------------------------------
# 3. Remove dead runtime-magic metadata. Native analysis/context is explicit.
# ---------------------------------------------------------------------------
plan_rel = "stoney_verify/services/server_design_plan_service.py"
plan = read(plan_rel)
count = plan.count('        plan_options["__use_live_majority_layout"] = True\n')
if count != 2:
    raise RuntimeError(f"smart-auto-detect magic marker count: expected 2, found {count}")
plan = plan.replace('        plan_options["__use_live_majority_layout"] = True\n', "")
write(plan_rel, plan)


# ---------------------------------------------------------------------------
# 4. Retired runtime-patch-era modules are gone, not merely sleeping in repo.
# ---------------------------------------------------------------------------
retired_files = [
    "stoney_verify/commands_ext/public_design_enhancements.py",
    "stoney_verify/startup_guards/server_design_command_module_guard.py",
    "stoney_verify/startup_guards/server_design_majority_layout_guard.py",
    "stoney_verify/startup_guards/server_design_strict_layout_guard.py",
    "stoney_verify/startup_guards/server_design_studio_command_guard.py",
]
for rel in retired_files:
    path = ROOT / rel
    if not path.exists():
        raise RuntimeError(f"retired file already missing unexpectedly: {rel}")
    path.unlink()

# Historical mutation scripts duplicate old implementations and are not runtime
# or validation assets. Keep permanent tests/audits, remove one-shot source mutators.
removed_migrations: list[str] = []
for pattern in ("tools/apply_dank_design_*.py", "tools/apply_p0_int_design_*.py"):
    for path in sorted(ROOT.glob(pattern)):
        removed_migrations.append(str(path.relative_to(ROOT)))
        path.unlink()
if len(removed_migrations) < 8:
    raise RuntimeError(f"expected historical design migration scripts, removed only {len(removed_migrations)}")


# ---------------------------------------------------------------------------
# 5. Update permanent tests/audits to assert absence rather than shim behavior.
# ---------------------------------------------------------------------------
consolidation_rel = "tests/test_design_studio_consolidation_032.py"
consolidation = read(consolidation_rel)
consolidation = once(
    consolidation,
    'ENHANCEMENTS = (ROOT / "stoney_verify/commands_ext/public_design_enhancements.py").read_text(encoding="utf-8")\n'
    'COMMAND_GUARD = (ROOT / "stoney_verify/startup_guards/server_design_command_module_guard.py").read_text(encoding="utf-8")\n',
    'RETIRED_DESIGN_PATHS = (\n'
    '    ROOT / "stoney_verify/commands_ext/public_design_enhancements.py",\n'
    '    ROOT / "stoney_verify/startup_guards/server_design_command_module_guard.py",\n'
    '    ROOT / "stoney_verify/startup_guards/server_design_majority_layout_guard.py",\n'
    '    ROOT / "stoney_verify/startup_guards/server_design_strict_layout_guard.py",\n'
    '    ROOT / "stoney_verify/startup_guards/server_design_studio_command_guard.py",\n'
    ')\n',
    "replace shim source constants with retired path list",
)
consolidation = regex_once(
    consolidation,
    r'def test_active_registration_does_not_activate_design_runtime_monkey_patch_guards\(\) -> None:\n.*?(?=\n\ndef test_legacy_bridge_is_small_explicit_navigation_help_and_apply_boundary)',
    'def test_active_registration_does_not_activate_design_runtime_monkey_patch_guards() -> None:\n'
    '    assert "public_design_studio_v2 as design" in GROUP\n'
    '    assert "activate_public_design_enhancements" not in GROUP\n'
    '    assert all(not path.exists() for path in RETIRED_DESIGN_PATHS)\n'
    '    assert "command_guard.build_design_plan =" not in PLAN\n'
    '    assert "DesignDoctorView =" not in PLAN\n'
    '    assert "__use_live_majority_layout" not in PLAN\n',
    "rewrite retired-runtime test",
)
consolidation = regex_once(
    consolidation,
    r'def test_design_command_guard_is_validation_only_not_registry_mutation\(\) -> None:\n.*?(?=\n\ndef test_setup_guard_no_longer_attaches_deprecated_design_command_shim)',
    'def test_retired_design_command_guard_is_not_a_startup_dependency() -> None:\n'
    '    startup = (ROOT / "stoney_verify/startup_guards/__init__.py").read_text(encoding="utf-8")\n'
    '    assert "server_design_command_module_guard" not in startup\n'
    '    assert "allowed.add(\\"design\\")" not in GROUP\n'
    '    assert "commands_ext._ALLOWED_DANK_CHILDREN =" not in GROUP\n',
    "rewrite command guard test",
)
consolidation = consolidation.replace(
    '        assert options["__use_live_majority_layout"] is True\n',
    '        assert "__use_live_majority_layout" not in options\n',
)
write(consolidation_rel, consolidation)

editor_state_rel = "tests/test_dank_design_editor_state_authority.py"
editor_state = read(editor_state_rel)
editor_state = regex_once(
    editor_state,
    r'def test_strict_layout_guard_has_no_persisted_design_option_rewriter\(\) -> None:\n.*?(?=\n\ndef test_recommended_strength_four_applies_selected_category_frame)',
    'def test_retired_strict_layout_guard_is_absent() -> None:\n'
    '    guard = Path("stoney_verify/startup_guards/server_design_strict_layout_guard.py")\n'
    '    assert not guard.exists()\n',
    "rewrite strict guard test",
)
write(editor_state_rel, editor_state)

newline_rel = "tests/test_server_design_no_visible_newline_artifacts.py"
newline_test = read(newline_rel)
newline_test = regex_once(
    newline_test,
    r'TARGETS = \[\n.*?\n\]\n',
    'TARGETS = [\n'
    '    Path("stoney_verify/commands_ext/public_design_studio.py"),\n'
    '    Path("stoney_verify/commands_ext/public_design_studio_v2.py"),\n'
    '    Path("stoney_verify/services/server_design_majority_layout.py"),\n'
    '    Path("stoney_verify/services/server_design_plan_service.py"),\n'
    '    Path("stoney_verify/services/server_design_rule_service.py"),\n'
    '    Path("stoney_verify/services/server_design_studio.py"),\n'
    ']\n',
    "retarget newline audit to active design owners",
)
write(newline_rel, newline_test)

# Permanent Smart Auto-Detect audit: native services exist, retired shims do not.
audit_rel = "tools/audit_dank_design_smart_auto_detect.py"
audit = read(audit_rel)
audit = once(
    audit,
    'DESIGN_ENHANCEMENTS = (ROOT / "stoney_verify/commands_ext/public_design_enhancements.py").read_text(encoding="utf-8")\n',
    'RETIRED = (\n'
    '    ROOT / "stoney_verify/commands_ext/public_design_enhancements.py",\n'
    '    ROOT / "stoney_verify/startup_guards/server_design_majority_layout_guard.py",\n'
    '    ROOT / "stoney_verify/startup_guards/server_design_strict_layout_guard.py",\n'
    ')\n',
    "audit retired path constants",
)
audit = once(
    audit,
    '    if "server_design_majority_layout_guard" in DESIGN_ENHANCEMENTS:\n'
    '        failures.append("public design enhancement hook still imports the majority startup guard")\n'
    '    if "server_design_strict_layout_guard" in DESIGN_ENHANCEMENTS:\n'
    '        failures.append("public design enhancement hook still imports the strict startup guard")\n',
    '    for retired in RETIRED:\n'
    '        if retired.exists():\n'
    '            failures.append(f"retired runtime-patch design module still exists: {retired.relative_to(ROOT)}")\n',
    "audit retired modules are absent",
)
audit = once(
    audit,
    '    if "__use_live_majority_layout" not in PLAN:\n'
    '        failures.append("native plan metadata no longer identifies Smart Auto-Detect previews")\n',
    '    if "__use_live_majority_layout" in PLAN:\n'
    '        failures.append("retired runtime-magic Smart Auto-Detect flag still exists in native plan service")\n',
    "audit retired magic is absent",
)
write(audit_rel, audit)

safe_rel = "tools/test_dank_design_safe_repair_cleanup_static.py"
safe = read(safe_rel)
safe = once(
    safe,
    'ENHANCEMENTS = (ROOT / "stoney_verify/commands_ext/public_design_enhancements.py").read_text(encoding="utf-8")\n',
    'RETIRED_ENHANCEMENTS = ROOT / "stoney_verify/commands_ext/public_design_enhancements.py"\n',
    "safe audit enhancement path",
)
safe = once(
    safe,
    '    assert "server_design_plan_service as plans" in V2\n'
    '    assert "server_design_majority_layout_guard" not in ENHANCEMENTS\n'
    '    assert "server_design_strict_layout_guard" not in ENHANCEMENTS\n'
    '    assert "server_design_majority_layout_guard" not in STARTUP\n'
    '    assert "server_design_strict_layout_guard" not in STARTUP\n',
    '    assert "server_design_plan_service as plans" in V2\n'
    '    assert not RETIRED_ENHANCEMENTS.exists()\n'
    '    assert "server_design_majority_layout_guard" not in STARTUP\n'
    '    assert "server_design_strict_layout_guard" not in STARTUP\n'
    '    assert "server_design_command_module_guard" not in STARTUP\n',
    "safe audit native ownership",
)
write(safe_rel, safe)

write(
    "tools/test_design_layout_helpers_not_startup.py",
    '''from __future__ import annotations\n\nfrom pathlib import Path\n\nROOT = Path(__file__).resolve().parents[1]\nSTARTUP = (ROOT / "stoney_verify/startup_guards/__init__.py").read_text(encoding="utf-8")\nPLAN = (ROOT / "stoney_verify/services/server_design_plan_service.py").read_text(encoding="utf-8")\nRULES = (ROOT / "stoney_verify/services/server_design_rule_service.py").read_text(encoding="utf-8")\nGROUP = (ROOT / "stoney_verify/commands_ext/public_design_group.py").read_text(encoding="utf-8")\n\nRETIRED = (\n    ROOT / "stoney_verify/commands_ext/public_design_enhancements.py",\n    ROOT / "stoney_verify/startup_guards/server_design_command_module_guard.py",\n    ROOT / "stoney_verify/startup_guards/server_design_majority_layout_guard.py",\n    ROOT / "stoney_verify/startup_guards/server_design_strict_layout_guard.py",\n    ROOT / "stoney_verify/startup_guards/server_design_studio_command_guard.py",\n)\n\n\ndef main() -> int:\n    failures: list[str] = []\n    for path in RETIRED:\n        if path.exists():\n            failures.append(f"retired design runtime shim still exists: {path.relative_to(ROOT)}")\n    if "server_design_command_module_guard" in STARTUP:\n        failures.append("startup registry still loads the retired design command guard")\n    if 'allowed.add("design")' in GROUP or "commands_ext._ALLOWED_DANK_CHILDREN =" in GROUP:\n        failures.append("native design registrar still mutates canonical registry state")\n    if "majority.build_category_aware_options" not in PLAN:\n        failures.append("native plan service lost category-aware planning")\n    if "persist_separator_choice" not in RULES or "reset_all_overrides" not in RULES:\n        failures.append("saved-rule service is missing separator/reset authority")\n    if "__use_live_majority_layout" in PLAN:\n        failures.append("retired runtime-magic design flag remains in plan service")\n    if failures:\n        print("DESIGN OWNERSHIP AUDIT FAILED")\n        for failure in failures:\n            print(f"- {failure}")\n        return 1\n    print("PASS design ownership is native; retired startup shims absent")\n    return 0\n\n\nif __name__ == "__main__":\n    raise SystemExit(main())\n''',
)

write(
    "tools/test_design_native_registration.py",
    '''from __future__ import annotations\n\nfrom pathlib import Path\n\nROOT = Path(__file__).resolve().parents[1]\nGROUP = (ROOT / "stoney_verify/commands_ext/public_design_group.py").read_text(encoding="utf-8")\nV2 = (ROOT / "stoney_verify/commands_ext/public_design_studio_v2.py").read_text(encoding="utf-8")\nLEGACY = (ROOT / "stoney_verify/commands_ext/public_design_studio.py").read_text(encoding="utf-8")\nREGISTRY = (ROOT / "stoney_verify/commands_ext/__init__.py").read_text(encoding="utf-8")\n\n\ndef main() -> int:\n    failures: list[str] = []\n    if '@dank_group.command(name="design"' not in GROUP:\n        failures.append("public_design_group does not own /dank design")\n    if "public_design_studio_v2 as design" not in GROUP:\n        failures.append("public registrar does not route to consolidated Studio")\n    if "register_public_design_studio_command" in V2:\n        failures.append("v2 still exposes a duplicate command registrar")\n    if "register_public_design_studio_command" in LEGACY:\n        failures.append("legacy Studio still exposes a duplicate command registrar")\n    if '"public_design_group"' not in REGISTRY or '"design"' not in REGISTRY:\n        failures.append("canonical commands_ext registry is missing design ownership")\n    if 'allowed.add("design")' in GROUP:\n        failures.append("registration still mutates allowed children")\n    if failures:\n        print("DESIGN NATIVE REGISTRATION FAILED")\n        for failure in failures:\n            print(f"- {failure}")\n        return 1\n    print("PASS one native /dank design registrar")\n    return 0\n\n\nif __name__ == "__main__":\n    raise SystemExit(main())\n''',
)

# Dedicated regression for the full ownership cleanup.
write(
    "tests/test_dank_design_runtime_ownership_033.py",
    '''from __future__ import annotations\n\nfrom pathlib import Path\n\nROOT = Path(__file__).resolve().parents[1]\nGROUP = (ROOT / "stoney_verify/commands_ext/public_design_group.py").read_text(encoding="utf-8")\nV2 = (ROOT / "stoney_verify/commands_ext/public_design_studio_v2.py").read_text(encoding="utf-8")\nLEGACY = (ROOT / "stoney_verify/commands_ext/public_design_studio.py").read_text(encoding="utf-8")\nPLAN = (ROOT / "stoney_verify/services/server_design_plan_service.py").read_text(encoding="utf-8")\nSTARTUP = (ROOT / "stoney_verify/startup_guards/__init__.py").read_text(encoding="utf-8")\nREGISTRY = (ROOT / "stoney_verify/commands_ext/__init__.py").read_text(encoding="utf-8")\n\nRETIRED = (\n    ROOT / "stoney_verify/commands_ext/public_design_enhancements.py",\n    ROOT / "stoney_verify/startup_guards/server_design_command_module_guard.py",\n    ROOT / "stoney_verify/startup_guards/server_design_majority_layout_guard.py",\n    ROOT / "stoney_verify/startup_guards/server_design_strict_layout_guard.py",\n    ROOT / "stoney_verify/startup_guards/server_design_studio_command_guard.py",\n)\n\n\ndef test_one_public_design_registration_owner() -> None:\n    assert GROUP.count('@dank_group.command(name="design"') == 1\n    assert "register_public_design_studio_command" not in V2\n    assert "register_public_design_studio_command" not in LEGACY\n    assert 'allowed.add("design")' not in GROUP\n    assert "commands_ext._ALLOWED_DANK_CHILDREN =" not in GROUP\n    assert '"public_design_group"' in REGISTRY\n    assert '"design"' in REGISTRY\n\n\ndef test_retired_runtime_patch_design_modules_are_physically_absent() -> None:\n    assert all(not path.exists() for path in RETIRED)\n    assert "server_design_command_module_guard" not in STARTUP\n    assert "server_design_majority_layout_guard" not in STARTUP\n    assert "server_design_strict_layout_guard" not in STARTUP\n\n\ndef test_native_plan_has_no_retired_runtime_magic_flag() -> None:\n    assert "__use_live_majority_layout" not in PLAN\n    assert "majority.build_category_aware_options" in PLAN\n    assert "majority.annotate_category_aware_plan_items" in PLAN\n\n\ndef test_separator_entry_uses_saved_authority_not_live_majority_guess() -> None:\n    start = V2.index("async def separator_only")\n    end = V2.index("async def back", start)\n    block = V2[start:end]\n    assert "effective_draft_separator" in block\n    assert "_infer_live_majority_context" not in block\n\n\ndef test_historical_design_mutators_are_removed() -> None:\n    assert not list((ROOT / "tools").glob("apply_dank_design_*.py"))\n    assert not list((ROOT / "tools").glob("apply_p0_int_design_*.py"))\n''',
)

# Dedicated workflow must compile active owners, not files we intentionally removed.
workflow_rel = ".github/workflows/dank-design-032.yml"
workflow = read(workflow_rel)
for line in (
    "      - 'stoney_verify/commands_ext/public_design_enhancements.py'\n",
    "      - 'stoney_verify/startup_guards/server_design_command_module_guard.py'\n",
    "      - 'stoney_verify/startup_guards/server_design_studio_command_guard.py'\n",
    "      - 'stoney_verify/startup_guards/server_design_strict_layout_guard.py'\n",
    "      - 'stoney_verify/startup_guards/server_design_majority_layout_guard.py'\n",
    "            stoney_verify/commands_ext/public_design_enhancements.py \\\n",
    "            stoney_verify/startup_guards/server_design_command_module_guard.py \\\n",
    "            stoney_verify/startup_guards/server_design_studio_command_guard.py \\\n",
    "            stoney_verify/startup_guards/server_design_strict_layout_guard.py \\\n",
    "            stoney_verify/startup_guards/server_design_majority_layout_guard.py\n",
):
    workflow = workflow.replace(line, "")
workflow = once(
    workflow,
    "          tests/test_design_studio_consolidation_032.py\n",
    "          tests/test_design_studio_consolidation_032.py\n          tests/test_dank_design_runtime_ownership_033.py\n",
    "add ownership regression to focused workflow",
)
write(workflow_rel, workflow)

print("DS-DESIGN-033 full redundancy cleanup staged")
print(f"Removed historical design migration scripts: {len(removed_migrations)}")
