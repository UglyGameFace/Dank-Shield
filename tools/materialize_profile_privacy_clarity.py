from __future__ import annotations

from pathlib import Path


def replace_once(path: Path, old: str, new: str, *, label: str) -> None:
    source = path.read_text(encoding="utf-8")
    count = source.count(old)
    if count != 1:
        raise RuntimeError(f"{label}: expected exactly one match in {path}, found {count}")
    path.write_text(source.replace(old, new, 1), encoding="utf-8")


def replace_section(path: Path, start: str, end: str, replacement: str, *, label: str) -> None:
    source = path.read_text(encoding="utf-8")
    if source.count(start) != 1 or source.count(end) < 1:
        raise RuntimeError(f"{label}: section markers missing or ambiguous in {path}")
    before, remainder = source.split(start, 1)
    _, after = remainder.split(end, 1)
    path.write_text(before + replacement + end + after, encoding="utf-8")


core = Path("stoney_verify/commands_ext/public_profile_cards_core.py")
replace_section(
    core,
    "def _settings_embed(\n",
    "class ProfileSettingsView(discord.ui.View):\n",
    '''def _settings_embed(
    member: discord.Member,
    user_row: Mapping[str, Any],
    guild_row: Mapping[str, Any],
    effective: Mapping[str, Any],
) -> discord.Embed:
    preferences = dict(effective.get("preferences") or {})
    global_preferences = dict(user_row.get("preferences") or {})
    local = dict(guild_row.get("settings") or {})
    platforms = dict(user_row.get("platforms") or {})

    def shown(value: Any) -> str:
        return "✅ Shown" if bool(value) else "❌ Hidden"

    embed = discord.Embed(
        title="🔐 Profile Privacy",
        description=(
            "The top row handles the important stuff: turn your live signature on or off, manage gaming/social "
            "accounts, preview it, or go back. The buttons below change optional details."
        ),
        color=discord.Color.blurple(),
        timestamp=discord.utils.utcnow(),
    )
    embed.add_field(
        name="Right now in this server",
        value=(
            f"**Live signature:** {'✅ On' if preferences.get('live_cards_enabled', True) else '⏸️ Off'}\n"
            f"**Profile roles:** {shown(preferences.get('show_roles', True))}\n"
            f"**Account dates:** {shown(preferences.get('show_account_dates', True))}\n"
            f"**Gaming/social accounts:** {shown(preferences.get('show_platforms', True))}"
        ),
        inline=False,
    )
    embed.add_field(
        name="Your default choices",
        value=(
            f"Roles: {'Show' if global_preferences.get('show_roles', True) else 'Hide'} • "
            f"Dates: {'Show' if global_preferences.get('show_account_dates', True) else 'Hide'} • "
            f"Accounts: {'Show' if global_preferences.get('show_platforms', True) else 'Hide'}"
        ),
        inline=False,
    )
    hidden_here = [
        label
        for key, label in (
            ("live_cards_enabled", "live signature"),
            ("show_roles", "roles"),
            ("show_account_dates", "account dates"),
            ("show_platforms", "gaming/social accounts"),
        )
        if local.get(key) is False
    ]
    embed.add_field(
        name="Different only in this server",
        value=(
            "Hidden here: " + ", ".join(hidden_here)
            if hidden_here
            else "Nothing special is hidden here; your default choices are being used."
        ),
        inline=False,
    )

    identity_lines: list[str] = []
    for key, spec in PLATFORM_SPECS.items():
        entry = platforms.get(key)
        if not isinstance(entry, Mapping):
            continue
        username = str(entry.get("username") or "").strip()
        if not username:
            continue
        visibility = "🌐 Public" if bool(entry.get("shared")) else "🔒 Private"
        link_state = " • official link" if str(entry.get("url") or "").strip() else " • username only"
        safe_username = display_profile_username(username)
        identity_lines.append(
            f"{spec.emoji} **{spec.label}:** `{safe_username}` — {visibility}{link_state}"
        )
    account_summary = "\n".join(identity_lines)[:820] if identity_lines else "No accounts saved yet."
    embed.add_field(
        name="Gaming & social accounts",
        value=(
            account_summary
            + "\n\nUse **Manage Accounts** below to add, edit, remove, or change an account's Public/Private status."
        )[:1024],
        inline=False,
    )
    embed.add_field(
        name="Privacy rule",
        value=(
            "Only you can make a saved identity public. Server managers may restrict which fields are allowed, "
            "but they cannot expose anything you kept private."
        ),
        inline=False,
    )
    embed.set_thumbnail(url=member.display_avatar.url)
    embed.set_footer(text="Dank Shield profile settings • private response")
    return embed


''',
    label="plain-language privacy embed",
)
replace_once(
    core,
    '''        current = bool(preferences.get(key, True))
        super().__init__(
            label=f"Every Server {label}: {'On' if current else 'Off'}",
            emoji=emoji,
            style=discord.ButtonStyle.success if current else discord.ButtonStyle.secondary,
''',
    '''        current = bool(preferences.get(key, True))
        super().__init__(
            label=f"Hide {label} Everywhere" if current else f"Show {label} Everywhere",
            emoji=emoji,
            style=discord.ButtonStyle.secondary if current else discord.ButtonStyle.success,
''',
    label="global privacy action labels",
)
replace_once(
    core,
    '''        hidden = settings.get(key) is False
        super().__init__(
            label=f"This Server {label}: {'Hidden' if hidden else 'Inherit'}",
            emoji=emoji,
            style=discord.ButtonStyle.secondary if hidden else discord.ButtonStyle.primary,
''',
    '''        hidden = settings.get(key) is False
        super().__init__(
            label=f"Use Default {label} Here" if hidden else f"Hide {label} In This Server",
            emoji=emoji,
            style=discord.ButtonStyle.success if hidden else discord.ButtonStyle.secondary,
''',
    label="server privacy action labels",
)

