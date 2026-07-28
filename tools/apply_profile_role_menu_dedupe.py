from __future__ import annotations

from pathlib import Path
import re
import subprocess

ROOT = Path(__file__).resolve().parents[1]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def write(path: str, text: str) -> None:
    (ROOT / path).write_text(text, encoding="utf-8")


def replace_required(path: str, old: str, new: str, *, label: str) -> None:
    text = read(path)
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{label}: expected one exact match, found {count}")
    write(path, text.replace(old, new, 1))


def replace_all_required(path: str, old: str, new: str, *, minimum: int, label: str) -> None:
    text = read(path)
    count = text.count(old)
    if count < minimum:
        raise RuntimeError(f"{label}: expected at least {minimum} matches, found {count}")
    write(path, text.replace(old, new))


def remove_between(path: str, start: str, end: str, *, label: str) -> None:
    text = read(path)
    start_index = text.find(start)
    if start_index < 0:
        raise RuntimeError(f"{label}: start marker was not found")
    end_index = text.find(end, start_index + len(start))
    if end_index < 0:
        raise RuntimeError(f"{label}: end marker was not found")
    write(path, text[:start_index] + text[end_index:])


def replace_function(path: str, function_name: str, replacement: str, *, label: str) -> None:
    text = read(path)
    pattern = rf"^def {re.escape(function_name)}\(.*?(?=^def |\Z)"
    updated, count = re.subn(pattern, replacement.rstrip() + "\n\n", text, count=1, flags=re.M | re.S)
    if count != 1:
        raise RuntimeError(f"{label}: expected one function, found {count}")
    write(path, updated)


def main() -> None:
    guard = "stoney_verify/startup_guards/profile_role_editor_guard.py"
    test_path = "tools/test_profile_role_editor_guard_static.py"
    workflow = ".github/workflows/profile-runtime-diagnostics.yml"

    replace_required(
        guard,
        "_ORIGINAL_HANDLE_BUILDER = None\n",
        "",
        label="remove obsolete builder handler state",
    )

    remove_between(
        guard,
        "def _patch_builder_view(profile: Any) -> None:\n",
        "def _patch_embeds(profile: Any) -> None:\n",
        label="remove duplicate builder role manager injection",
    )

    handler_text = read(guard)
    handler_text, count = re.subn(
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
        handler_text,
        count=1,
    )
    if count != 1:
        raise RuntimeError(f"remove obsolete builder handler capture: expected one match, found {count}")
    write(guard, handler_text)

    remove_between(
        guard,
        "    async def _open_role_editor(interaction: discord.Interaction) -> None:\n",
        "    async def _handle_profile_interaction_patched(interaction: discord.Interaction) -> bool:\n",
        label="remove duplicate builder action route",
    )

    replace_required(
        guard,
        "    profile._handle_builder_action = _handle_builder_action_patched\n"
        "    profile._handle_profile_interaction = _handle_profile_interaction_patched\n",
        "    profile._handle_profile_interaction = _handle_profile_interaction_patched\n",
        label="keep only suggestion interaction patch",
    )
    replace_required(
        guard,
        "        _patch_panel_views(profile)\n"
        "        _patch_builder_view(profile)\n"
        "        _patch_embeds(profile)\n",
        "        _patch_panel_views(profile)\n"
        "        _patch_embeds(profile)\n",
        label="stop patching the native builder manager",
    )
    replace_required(
        guard,
        '        _log("active; Profile Builder has server roles/cosmetics editor and member role suggestions")\n',
        '        _log("active; native Profile Tags manager retained and member suggestions enabled")\n',
        label="accurate guard startup log",
    )

    replace_all_required(
        guard,
        'label="Suggest Role"',
        'label="Suggest Profile Tag"',
        minimum=2,
        label="rename suggestion buttons",
    )
    for old, new, label in (
        (
            'class ProfileRoleSuggestionModal(discord.ui.Modal, title="Suggest Profile Role"):',
            'class ProfileRoleSuggestionModal(discord.ui.Modal, title="Suggest Profile Tag"):',
            "rename suggestion modal",
        ),
        ('            label="Role you want added",\n', '            label="Profile tag you want added",\n', "rename suggestion input"),
        ('        title="💡 Profile Role Suggestion",\n', '        title="💡 Profile Tag Suggestion",\n', "rename staff suggestion title"),
        (
            '        description="A member suggested a role for the Profile Builder. Staff/owner review is required.",\n',
            '        description="A member suggested an optional profile tag. Staff/owner review is required.",\n',
            "clarify staff suggestion description",
        ),
        ('            name="Suggest a role",\n', '            name="Suggest a profile tag",\n', "rename panel suggestion field"),
        ('            name="Missing role?",\n', '            name="Missing profile tag?",\n', "rename missing tag field"),
        (
            '            value="Pick optional server roles/cosmetics, or suggest one the owner should add.",\n',
            '            value="Pick optional profile tags/cosmetics, or suggest one the owner should add.",\n',
            "remove mixed server-role wording",
        ),
    ):
        replace_required(guard, old, new, label=label)
    replace_all_required(
        guard,
        "Use **Suggest Role**",
        "Use **Suggest Profile Tag**",
        minimum=2,
        label="rename suggestion instructions",
    )

    replace_function(
        test_path,
        "test_builder_gets_profile_roles_cosmetics_editor_button",
        '''def test_builder_reuses_one_native_profile_tags_manager_button() -> None:
    assert "ProfileBuilderViewWithRoleEditor" not in GUARD
    assert "builder:role_editor" not in GUARD
    assert "_open_role_editor" not in GUARD
    assert 'custom_id=f"{PROFILE_PREFIX}builder:cosmetics"' in PROFILE
    assert PROFILE.count('custom_id=f"{PROFILE_PREFIX}builder:cosmetics"') == 1''',
        label="replace duplicate builder contract",
    )
    replace_required(
        test_path,
        '    assert "Suggest Role" in GUARD\n',
        '    assert "Suggest Profile Tag" in GUARD\n',
        label="update suggestion button contract",
    )
    replace_required(
        test_path,
        "        test_builder_gets_profile_roles_cosmetics_editor_button,\n",
        "        test_builder_reuses_one_native_profile_tags_manager_button,\n",
        label="update static test runner",
    )

    workflow_text = read(workflow)
    workflow_text, count = re.subn(
        r"      - name: Remove duplicate profile role manager\n"
        r"        run: python tools/apply_profile_role_menu_dedupe\.py\n\n",
        "",
        workflow_text,
        count=1,
    )
    if count != 1:
        raise RuntimeError(f"remove dedupe materializer step: expected one match, found {count}")
    write(workflow, workflow_text)

    Path(__file__).unlink()
    subprocess.run(["git", "diff", "--check"], cwd=ROOT, check=True)

    source = read(guard)
    if any(marker in source for marker in ("builder:role_editor", "ProfileBuilderViewWithRoleEditor", "_ORIGINAL_HANDLE_BUILDER")):
        raise RuntimeError("duplicate Profile Tags builder route remains")
    if 'custom_id=f"{PROFILE_PREFIX}builder:cosmetics"' not in read("stoney_verify/commands_ext/public_self_roles_group.py"):
        raise RuntimeError("native Profile Tags manager route is missing")
    print("Removed duplicate Profile Tags manager route and clarified suggestion wording.")


if __name__ == "__main__":
    main()
