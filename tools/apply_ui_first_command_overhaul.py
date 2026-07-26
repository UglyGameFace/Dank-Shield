from __future__ import annotations

"""One-time repository patch for the UI-first /dank command surface.

The workflow that runs this file deletes it before committing the resulting
implementation. Every replacement is exact and fail-closed so an unexpected
repository shape cannot silently produce a partial command tree.
"""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def write(path: str, text: str) -> None:
    (ROOT / path).write_text(text, encoding="utf-8")


def replace_once(path: str, old: str, new: str) -> None:
    text = read(path)
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{path}: expected one match, found {count}: {old[:140]!r}")
    write(path, text.replace(old, new, 1))


def replace_all_checked(path: str, old: str, new: str, *, minimum: int = 1) -> None:
    text = read(path)
    count = text.count(old)
    if count < minimum:
        raise RuntimeError(f"{path}: expected at least {minimum} matches, found {count}: {old[:140]!r}")
    write(path, text.replace(old, new))


# ---------------------------------------------------------------------------
# Persist member appearance choices separately from privacy booleans.
# ---------------------------------------------------------------------------
replace_once(
    "stoney_verify/profile_card_service.py",
    '''def normalize_preferences(value: Optional[Mapping[str, Any]]) -> dict[str, bool]:
    result = dict(DEFAULT_PROFILE_PREFERENCES)
    if isinstance(value, Mapping):
        for key in result:
            if key in value:
                result[key] = bool(value.get(key))
    return result


def effective_preferences(
    user_preferences: Optional[Mapping[str, Any]],
    guild_settings: Optional[Mapping[str, Any]],
) -> dict[str, bool]:
    global_values = normalize_preferences(user_preferences)
    local = dict(guild_settings or {})
    return {
        key: bool(global_values[key]) and bool(local.get(key, True))
        for key in global_values
    }
''',
    '''def normalize_preferences(value: Optional[Mapping[str, Any]]) -> dict[str, Any]:
    raw = dict(value or {}) if isinstance(value, Mapping) else {}
    result: dict[str, Any] = dict(DEFAULT_PROFILE_PREFERENCES)
    for key in DEFAULT_PROFILE_PREFERENCES:
        if key in raw:
            result[key] = bool(raw.get(key))
    result.update(normalize_member_profile_style(raw))
    return result


def effective_preferences(
    user_preferences: Optional[Mapping[str, Any]],
    guild_settings: Optional[Mapping[str, Any]],
) -> dict[str, Any]:
    global_values = normalize_preferences(user_preferences)
    local = dict(guild_settings or {})
    resolved = dict(global_values)
    for key in DEFAULT_PROFILE_PREFERENCES:
        resolved[key] = bool(global_values[key]) and bool(local.get(key, True))
    return resolved
''',
)
replace_once(
    "stoney_verify/profile_card_service.py",
    '''        preferences = normalize_preferences(current.get("preferences"))
        for key in DEFAULT_PROFILE_PREFERENCES:
            if key in updates and updates.get(key) is not None:
                preferences[key] = bool(updates.get(key))
        payload = {
''',
    '''        preferences = normalize_preferences(current.get("preferences"))
        for key in DEFAULT_PROFILE_PREFERENCES:
            if key in updates and updates.get(key) is not None:
                preferences[key] = bool(updates.get(key))
        for key in DEFAULT_MEMBER_PROFILE_STYLE:
            if key in updates and updates.get(key) is not None:
                preferences[key] = updates.get(key)
        preferences = normalize_preferences(preferences)
        payload = {
''',
)
replace_once(
    "stoney_verify/profile_card_service.py",
    '    "DEFAULT_PROFILE_PREFERENCES",\n',
    '    "DEFAULT_MEMBER_PROFILE_STYLE",\n    "DEFAULT_PROFILE_PREFERENCES",\n',
)