public = Path("stoney_verify/commands_ext/public_profile_cards.py")
replace_once(
    public,
    '''        try:
            user_row = await _core.get_profile_user(view.author_id, refresh=True)
            current = bool(dict(user_row.get("preferences") or {}).get("live_cards_enabled", True))
            await _core.upsert_profile_user_preferences(
                view.author_id,
                {"live_cards_enabled": not current},
            )
            if interaction.guild is not None:
                await invalidate_member_live_cards(
                    interaction.client,
                    interaction.guild,
                    view.author_id,
                    all_guilds=True,
                )
            await view.refresh(interaction)
''',
    '''        try:
            user_row = await _core.get_profile_user(view.author_id, refresh=True)
            guild_row = await _core.get_profile_guild_settings(view.guild_id, view.author_id, refresh=True)
            current = bool(
                _core.effective_preferences(
                    user_row.get("preferences"),
                    guild_row.get("settings"),
                ).get("live_cards_enabled", True)
            )
            if current:
                await _core.upsert_profile_user_preferences(
                    view.author_id,
                    {"live_cards_enabled": False},
                )
            else:
                await _core.upsert_profile_user_preferences(
                    view.author_id,
                    {"live_cards_enabled": True},
                )
                await _core.upsert_profile_guild_settings(
                    view.guild_id,
                    view.author_id,
                    {"live_cards_enabled": None},
                )
            if interaction.guild is not None:
                await invalidate_member_live_cards(
                    interaction.client,
                    interaction.guild,
                    view.author_id,
                    all_guilds=True,
                )
            await view.refresh(interaction)
''',
    label="effective live privacy toggle",
)
replace_once(
    public,
    '''        detail_specs = (
            ("Roles", "show_roles", "🎭"),
            ("Dates", "show_account_dates", "📅"),
            ("Platforms", "show_platforms", "🔗"),
        )
        self.add_item(
            _LiveSignatureToggleButton(
                enabled=bool(global_values.get("live_cards_enabled", True)),
                row=0,
            )
        )
''',
    '''        detail_specs = (
            ("Roles", "show_roles", "🎭"),
            ("Dates", "show_account_dates", "📅"),
            ("Accounts", "show_platforms", "🔗"),
        )
        effective_values = _core.effective_preferences(global_values, local_values)
        self.add_item(
            _LiveSignatureToggleButton(
                enabled=bool(effective_values.get("live_cards_enabled", True)),
                row=0,
            )
        )
''',
    label="effective live status in privacy view",
)

