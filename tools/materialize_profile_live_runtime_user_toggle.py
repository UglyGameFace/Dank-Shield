from __future__ import annotations

from pathlib import Path


def replace_once(path: Path, old: str, new: str, *, label: str) -> None:
    source = path.read_text(encoding="utf-8")
    count = source.count(old)
    if count != 1:
        raise RuntimeError(f"{label}: expected exactly one match in {path}, found {count}")
    path.write_text(source.replace(old, new, 1), encoding="utf-8")


runtime = Path("stoney_verify/profile_card_runtime.py")
replace_once(
    runtime,
    '''        channel = message.channel
        guild = message.guild
        member = guild.get_member(trigger.user_id) if guild else None
        if not isinstance(channel, discord.TextChannel) or not isinstance(member, discord.Member):
            return
''',
    '''        channel = message.channel
        guild = message.guild
        message_author = getattr(message, "author", None)
        if isinstance(message_author, discord.Member) and int(message_author.id) == int(trigger.user_id):
            member = message_author
        else:
            member = guild.get_member(trigger.user_id) if guild else None
        if not isinstance(channel, discord.TextChannel):
            print(
                "⚠️ live_profile_card skipped unsupported channel "
                f"guild={trigger.guild_id} channel={trigger.channel_id} user={trigger.user_id}"
            )
            return
        if not isinstance(member, discord.Member):
            print(
                "⚠️ live_profile_card skipped member unavailable "
                f"guild={trigger.guild_id} channel={trigger.channel_id} user={trigger.user_id} "
                "source=message_author_then_cache"
            )
            return
''',
    label="use authoritative message member",
)
replace_once(
    runtime,
    '''        if not _channel_can_host_cards(channel):
            return

        state = await get_live_card_state(trigger.guild_id, trigger.channel_id)
''',
    '''        if not _channel_can_host_cards(channel):
            print(
                "⚠️ live_profile_card skipped channel permissions "
                f"guild={trigger.guild_id} channel={trigger.channel_id} user={trigger.user_id} "
                "required=view,send,embed,history,attach"
            )
            return

        state = await get_live_card_state(trigger.guild_id, trigger.channel_id)
''',
    label="runtime permission diagnostic",
)
replace_once(
    runtime,
    '''        if rendered is None:
            return

        try:
''',
    '''        if rendered is None:
            print(
                "ℹ️ live_profile_card skipped member disabled "
                f"guild={trigger.guild_id} channel={trigger.channel_id} user={trigger.user_id}"
            )
            return

        try:
''',
    label="runtime privacy diagnostic",
)

public = Path("stoney_verify/commands_ext/public_profile_cards.py")
insert_marker = '''class _ManagePlatformsButton(discord.ui.Button):
'''
source = public.read_text(encoding="utf-8")
if insert_marker not in source:
    raise RuntimeError("live toggle insertion marker missing")
