from __future__ import annotations

from pathlib import Path
import re
import subprocess

ROOT = Path(__file__).resolve().parents[1]
GUARD_PATH = ROOT / "stoney_verify/startup_guards/profile_role_editor_guard.py"
TEST_PATH = ROOT / "tools/test_profile_role_editor_guard_static.py"
PROFILE_PATH = ROOT / "stoney_verify/commands_ext/public_self_roles_group.py"


def _remove_function(text: str, name: str) -> str:
    return re.sub(
        rf"^def {re.escape(name)}\(.*?(?=^def |\Z)",
        "",
        text,
        count=1,
        flags=re.MULTILINE | re.DOTALL,
    )


def main() -> None:
    guard = GUARD_PATH.read_text(encoding="utf-8")

    # Keep the native builder:cosmetics route and remove only the guard-added
    # duplicate builder:role_editor route. Every operation is safe to repeat.
    guard = guard.replace("_ORIGINAL_HANDLE_BUILDER = None\n", "")
    guard = _remove_function(guard, "_patch_builder_view")
    guard = re.sub(
        r"def _patch_handlers\(profile: Any\) -> None:\n"
        r"    global _ORIGINAL_HANDLE_PROFILE(?:, _ORIGINAL_HANDLE_BUILDER)?\n"
        r"    if _ORIGINAL_HANDLE_PROFILE is None:\n"
        r"        _ORIGINAL_HANDLE_PROFILE = getattr\(profile, \"_handle_profile_interaction\", None\)\n"
        r"(?:    if _ORIGINAL_HANDLE_BUILDER is None:\n"
        r"        _ORIGINAL_HANDLE_BUILDER = getattr\(profile, \"_handle_builder_action\", None\)\n)?",
        "def _patch_handlers(profile: Any) -> None:\n"
        "    global _ORIGINAL_HANDLE_PROFILE\n"
        "    if _ORIGINAL_HANDLE_PROFILE is None:\n"
        "        _ORIGINAL_HANDLE_PROFILE = getattr(profile, \"_handle_profile_interaction\", None)\n",
        guard,
        count=1,
    )
    guard = re.sub(
        r"\n    async def _open_role_editor\(interaction: discord\.Interaction\) -> None:\n"
        r".*?(?=\n    async def _handle_profile_interaction_patched\(interaction: discord\.Interaction\) -> bool:\n)",
        "",
        guard,
        count=1,
        flags=re.DOTALL,
    )
    guard = guard.replace("    profile._handle_builder_action = _handle_builder_action_patched\n", "")
    guard = guard.replace("        _patch_builder_view(profile)\n", "")

    wording = (
        (
            'active; Profile Builder has server roles/cosmetics editor and member role suggestions',
            'active; native Profile Tags manager retained and member suggestions enabled',
        ),
        ("Suggest Profile Role", "Suggest Profile Tag"),
        ("Suggest Role", "Suggest Profile Tag"),
        ("Role you want added", "Profile tag you want added"),
        ("Profile Role Suggestion", "Profile Tag Suggestion"),
        (
            "A member suggested a role for the Profile Builder.",
            "A member suggested an optional profile tag.",
        ),
        ("Suggest a role", "Suggest a profile tag"),
        ("Missing role?", "Missing profile tag?"),
        ("Pick optional server roles/cosmetics", "Pick optional profile tags/cosmetics"),
        ("Use **Suggest Role**", "Use **Suggest Profile Tag**"),
        ("Role suggestion sent to staff", "Profile tag suggestion sent to staff"),
        ("role suggestions", "profile tag suggestions"),
        ("role suggestion review", "profile tag suggestion review"),
    )
    for old, new in wording:
        guard = guard.replace(old, new)

    GUARD_PATH.write_text(guard, encoding="utf-8")

    test = TEST_PATH.read_text(encoding="utf-8")
    if "def test_builder_gets_profile_roles_cosmetics_editor_button" in test:
        test, count = re.subn(
            r"^def test_builder_gets_profile_roles_cosmetics_editor_button\(\) -> None:\n"
            r".*?(?=^def |^if __name__ == \"__main__\":|\Z)",
            '''def test_builder_reuses_one_native_profile_tags_manager_button() -> None:
    assert "ProfileBuilderViewWithRoleEditor" not in GUARD
    assert "builder:role_editor" not in GUARD
    assert "_open_role_editor" not in GUARD
    assert "_ORIGINAL_HANDLE_BUILDER" not in GUARD
    assert 'custom_id=f"{PROFILE_PREFIX}builder:cosmetics"' in PROFILE
    assert PROFILE.count('custom_id=f"{PROFILE_PREFIX}builder:cosmetics"') == 1


''',
            test,
            count=1,
            flags=re.MULTILINE | re.DOTALL,
        )
        if count != 1:
            raise RuntimeError("Could not replace the obsolete duplicate-manager test.")
    test = test.replace(
        "test_builder_gets_profile_roles_cosmetics_editor_button,",
        "test_builder_reuses_one_native_profile_tags_manager_button,",
    )
    test = test.replace('assert "Suggest Role" in GUARD', 'assert "Suggest Profile Tag" in GUARD')
    TEST_PATH.write_text(test, encoding="utf-8")

    final_guard = GUARD_PATH.read_text(encoding="utf-8")
    forbidden = (
        "ProfileBuilderViewWithRoleEditor",
        "builder:role_editor",
        "_open_role_editor",
        "_ORIGINAL_HANDLE_BUILDER",
        "_handle_builder_action_patched",
        "Suggest Role",
        "Suggest Profile Role",
        "Server Roles / Cosmetics",
    )
    remaining = [marker for marker in forbidden if marker in final_guard]
    if remaining:
        raise RuntimeError(f"Duplicate or mixed Profile Tags routes remain: {remaining}")

    profile = PROFILE_PATH.read_text(encoding="utf-8")
    native_route = 'custom_id=f"{PROFILE_PREFIX}builder:cosmetics"'
    if profile.count(native_route) != 1:
        raise RuntimeError("The native Profile Tags manager must have exactly one builder route.")

    Path(__file__).unlink()
    subprocess.run(["git", "diff", "--check"], cwd=ROOT, check=True)
    print("Removed duplicate Profile Tags manager route and normalized suggestion wording.")


if __name__ == "__main__":
    main()