studio = Path("stoney_verify/profile_signature_studio.py")
replace_once(
    studio,
    '''from io import BytesIO
from typing import Any, Mapping, Optional
''',
    '''import asyncio
from io import BytesIO
from typing import Any, Mapping, Optional
''',
    label="studio asyncio import",
)
replace_once(
    studio,
    '''    display_profile_username,
    get_profile_user,
    remove_platform_identity,
    save_platform_identity,
    upsert_profile_user_preferences,
''',
    '''    display_profile_username,
    effective_preferences,
    get_profile_guild_settings,
    get_profile_user,
    remove_platform_identity,
    save_platform_identity,
    upsert_profile_guild_settings,
    upsert_profile_user_preferences,
''',
    label="studio effective privacy imports",
)
replace_once(
    studio,
    '''async def _studio_embed(member: discord.Member) -> discord.Embed:
    user = await get_profile_user(member.id)
    config = await get_guild_config(member.guild.id)
    preferences = dict(user.get("preferences") or {})
    labels = _style_labels(preferences, config)
''',
    '''async def _studio_embed(member: discord.Member) -> discord.Embed:
    user, guild_row, config = await asyncio.gather(
        get_profile_user(member.id),
        get_profile_guild_settings(member.guild.id, member.id),
        get_guild_config(member.guild.id),
    )
    preferences = dict(user.get("preferences") or {})
    effective_privacy = effective_preferences(preferences, guild_row.get("settings"))
    labels = _style_labels(preferences, config)
''',
    label="studio effective privacy read",
)
replace_once(
    studio,
    '''            f"**Live signature:** {'On' if preferences.get('live_cards_enabled', True) else 'Off'}\n"
            f"**Roles:** {'Shown' if preferences.get('show_roles', True) else 'Hidden'}\n"
            f"**Dates:** {'Shown' if preferences.get('show_account_dates', True) else 'Hidden'}\n"
''',
    '''            f"**Live signature:** {'On' if effective_privacy.get('live_cards_enabled', True) else 'Off'}\n"
            f"**Roles:** {'Shown' if effective_privacy.get('show_roles', True) else 'Hidden'}\n"
            f"**Dates:** {'Shown' if effective_privacy.get('show_account_dates', True) else 'Hidden'}\n"
''',
    label="studio effective sharing labels",
)
replace_once(
    studio,
    '''        try:
            user = await get_profile_user(member.id, refresh=True)
            current = bool(dict(user.get("preferences") or {}).get("live_cards_enabled", True))
            await upsert_profile_user_preferences(member.id, {"live_cards_enabled": not current})
            await _invalidate(interaction, all_guilds=True)
            updated = await get_profile_user(member.id, refresh=True)
            enabled = bool(dict(updated.get("preferences") or {}).get("live_cards_enabled", True))
            embed = await _studio_embed(member)
''',
    '''        try:
            user, guild_row = await asyncio.gather(
                get_profile_user(member.id, refresh=True),
                get_profile_guild_settings(member.guild.id, member.id, refresh=True),
            )
            current = bool(
                effective_preferences(
                    user.get("preferences"),
                    guild_row.get("settings"),
                ).get("live_cards_enabled", True)
            )
            if current:
                await upsert_profile_user_preferences(member.id, {"live_cards_enabled": False})
            else:
                await upsert_profile_user_preferences(member.id, {"live_cards_enabled": True})
                await upsert_profile_guild_settings(
                    member.guild.id,
                    member.id,
                    {"live_cards_enabled": None},
                )
            await _invalidate(interaction, all_guilds=True)
            updated_user, updated_guild = await asyncio.gather(
                get_profile_user(member.id, refresh=True),
                get_profile_guild_settings(member.guild.id, member.id, refresh=True),
            )
            enabled = bool(
                effective_preferences(
                    updated_user.get("preferences"),
                    updated_guild.get("settings"),
                ).get("live_cards_enabled", True)
            )
            embed = await _studio_embed(member)
''',
    label="studio effective live toggle",
)
replace_once(
    studio,
    '''    try:
        user = await get_profile_user(member.id, refresh=True)
        embed = await _studio_embed(member)
    except ProfileStorageUnavailable:
        return await _private(interaction, content="❌ Private profile storage is unavailable.")
    live_enabled = bool(dict(user.get("preferences") or {}).get("live_cards_enabled", True))
''',
    '''    try:
        user, guild_row = await asyncio.gather(
            get_profile_user(member.id, refresh=True),
            get_profile_guild_settings(member.guild.id, member.id, refresh=True),
        )
        embed = await _studio_embed(member)
    except ProfileStorageUnavailable:
        return await _private(interaction, content="❌ Private profile storage is unavailable.")
    live_enabled = bool(
        effective_preferences(
            user.get("preferences"),
            guild_row.get("settings"),
        ).get("live_cards_enabled", True)
    )
''',
    label="studio effective initial state",
)