live_toggle = '''class _LiveSignatureToggleButton(discord.ui.Button):
    def __init__(self, *, enabled: bool, row: int = 0) -> None:
        self.enabled = bool(enabled)
        super().__init__(
            label="Turn Off Live Signature" if self.enabled else "Turn On Live Signature",
            emoji="⏸️" if self.enabled else "▶️",
            style=discord.ButtonStyle.danger if self.enabled else discord.ButtonStyle.success,
            custom_id="dank:profilecard:v3:toggle_live_signature",
            row=row,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        view = self.view
        if not isinstance(view, ProfileSettingsView) or not await view.interaction_check(interaction):
            return
        await _defer_private(interaction, component_update=True)
        try:
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
        except ProfileStorageUnavailable:
            await _safe_ephemeral(interaction, "Private profile storage is unavailable. Nothing changed.", ok=False)


'''
public.write_text(source.replace(insert_marker, live_toggle + insert_marker, 1), encoding="utf-8")
replace_once(
    public,
    '''        specs = (
            ("Live", "live_cards_enabled", "🪪"),
            ("Roles", "show_roles", "🎭"),
            ("Dates", "show_account_dates", "📅"),
            ("Platforms", "show_platforms", "🔗"),
        )
        for label, key, emoji in specs:
            self.add_item(_core._GlobalPrivacyToggleButton(label, key, global_values, emoji, 0))
        for label, key, emoji in specs:
            self.add_item(_core._GuildPrivacyToggleButton(label, key, local_values, emoji, 1))
        self.add_item(_ManagePlatformsButton(row=2))
        self.add_item(_PreviewProfileButton(row=2))
        self.add_item(_BackToSignatureButton(row=2))
''',
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
        self.add_item(_ManagePlatformsButton(row=0))
        self.add_item(_PreviewProfileButton(row=0))
        self.add_item(_BackToSignatureButton(row=0))
        for label, key, emoji in detail_specs:
            self.add_item(_core._GlobalPrivacyToggleButton(label, key, global_values, emoji, 1))
        for label, key, emoji in detail_specs:
            self.add_item(_core._GuildPrivacyToggleButton(label, key, local_values, emoji, 2))
''',
    label="simple live toggle layout",
)

studio = Path("stoney_verify/profile_signature_studio.py")
replace_once(
    studio,
    '''class SignatureStudioView(discord.ui.View):
    def __init__(self, *, author_id: int) -> None:
        super().__init__(timeout=600)
        self.author_id = int(author_id)
''',
    '''class SignatureStudioView(discord.ui.View):
    def __init__(self, *, author_id: int, live_enabled: bool = True) -> None:
        super().__init__(timeout=600)
        self.author_id = int(author_id)
        self.live_enabled = bool(live_enabled)
        self.live_toggle.label = "Live Signature: ON" if self.live_enabled else "Live Signature: OFF"
        self.live_toggle.emoji = "✅" if self.live_enabled else "⏸️"
        self.live_toggle.style = discord.ButtonStyle.success if self.live_enabled else discord.ButtonStyle.danger
''',
    label="signature studio live state",
)
replace_once(
    studio,
    '''    @discord.ui.button(label="Reset My Look", emoji="🔄", style=discord.ButtonStyle.secondary, row=1)
    async def reset(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
''',
    '''    @discord.ui.button(label="Live Signature: ON", emoji="✅", style=discord.ButtonStyle.success, row=2)
    async def live_toggle(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        member = _member(interaction)
        if member is None:
            return await _private(interaction, content="❌ Use this inside a server as a member.")
        await _defer(interaction, component_update=True)
        try:
            user = await get_profile_user(member.id, refresh=True)
            current = bool(dict(user.get("preferences") or {}).get("live_cards_enabled", True))
            await upsert_profile_user_preferences(member.id, {"live_cards_enabled": not current})
            await _invalidate(interaction, all_guilds=True)
            updated = await get_profile_user(member.id, refresh=True)
            enabled = bool(dict(updated.get("preferences") or {}).get("live_cards_enabled", True))
            embed = await _studio_embed(member)
        except ProfileStorageUnavailable:
            return await _edit_private(interaction, content="❌ Private profile storage is unavailable. Nothing changed.")
        await _edit_private(
            interaction,
            content=f"✅ Live Signature is now **{'ON' if enabled else 'OFF'}**.",
            embed=embed,
            view=SignatureStudioView(author_id=member.id, live_enabled=enabled),
        )

    @discord.ui.button(label="Reset My Look", emoji="🔄", style=discord.ButtonStyle.secondary, row=1)
    async def reset(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
''',
    label="signature studio live toggle button",
)
replace_once(
    studio,
    '''    @discord.ui.button(label="Close", emoji="✖️", style=discord.ButtonStyle.danger, row=2)
''',
    '''    @discord.ui.button(label="Close", emoji="✖️", style=discord.ButtonStyle.danger, row=3)
''',
    label="move close below live toggle",
)
replace_once(
    studio,
    '''    try:
        embed = await _studio_embed(member)
    except ProfileStorageUnavailable:
        return await _private(interaction, content="❌ Private profile storage is unavailable.")
    panel = SignatureStudioView(author_id=member.id)
''',
    '''    try:
        user = await get_profile_user(member.id, refresh=True)
        embed = await _studio_embed(member)
    except ProfileStorageUnavailable:
        return await _private(interaction, content="❌ Private profile storage is unavailable.")
    live_enabled = bool(dict(user.get("preferences") or {}).get("live_cards_enabled", True))
    panel = SignatureStudioView(author_id=member.id, live_enabled=live_enabled)
''',
    label="signature studio resolved live state",
)

Path("tests/test_profile_live_runtime_authoritative_member.py").write_text(
    '''from __future__ import annotations

import asyncio

import discord

import stoney_verify.profile_card_runtime as runtime_module
from stoney_verify.profile_card_runtime import LiveProfileCardRuntime
from tests.test_live_profile_card_runtime import (
    FakeBot,
    FakeGuild,
    FakeIncomingMessage,
    _config,
    _fake_renderer,
    _patch_discord_types,
    _wait_for_pending,
)


def test_live_runtime_uses_message_author_when_guild_member_cache_misses(monkeypatch):
    async def scenario():
        _patch_discord_types(monkeypatch)
        bot = FakeBot()
        guild = FakeGuild(901, bot.user)
        bot.guilds = [guild]
        channel = guild.add_channel(902)
        author = runtime_module.discord.Member(903, guild)
        assert guild.get_member(author.id) is None
        seen = []
        states = []

        async def get_config(_guild_id):
            return _config(channel.id, cooldown=30)

        async def get_state(_guild_id, _channel_id):
            return None

        async def save_state(guild_id, channel_id, **payload):
            states.append((guild_id, channel_id, payload))

        monkeypatch.setattr(runtime_module, "get_guild_config", get_config)
        monkeypatch.setattr(runtime_module, "get_live_card_state", get_state)
        monkeypatch.setattr(runtime_module, "upsert_live_card_state", save_state)

        runtime = LiveProfileCardRuntime(bot, renderer=_fake_renderer(seen), sleep=asyncio.sleep)
        await runtime.on_message(FakeIncomingMessage(904, guild, channel, author))
        await _wait_for_pending(runtime)

        assert [item[0] for item in seen] == [author.id]
        assert len(channel.sent) == 1
        assert states[0][2]["user_id"] == author.id

    asyncio.run(scenario())


def test_profile_privacy_has_one_clear_live_signature_switch():
    from stoney_verify.commands_ext.public_profile_cards import ProfileSettingsView

    enabled = ProfileSettingsView(
        author_id=42,
        guild_id=7,
        user_preferences={"live_cards_enabled": True},
        guild_settings={},
    )
    disabled = ProfileSettingsView(
        author_id=42,
        guild_id=7,
        user_preferences={"live_cards_enabled": False},
        guild_settings={},
    )
    enabled_labels = [str(child.label) for child in enabled.children if isinstance(child, discord.ui.Button)]
    disabled_labels = [str(child.label) for child in disabled.children if isinstance(child, discord.ui.Button)]
    assert enabled_labels.count("Turn Off Live Signature") == 1
    assert disabled_labels.count("Turn On Live Signature") == 1
    assert not any(label.startswith("Every Server Live") for label in enabled_labels)
''',
    encoding="utf-8",
)

active = Path("ACTIVE_TASK.md")
source = active.read_text(encoding="utf-8")
source = source.replace(
    "## Scope\n",
    "## Additional live-runtime finding\n\n"
    "- The live worker discarded the authoritative `message.author` member and relied on `guild.get_member()`. "
    "A member-cache miss therefore produced a silent no-card result even for a valid message.\n"
    "- Members need one obvious ON/OFF switch instead of a generic Every Server Live inheritance button.\n\n"
    "## Scope\n",
)
source = source.replace(
    "- Add an obvious **Manage Accounts** action to Profile Privacy.\n",
    "- Use the authoritative message member before any guild-cache fallback in live posting.\n"
    "- Add actionable runtime diagnostics for member, permission, privacy, send, and state failures.\n"
    "- Add one obvious **Turn On/Off Live Signature** member control.\n"
    "- Add an obvious **Manage Accounts** action to Profile Privacy.\n",
)
source = source.replace(
    "- [ ] Privacy panel exposes Manage Accounts, Preview Signature, and Back to Profile.\n",
    "- [ ] A valid message still posts when `guild.get_member()` misses but `message.author` is a member.\n"
    "- [ ] Profile Privacy exposes exactly one obvious Turn On/Off Live Signature switch.\n"
    "- [ ] Profile home displays Live Signature: ON/OFF and toggles it in place.\n"
    "- [ ] Privacy panel exposes Manage Accounts, Preview Signature, and Back to Profile.\n",
)
active.write_text(source, encoding="utf-8")

print("materialized live signature authoritative member and simple user toggle correction")