# ---------------------------------------------------------------------------
# Resolve independent member/server style before compact image rendering.
# ---------------------------------------------------------------------------
replace_once(
    "stoney_verify/profile_card_runtime.py",
    "from .profile_signature_renderer import render_member_profile_signature\n",
    "from .profile_signature_renderer import render_member_profile_signature\n"
    "from .profile_signature_style import effective_profile_style\n",
)
replace_once(
    "stoney_verify/profile_card_runtime.py",
    '''    image_bytes = await render_member_profile_signature(
        member,
        cfg=cfg,
        role_labels=_compact_role_labels(member) if show_roles else [],
        date_labels=_compact_date_labels(member) if show_dates else [],
        platform_labels=_compact_platform_labels(platforms),
    )
''',
    '''    image_bytes = await render_member_profile_signature(
        member,
        style=effective_profile_style(preferences, cfg),
        role_labels=_compact_role_labels(member) if show_roles else [],
        date_labels=_compact_date_labels(member) if show_dates else [],
        platform_labels=_compact_platform_labels(platforms),
    )
''',
)


# Image signatures must never be enabled where attachments cannot be posted.
replace_once(
    "stoney_verify/profile_card_setup_ui_core.py",
    '''        ("Embed Links", permissions.embed_links),
        ("Read Message History", permissions.read_message_history),
    )
''',
    '''        ("Embed Links", permissions.embed_links),
        ("Read Message History", permissions.read_message_history),
        ("Attach Files", permissions.attach_files),
    )
''',
)
replace_once(
    "stoney_verify/profile_card_runtime_core.py",
    '''            permissions.embed_links
            and permissions.read_message_history
        )
''',
    '''            permissions.embed_links
            and permissions.read_message_history
            and permissions.attach_files
        )
''',
)


# ---------------------------------------------------------------------------
# Finish the server-side Profile Signatures setup area.
# ---------------------------------------------------------------------------
replace_once(
    "stoney_verify/profile_card_setup_ui.py",
    '''    embed.add_field(
        name="Privacy and anti-repetition",
''',
    '''    embed.add_field(
        name="What members can customize",
        value=(
            "Theme, font, colors, background style, layout, avatar frame, privacy, platforms, and profile roles. "
            "Server managers choose channels, allowed information, and the starting visual defaults."
        ),
        inline=False,
    )
    embed.add_field(
        name="Privacy and anti-repetition",
''',
)
replace_once(
    "stoney_verify/profile_card_setup_ui.py",
    '''class ProfileCardSetupView(_core.ProfileCardSetupView):
''',
    '''class _ServerDefaultsButton(discord.ui.Button):
    def __init__(self) -> None:
        super().__init__(
            label="Server Signature Defaults",
            emoji="🎨",
            style=discord.ButtonStyle.primary,
            custom_id="dank_setup_profile_cards:defaults",
            row=3,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        from .profile_signature_studio import open_server_signature_defaults

        await open_server_signature_defaults(interaction)


class _ProfileRoleBuilderButton(discord.ui.Button):
    def __init__(self) -> None:
        super().__init__(
            label="Profile Panel & Roles",
            emoji="🎭",
            style=discord.ButtonStyle.secondary,
            custom_id="dank_setup_profile_cards:roles",
            row=3,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        from .commands_ext.public_self_roles_group import _post_profile_builder

        await _post_profile_builder(interaction, title="Profile Panel")


class ProfileCardSetupView(_core.ProfileCardSetupView):
''',
)
replace_once(
    "stoney_verify/profile_card_setup_ui.py",
    '''        self.add_item(_PreviewButton())
        self.add_item(_core._RefreshButton())
        self.add_item(_core._BackButton())
''',
    '''        self.add_item(_PreviewButton())
        self.add_item(_core._RefreshButton())
        self.add_item(_ServerDefaultsButton())
        self.add_item(_ProfileRoleBuilderButton())
        self.add_item(_core._BackButton())
''',
)


