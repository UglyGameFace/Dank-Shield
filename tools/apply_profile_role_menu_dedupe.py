from __future__ import annotations

from pathlib import Path
import re
import subprocess

ROOT = Path(__file__).resolve().parents[1]
GUARD = ROOT / "stoney_verify/startup_guards/profile_role_editor_guard.py"
TEST = ROOT / "tools/test_profile_role_editor_guard_static.py"
WORKFLOW = ROOT / ".github/workflows/profile-runtime-diagnostics.yml"


def required_sub(text: str, pattern: str, replacement: str, *, label: str, flags: int = 0) -> str:
    updated, count = re.subn(pattern, replacement, text, count=1, flags=flags)
    if count != 1:
        raise RuntimeError(f"{label}: expected one match, found {count}")
    return updated


def main() -> None:
    print("dedupe: loading current guard")
    guard = GUARD.read_text(encoding="utf-8")

    guard = required_sub(
        guard,
        r"^_ORIGINAL_HANDLE_BUILDER = None\n",
        "",
        label="obsolete builder handler state",
        flags=re.M,
    )
    guard = required_sub(
        guard,
        r"^def _patch_builder_view\(profile: Any\) -> None:\n.*?(?=^def _patch_embeds\()",
        "",
        label="duplicate builder view",
        flags=re.M | re.S,
    )
    guard = required_sub(
        guard,
        r"def _patch_handlers\(profile: Any\) -> None:\n"
        r"    global _ORIGINAL_HANDLE_PROFILE, _ORIGINAL_HANDLE_BUILDER\n"
        r"    if _ORIGINAL_HANDLE_PROFILE is None:\n"
        r"        _ORIGINAL_HANDLE_PROFILE = getattr\(profile, \"_handle_profile_interaction\", None\)\n"
        r"    if _ORIGINAL_HANDLE_BUILDER is None:\n"
        r"        _ORIGINAL_HANDLE_BUILDER = getattr\(profile, \"_handle_builder_action\", None\)\n",
        "def _patch_handlers(profile: Any) -> None:\n"
        "    global _ORIGINAL_HANDLE_PROFILE\n"
        "    if _ORIGINAL_HANDLE_PROFILE is None:\n"
        "        _ORIGINAL_HANDLE_PROFILE = getattr(profile, \"_handle_profile_interaction\", None)\n",
        label="obsolete builder handler capture",
    )
    guard = required_sub(
        guard,
        r"    async def _open_role_editor\(interaction: discord\.Interaction\) -> None:\n"
        r".*?(?=    async def _handle_profile_interaction_patched)",
        "",
        label="duplicate builder action route",
        flags=re.S,
    )
    guard = required_sub(
        guard,
        r"    profile\._handle_builder_action = _handle_builder_action_patched\n"
        r"    profile\._handle_profile_interaction = _handle_profile_interaction_patched\n",
        "    profile._handle_profile_interaction = _handle_profile_interaction_patched\n",
        label="builder handler assignment",
    )
    guard = required_sub(
        guard,
        r"        _patch_panel_views\(profile\)\n"
        r"        _patch_builder_view\(profile\)\n"
        r"        _patch_embeds\(profile\)\n",
        "        _patch_panel_views(profile)\n        _patch_embeds(profile)\n",
        label="builder patch invocation",
    )

    replacements = (
        ("Suggest Role", "Suggest Profile Tag"),
        ("Suggest Profile Role", "Suggest Profile Tag"),
        ("Role you want added", "Profile tag you want added"),
        ("Profile Role Suggestion", "Profile Tag Suggestion"),
        ("A member suggested a role for the Profile Builder.", "A member suggested an optional profile tag."),
        ("Suggest a role", "Suggest a profile tag"),
        ("Missing role?", "Missing profile tag?"),
        ("Pick optional server roles/cosmetics", "Pick optional profile tags/cosmetics"),
        (
            "active; Profile Builder has server roles/cosmetics editor and member role suggestions",
            "active; native Profile Tags manager retained and member suggestions enabled",
        ),
    )
    for old, new in replacements:
        if old not in guard:
            raise RuntimeError(f"guard wording marker missing: {old!r}")
        guard = guard.replace(old, new)

    forbidden = ("builder:role_editor", "ProfileBuilderViewWithRoleEditor", "_ORIGINAL_HANDLE_BUILDER")
    remaining = [marker for marker in forbidden if marker in guard]
    if remaining:
        raise RuntimeError(f"duplicate builder markers remain: {remaining}")
    GUARD.write_text(guard, encoding="utf-8")
    print("dedupe: guard updated")

    test = TEST.read_text(encoding="utf-8")
    test = required_sub(
        test,
        r"^def test_builder_gets_profile_roles_cosmetics_editor_button\(\) -> None:\n"
        r".*?(?=^def |\Z)",
        '''def test_builder_reuses_one_native_profile_tags_manager_button() -> None:
    assert "ProfileBuilderViewWithRoleEditor" not in GUARD
    assert "builder:role_editor" not in GUARD
    assert "_open_role_editor" not in GUARD
    assert 'custom_id=f"{PROFILE_PREFIX}builder:cosmetics"' in PROFILE
    assert PROFILE.count('custom_id=f"{PROFILE_PREFIX}builder:cosmetics"') == 1


''',
        label="builder static contract",
        flags=re.M | re.S,
    )
    test = test.replace('    assert "Suggest Role" in GUARD\n', '    assert "Suggest Profile Tag" in GUARD\n')
    test = test.replace(
        "        test_builder_gets_profile_roles_cosmetics_editor_button,\n",
        "        test_builder_reuses_one_native_profile_tags_manager_button,\n",
    )
    if "test_builder_gets_profile_roles_cosmetics_editor_button" in test:
        raise RuntimeError("old duplicate-builder test name remains")
    TEST.write_text(test, encoding="utf-8")
    print("dedupe: tests updated")

    workflow = WORKFLOW.read_text(encoding="utf-8")
    workflow = required_sub(
        workflow,
        r"      - name: Remove duplicate profile role manager\n"
        r"        run: python tools/apply_profile_role_menu_dedupe\.py\n\n",
        "",
        label="dedupe workflow step",
    )
    WORKFLOW.write_text(workflow, encoding="utf-8")

    Path(__file__).unlink()
    subprocess.run(["git", "diff", "--check"], cwd=ROOT, check=True)
    print("dedupe: removed duplicate manager route and cleaned temporary script")


if __name__ == "__main__":
    main()
