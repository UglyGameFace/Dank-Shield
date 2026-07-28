from __future__ import annotations

from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[1]


def replace_once(path: str, old: str, new: str, *, label: str) -> None:
    target = ROOT / path
    text = target.read_text(encoding="utf-8")
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{label}: expected one exact match, found {count}")
    target.write_text(text.replace(old, new, 1), encoding="utf-8")


def replace_all_required(path: str, old: str, new: str, *, minimum: int, label: str) -> None:
    target = ROOT / path
    text = target.read_text(encoding="utf-8")
    count = text.count(old)
    if count < minimum:
        raise RuntimeError(f"{label}: expected at least {minimum} matches, found {count}")
    target.write_text(text.replace(old, new), encoding="utf-8")


def main() -> None:
    guard = "stoney_verify/startup_guards/profile_role_editor_guard.py"

    replace_once(
        guard,
        "_ORIGINAL_HANDLE_BUILDER = None\n",
        "",
        label="remove obsolete builder handler state",
    )

    replace_once(
        guard,
        '''def _patch_builder_view(profile: Any) -> None:
    original_builder = profile.ProfileBuilderView
    prefix = profile.PROFILE_PREFIX

    class ProfileBuilderViewWithRoleEditor(original_builder):
        def __init__(self, *, author_id: int, ready: bool, fixable: bool, title: str) -> None:
            super().__init__(author_id=author_id, ready=ready, fixable=fixable, title=title)
            _retitle_profile_roles_button(self, prefix)
            cid = f"{prefix}builder:role_editor"
            if not _has_child(self, cid):
                self.add_item(_button(label=PROFILE_ROLE_EDITOR_LABEL, emoji="🧩", custom_id=cid, row=1, style=discord.ButtonStyle.primary))

    profile.ProfileBuilderView = ProfileBuilderViewWithRoleEditor


''',
        "",
        label="remove duplicate builder role manager injection",
    )

    replace_once(
        guard,
        '''def _patch_handlers(profile: Any) -> None:
    global _ORIGINAL_HANDLE_PROFILE, _ORIGINAL_HANDLE_BUILDER
    if _ORIGINAL_HANDLE_PROFILE is None:
        _ORIGINAL_HANDLE_PROFILE = getattr(profile, "_handle_profile_interaction", None)
    if _ORIGINAL_HANDLE_BUILDER is None:
        _ORIGINAL_HANDLE_BUILDER = getattr(profile, "_handle_builder_action", None)
''',
        '''def _patch_handlers(profile: Any) -> None:
    global _ORIGINAL_HANDLE_PROFILE
    if _ORIGINAL_HANDLE_PROFILE is None:
        _ORIGINAL_HANDLE_PROFILE = getattr(profile, "_handle_profile_interaction", None)
''',
        label="remove obsolete builder handler capture",
    )

    replace_once(
        guard,
        '''    async def _open_role_editor(interaction: discord.Interaction) -> None:
        guild = interaction.guild
        if guild is None:
            return await profile._reply(interaction, "This only works inside the server.", ok=False)
        await interaction.response.send_message(
            embed=await profile._profile_cosmetic_manager_embed(guild),
            view=profile.ProfileCosmeticRoleManagerView(author_id=int(interaction.user.id)),
            ephemeral=True,
            allowed_mentions=discord.AllowedMentions.none(),
        )

    async def _handle_builder_action_patched(interaction: discord.Interaction, action: str) -> bool:
        if str(action or "") == "role_editor":
            await _open_role_editor(interaction)
            return True
        if callable(_ORIGINAL_HANDLE_BUILDER):
            return await _ORIGINAL_HANDLE_BUILDER(interaction, action)
        return False

''',
        "",
        label="remove duplicate builder action route",
    )

    replace_once(
        guard,
        '''    profile._handle_builder_action = _handle_builder_action_patched
    profile._handle_profile_interaction = _handle_profile_interaction_patched
''',
        '''    profile._handle_profile_interaction = _handle_profile_interaction_patched
''',
        label="keep only suggestion interaction patch",
    )

    replace_once(
        guard,
        '''        _patch_panel_views(profile)
        _patch_builder_view(profile)
        _patch_embeds(profile)
''',
        '''        _patch_panel_views(profile)
        _patch_embeds(profile)
''',
        label="stop patching the native builder manager",
    )

    replace_once(
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
    replace_once(
        guard,
        'class ProfileRoleSuggestionModal(discord.ui.Modal, title="Suggest Profile Role"):',
        'class ProfileRoleSuggestionModal(discord.ui.Modal, title="Suggest Profile Tag"):',
        label="rename suggestion modal",
    )
    replace_once(
        guard,
        '            label="Role you want added",\n',
        '            label="Profile tag you want added",\n',
        label="rename suggestion input",
    )
    replace_once(
        guard,
        '        title="💡 Profile Role Suggestion",\n',
        '        title="💡 Profile Tag Suggestion",\n',
        label="rename staff suggestion title",
    )
    replace_once(
        guard,
        '        description="A member suggested a role for the Profile Builder. Staff/owner review is required.",\n',
        '        description="A member suggested an optional profile tag. Staff/owner review is required.",\n',
        label="clarify staff suggestion description",
    )
    replace_once(
        guard,
        '            name="Suggest a role",\n',
        '            name="Suggest a profile tag",\n',
        label="rename panel suggestion field",
    )
    replace_all_required(
        guard,
        "Use **Suggest Role**",
        "Use **Suggest Profile Tag**",
        minimum=2,
        label="rename suggestion instructions",
    )
    replace_once(
        guard,
        '            name="Missing role?",\n',
        '            name="Missing profile tag?",\n',
        label="rename missing tag field",
    )
    replace_once(
        guard,
        '            value="Pick optional server roles/cosmetics, or suggest one the owner should add.",\n',
        '            value="Pick optional profile tags/cosmetics, or suggest one the owner should add.",\n',
        label="remove mixed server-role wording",
    )

    test_path = "tools/test_profile_role_editor_guard_static.py"
    replace_once(
        test_path,
        '''def test_builder_gets_profile_roles_cosmetics_editor_button() -> None:
    assert "ProfileBuilderViewWithRoleEditor" in GUARD
    assert "Profile Tags & Cosmetics" in GUARD
    assert "builder:role_editor" in GUARD
    assert "_open_role_editor" in GUARD
''',
        '''def test_builder_reuses_one_native_profile_tags_manager_button() -> None:
    assert "ProfileBuilderViewWithRoleEditor" not in GUARD
    assert "builder:role_editor" not in GUARD
    assert "_open_role_editor" not in GUARD
    assert 'custom_id=f"{PROFILE_PREFIX}builder:cosmetics"' in PROFILE
    assert PROFILE.count('custom_id=f"{PROFILE_PREFIX}builder:cosmetics"') == 1
''',
        label="replace duplicate builder contract",
    )
    replace_once(
        test_path,
        "    assert \"Suggest Role\" in GUARD\n",
        "    assert \"Suggest Profile Tag\" in GUARD\n",
        label="update suggestion button contract",
    )
    replace_once(
        test_path,
        "        test_builder_gets_profile_roles_cosmetics_editor_button,\n",
        "        test_builder_reuses_one_native_profile_tags_manager_button,\n",
        label="update static test runner",
    )

    workflow = ".github/workflows/profile-runtime-diagnostics.yml"
    replace_once(
        workflow,
        '''      - name: Remove duplicate profile role manager
        run: python tools/apply_profile_role_menu_dedupe.py

''',
        "",
        label="remove dedupe materializer step",
    )

    Path(__file__).unlink()
    subprocess.run(["git", "diff", "--check"], cwd=ROOT, check=True)

    source = (ROOT / guard).read_text(encoding="utf-8")
    if "builder:role_editor" in source or "ProfileBuilderViewWithRoleEditor" in source:
        raise RuntimeError("duplicate Profile Tags builder route remains")
    print("Removed duplicate Profile Tags manager route and clarified suggestion wording.")


if __name__ == "__main__":
    main()