# Existing profile panels open the full member studio, not a narrow privacy page.
replace_all_checked(
    "stoney_verify/commands_ext/public_self_roles_group.py",
    'label="Privacy & Platforms"',
    'label="Signature Settings"',
)
replace_once(
    "stoney_verify/commands_ext/public_self_roles_group.py",
    '''    if suffix == "privacy":
        from .public_profile_cards import profile_settings

        await profile_settings(interaction)
        return True
''',
    '''    if suffix == "privacy":
        from stoney_verify.profile_signature_studio import open_profile_signature_studio

        await open_profile_signature_studio(interaction)
        return True
''',
)


# ---------------------------------------------------------------------------
# Restore Welcome & Join and Profile Signatures as separate setup destinations.
# ---------------------------------------------------------------------------
replace_once(
    "stoney_verify/commands_ext/public_setup_recommend.py",
    '''    embed.add_field(
        name="🎨 Server Design",
        value="Smart Auto-Detect, previews, styling, and undo tools.",
        inline=False,
    )
    embed.add_field(
        name="💾 Backups & History",
''',
    '''    embed.add_field(
        name="🎨 Server Design",
        value="Smart Auto-Detect, previews, styling, and undo tools.",
        inline=False,
    )
    embed.add_field(
        name="👋 Welcome & Join",
        value="Static welcome/start-here message, join-only image cards, and join/leave announcements.",
        inline=False,
    )
    embed.add_field(
        name="🪪 Profile Signatures",
        value="Compact live signatures, member appearance, privacy, platforms, roles, and server defaults.",
        inline=False,
    )
    embed.add_field(
        name="💾 Backups & History",
''',
)
replace_once(
    "stoney_verify/commands_ext/public_setup_recommend.py",
    'value="Open Tickets, Verification, Security, Logs, Design, Backups, and more.",',
    'value="Open Tickets, Verification, Security, Logs, Welcome & Join, Profile Signatures, Design, Backups, and more.",',
)
replace_once(
    "stoney_verify/commands_ext/public_setup_recommend.py",
    '''    @discord.ui.button(
        label="Member Profiles & Live Cards",
        emoji="🪪",
        style=discord.ButtonStyle.secondary,
        custom_id="dank_setup_features:profiles",
        row=3,
    )
''',
    '''    @discord.ui.button(
        label="Welcome & Join",
        emoji="👋",
        style=discord.ButtonStyle.secondary,
        custom_id="dank_setup_features:welcome",
        row=3,
    )
    async def welcome_join(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        _ = button
        from stoney_verify import welcome_setup_ui

        await welcome_setup_ui.open_welcome_setup(interaction)

    @discord.ui.button(
        label="Profile Signatures",
        emoji="🪪",
        style=discord.ButtonStyle.secondary,
        custom_id="dank_setup_features:profiles",
        row=3,
    )
''',
)


# ---------------------------------------------------------------------------
# Run the command compactor only after every additive registrar has finished.
# ---------------------------------------------------------------------------
replace_once(
    "stoney_verify/commands.py",
    '''try:
    from .commands_ext.public_profile_cards import register_public_profile_cards
except Exception as e:
''',
    '''try:
    from .commands_ext.public_profile_cards import register_public_profile_cards
except Exception as e:
''',
)
# Insert the compactor import after the profile registrar fallback block.
replace_once(
    "stoney_verify/commands.py",
    '''    def register_public_profile_cards(bot: Any, tree: Any) -> None:  # type: ignore
        return None


# ============================================================
# Kick timer bridges
''',
    '''    def register_public_profile_cards(bot: Any, tree: Any) -> None:  # type: ignore
        return None


try:
    from .commands_ext.public_command_hub import compact_public_dank_surface
except Exception as e:
    print(f"⚠️ commands.py failed to import public_command_hub: {repr(e)}")

    def compact_public_dank_surface(bot: Any, tree: Any) -> int:  # type: ignore
        return 0


# ============================================================
# Kick timer bridges
''',
)
replace_once(
    "stoney_verify/commands.py",
    '''try:
    register_public_profile_cards(bot, bot.tree)
except Exception as e:
    try:
        print(f"⚠️ commands.py failed to register public profile cards: {repr(e)}")
    except Exception:
        pass


# ============================================================
# Register centralized component interaction handler
''',
    '''try:
    register_public_profile_cards(bot, bot.tree)
except Exception as e:
    try:
        print(f"⚠️ commands.py failed to register public profile cards: {repr(e)}")
    except Exception:
        pass

try:
    compact_public_dank_surface(bot, bot.tree)
except Exception as e:
    try:
        print(f"❌ commands.py failed to compact /dank command surface: {repr(e)}")
    except Exception:
        pass
    raise


# ============================================================
# Register centralized component interaction handler
''',
)
replace_once(
    "stoney_verify/commands.py",
    '''    try:
        register_public_profile_cards(bot, tree)
    except Exception as e:
        try:
            print(f"⚠️ register_extra_commands profile cards failed: {repr(e)}")
        except Exception:
            pass


# ============================================================
# Events
''',
    '''    try:
        register_public_profile_cards(bot, tree)
    except Exception as e:
        try:
            print(f"⚠️ register_extra_commands profile cards failed: {repr(e)}")
        except Exception:
            pass

    compact_public_dank_surface(bot, tree)


# ============================================================
# Events
''',
)