ux_tests = Path("tests/test_profile_platform_privacy_preview_ux.py")
source = ux_tests.read_text(encoding="utf-8")
source += '''\n\ndef test_privacy_buttons_use_plain_action_language():
    view = public_profile_cards.ProfileSettingsView(
        author_id=42,
        guild_id=7,
        user_preferences={"show_roles": True, "show_account_dates": True, "show_platforms": True},
        guild_settings={},
    )
    labels = {str(child.label) for child in view.children if isinstance(child, discord.ui.Button)}
    assert "Hide Roles Everywhere" in labels
    assert "Hide Roles In This Server" in labels
    assert "Hide Dates Everywhere" in labels
    assert "Hide Accounts In This Server" in labels
    assert not any("Inherit" in label for label in labels)
    assert not any(label.startswith("Every Server") for label in labels)


def test_live_switch_uses_effective_server_state():
    view = public_profile_cards.ProfileSettingsView(
        author_id=42,
        guild_id=7,
        user_preferences={"live_cards_enabled": True},
        guild_settings={"live_cards_enabled": False},
    )
    labels = {str(child.label) for child in view.children if isinstance(child, discord.ui.Button)}
    assert "Turn On Live Signature" in labels
    assert "Turn Off Live Signature" not in labels


def test_privacy_embed_avoids_inheritance_jargon():
    embed = _settings_embed(
        _Member(),
        {"preferences": {}, "platforms": {}},
        {"settings": {}},
        {"preferences": {}},
    )
    text = " ".join(
        [str(embed.title or ""), str(embed.description or "")]
        + [f"{field.name} {field.value}" for field in embed.fields]
    )
    assert "Right now in this server" in text
    assert "Your default choices" in text
    assert "Different only in this server" in text
    assert "inherit" not in text.lower()
'''
ux_tests.write_text(source, encoding="utf-8")

active = Path("ACTIVE_TASK.md")
source = active.read_text(encoding="utf-8")
source = source.replace(
    "- Add one obvious **Turn On/Off Live Signature** member control.\n",
    "- Add one obvious **Turn On/Off Live Signature** member control that reflects the effective state in this server.\n"
    "- Replace inheritance jargon with plain action labels and plain-language status summaries.\n",
)
source = source.replace(
    "- [x] Profile home displays Live Signature: ON/OFF and toggles it in place.\n",
    "- [x] Profile home displays Live Signature: ON/OFF and toggles it in place.\n"
    "- [ ] Live switches reflect an old hidden-here override and clear it when turning back on.\n"
    "- [ ] Privacy buttons use plain actions without Every Server/Inherit jargon.\n",
)
active.write_text(source, encoding="utf-8")

print("materialized plain-language profile privacy controls")