# ---------------------------------------------------------------------------
# Regression tests and payload audit.
# ---------------------------------------------------------------------------
write(
    "tests/test_ui_first_dank_command_surface.py",
    '''from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_commands_compact_dank_after_all_additive_registrars() -> None:
    source = (ROOT / "stoney_verify/commands.py").read_text(encoding="utf-8")
    assert source.index("register_public_profile_cards(bot, bot.tree)") < source.index(
        "compact_public_dank_surface(bot, bot.tree)"
    )


def test_ui_first_surface_has_small_explicit_entry_set() -> None:
    source = (ROOT / "stoney_verify/commands_ext/public_command_hub.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    assert tree is not None
    for required in (
        '"home"',
        '"profile"',
        '"help"',
        '"setup"',
        '"status"',
        '"diagnostics"',
        '"welcome"',
    ):
        assert required in source
    assert "DANK_PAYLOAD_SAFETY_LIMIT = 7600" in source
    assert "dank_payload_size(tree)" in source


def test_welcome_and_profiles_are_separate_ui_destinations() -> None:
    setup_source = (ROOT / "stoney_verify/commands_ext/public_setup_recommend.py").read_text(encoding="utf-8")
    profile_source = (ROOT / "stoney_verify/profile_card_setup_ui.py").read_text(encoding="utf-8")
    welcome_source = (ROOT / "stoney_verify/welcome_setup_ui.py").read_text(encoding="utf-8")
    assert 'label="Welcome & Join"' in setup_source
    assert 'label="Profile Signatures"' in setup_source
    assert "Add Welcome Channel" not in profile_source
    assert "Welcome & Join • separate from Profile Signatures" in welcome_source
    assert "Profile Signatures" in profile_source


def test_member_signature_studio_exposes_age_friendly_controls() -> None:
    source = (ROOT / "stoney_verify/profile_signature_studio.py").read_text(encoding="utf-8")
    for label in (
        "Appearance",
        "Privacy",
        "Platforms",
        "Profile Roles",
        "Preview",
        "Reset My Look",
        "Theme",
        "Font",
        "Colors",
        "Background",
        "Layout",
        "Avatar Frame",
    ):
        assert f'label="{label}"' in source


def test_image_signatures_require_attach_files() -> None:
    setup_core = (ROOT / "stoney_verify/profile_card_setup_ui_core.py").read_text(encoding="utf-8")
    runtime_core = (ROOT / "stoney_verify/profile_card_runtime_core.py").read_text(encoding="utf-8")
    assert '("Attach Files", permissions.attach_files)' in setup_core
    assert "permissions.attach_files" in runtime_core
''',
)
write(
    "tools/test_dank_command_payload.py",
    '''from __future__ import annotations

import json

from stoney_verify.globals import bot
import stoney_verify.commands  # noqa: F401
from stoney_verify.commands_ext.public_command_hub import (
    DANK_PAYLOAD_SAFETY_LIMIT,
    dank_payload_size,
)
from stoney_verify.commands_ext.public_setup_group import dank_group

size = dank_payload_size(bot.tree)
children = sorted(str(getattr(command, "name", "")) for command in dank_group.commands)
print(json.dumps({"dank_payload_size": size, "limit": DANK_PAYLOAD_SAFETY_LIMIT, "children": children}))
if size > DANK_PAYLOAD_SAFETY_LIMIT:
    raise SystemExit(f"/dank payload is too large: {size}/{DANK_PAYLOAD_SAFETY_LIMIT}")
if children != ["diagnostics", "help", "home", "profile", "setup", "status", "welcome"]:
    raise SystemExit(f"unexpected UI-first /dank children: {children}")
''',
)


# Update the active task instead of pretending unrelated warnings are fixed.
write(
    "ACTIVE_TASK.md",
    '''# ACTIVE TASK

## DS-COMMAND-UI-004 — UI-first command overhaul with complete Profile and Welcome setup

**Status:** IMPLEMENTATION / VALIDATION  
**Branch:** `fix/profile-command-payload-limit`  
**PR:** `#132`  
**Base:** current `main`

## Single Active Task Lock

Do not switch to unrelated implementation work until this correction reaches Definition of Done or the owner explicitly force-switches tasks.

## Root causes confirmed

- `/dank` accumulated whole nested feature trees and exceeded Discord's 8,000-character application-command group limit.
- Profile signatures exposed privacy/platform toggles but not the full appearance experience discussed with the owner.
- Profile styling still borrowed welcome-card configuration internally.
- The welcome shortcut was removed from Profiles without a dedicated Welcome & Join setup home.

## Implementation

- Canonical `/dank home` control center with guided buttons for Setup, Protection, Welcome & Join, Profiles, Members, Design, Roles, Logs, Diagnostics, Status, and Help.
- Public slash surface compacted to seven predictable children: `home`, `setup`, `profile`, `status`, `diagnostics`, `help`, and the upload-only `welcome` group.
- A fail-closed serialized-payload guard blocks future `/dank` growth above 7,600 characters.
- Dedicated Welcome & Join setup area for static welcome/start-here messages, join-only cards, join/leave announcements, previews, health, and attachment-command guidance.
- Dedicated Profile Signatures setup area for channels, allowed information, profile panel/roles, server appearance defaults, previews, and cleanup.
- Member signature studio for theme, font, colors, background, layout, avatar frame, privacy, platforms, roles, preview, and reset.
- Profile style state is independent from welcome-card state. A server manager may explicitly import the Join Card look once; later changes stay separate.
- Attach Files is now required before image signatures can be enabled.

## Validation required

- [ ] One-time patch workflow succeeds and removes all temporary patch files/workflows.
- [ ] Changed Python modules compile.
- [ ] Focused UI/profile/welcome tests pass.
- [ ] `tools/test_dank_command_payload.py` proves the exact live tree is below 7,600.
- [ ] Full unit suite and every repository audit pass.
- [ ] PR is zero commits behind `main` with no unresolved review threads.
- [ ] Live Discord smoke proves slash sync succeeds and the new menus open correctly.
- [ ] Merge requires explicit owner approval.

## Backlog observation — not active implementation

The supplied deployment log also shows departed-member reconciliation treating `Guild.fetch_members()` as a normal iterable instead of an async iterator (`TypeError: 'async_generator' object is not iterable`). That remains backlogged until this active command/profile/welcome correction is complete unless the owner explicitly force-switches.
''',
)


# The patch runner and all earlier temporary workflows must not survive.
for temporary in (
    ".github/workflows/apply-profile-welcome-ux.yml",
    ".github/workflows/run-profile-welcome-ux-patch.yml",
    ".github/workflows/run-ui-first-command-overhaul.yml",
    "tools/apply_ui_first_command_overhaul.py",
):
    path = ROOT / temporary
    if path.exists():
        path.unlink()

print("UI-first command overhaul patch applied")
