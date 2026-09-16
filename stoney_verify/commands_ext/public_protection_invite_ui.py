from __future__ import annotations

"""Native Invite Shield setup and cleanup UI for `/dank protection`.

This module owns presentation only. Guild-scoped target persistence lives in
``invite_scope_settings`` and all historical-message deletion decisions remain
owned by ``invite_policy_engine``.
"""

from typing import Any

import discord

from ..invite_scope_settings import (
    ALL_BOTS_KEY,
    BOT_IDS_KEY,
    CHANNEL_IDS_KEY,
    PROTECTED_RULE_KEY,
    load_invite_scope_settings,
    parse_ids,
    save_invite_scope_settings,
)
from ..ui import DankGuildResourceBrowserView

_INSTALLED = False
_ORIGINAL_TOGGLE: Any = None


def _center() -> Any:
    from . import public_protection_center as center

    return center


def _channel_id(interaction: discord.Interaction) -> int:
    try:
        return int(getattr(interaction, "channel_id", 0) or 0)
    except Exception:
        return 0


def _status(scope: dict[str, Any]) -> str:
    all_bots = bool(scope.get(ALL_BOTS_KEY))
    bot_ids = parse_ids(scope.get(BOT_IDS_KEY))
    channel_ids = parse_ids(scope.get(CHANNEL_IDS_KEY))
    protected = bool(scope.get(PROTECTED_RULE_KEY))

    if all_bots:
        bots = "Every bot"
    elif bot_ids:
        bots = f"{len(bot_ids)} exact bot/user ID(s)"
    else:
        bots = "No protected bot/user targets"

    channels = f"{len(channel_ids)} exact channel(s)" if channel_ids else "All channels"
    return (
        f"**Protected-poster rule:** {'ON' if protected else 'OFF'}\n"
        f"**Bot/user targets:** {bots}\n"
        f"**Channel scope:** {channels}"
    )


def invite_shield_embed(scope: dict[str, Any], *, note: str = "") -> discord.Embed:
    embed = discord.Embed(
        title="🚫 Invite Shield",
        description=(
            "Configure where Invite Shield should pay extra attention and clean old invite posts. "
            "Normal links are unaffected unless Link Shield is enabled separately."
        ),
        color=discord.Color.blurple(),
    )
    embed.add_field(name="Current targeting", value=_status(scope), inline=False)
    embed.add_field(
        name="Fast setup",
        value=(
            "**Fix This Channel** turns Invite Shield on and protects bot invite posts in the channel where you opened this screen.\n"
            "**Turn Shield On / Off** preserves the original Invite Blocker toggle without leaving you trapped in this editor.\n"
            "**Watch Every Bot** protects bot invite posts server-wide.\n"
            "**Choose Watched Channel** uses Dank Shield's searchable server browser.\n"
            "**Clean Existing Invites** scans one chosen channel through the central invite policy before deleting anything."
        ),
        inline=False,
    )
    if note:
        embed.add_field(name="Last action", value=str(note)[:1024], inline=False)
    return embed


async def _safe_ephemeral(interaction: discord.Interaction, message: str) -> None:
    try:
        if not interaction.response.is_done():
            await interaction.response.send_message(
                message,
                ephemeral=True,
                allowed_mentions=discord.AllowedMentions.none(),
            )
        else:
            await interaction.followup.send(
                message,
                ephemeral=True,
                allowed_mentions=discord.AllowedMentions.none(),
            )
    except Exception:
        pass


async def _require_owner(interaction: discord.Interaction, author_id: int) -> bool:
    if int(getattr(interaction.user, "id", 0) or 0) == int(author_id):
        return True
    await _safe_ephemeral(interaction, "❌ Open your own `/dank protection` panel to change Invite Shield.")
    return False


async def _turn_invite_shield_on(interaction: discord.Interaction) -> None:
    center = _center()
    guild = interaction.guild
    if guild is None:
        return
    await center._save_automod(
        int(guild.id),
        {
            "automod_enabled": True,
            "automod_block_invites": True,
            "automod_block_links": False,
            "automod_link_policy": "invite_shield",
            "automod_updated_by_id": str(int(interaction.user.id)),
        },
    )


async def _redraw(
    interaction: discord.Interaction,
    *,
    author_id: int,
    scope: dict[str, Any],
    origin_channel_id: int,
    note: str = "",
) -> None:
    view = InviteShieldView(
        author_id=author_id,
        guild=interaction.guild,
        origin_channel_id=origin_channel_id,
        scope=scope,
    )
    embed = invite_shield_embed(scope, note=note)
    if not interaction.response.is_done():
        await interaction.response.edit_message(embed=embed, view=view)
    else:
        await interaction.edit_original_response(embed=embed, view=view)


class InviteScopeIdsModal(discord.ui.Modal, title="Invite Shield Advanced IDs"):
    def __init__(self, *, author_id: int, guild_id: int, origin_channel_id: int, scope: dict[str, Any]) -> None:
        super().__init__(timeout=300)
        self.author_id = int(author_id)
        self.guild_id = int(guild_id)
        self.origin_channel_id = int(origin_channel_id)
        self.bot_ids = discord.ui.TextInput(
            label="Exact bot/user IDs",
            placeholder="IDs or mentions, separated by spaces, commas, or lines",
            default=", ".join(parse_ids(scope.get(BOT_IDS_KEY))),
            required=False,
            style=discord.TextStyle.paragraph,
            max_length=1200,
        )
        self.channel_ids = discord.ui.TextInput(
            label="Exact channel IDs",
            placeholder="Blank means all channels; IDs or channel mentions accepted",
            default=", ".join(parse_ids(scope.get(CHANNEL_IDS_KEY))),
            required=False,
            style=discord.TextStyle.paragraph,
            max_length=1200,
        )
        self.add_item(self.bot_ids)
        self.add_item(self.channel_ids)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        if not await _require_owner(interaction, self.author_id):
            return
        center = _center()
        if not await center._require_setup_permission(interaction):
            return
        guild = interaction.guild
        if guild is None or int(guild.id) != self.guild_id:
            return await _safe_ephemeral(interaction, "❌ That Invite Shield screen is no longer valid for this server.")

        scope = await save_invite_scope_settings(
            self.guild_id,
            {
                BOT_IDS_KEY: parse_ids(self.bot_ids.value),
                CHANNEL_IDS_KEY: parse_ids(self.channel_ids.value),
                PROTECTED_RULE_KEY: True,
            },
        )
        await _redraw(
            interaction,
            author_id=self.author_id,
            scope=scope,
            origin_channel_id=self.origin_channel_id,
            note="Advanced Invite Shield targets saved.",
        )

    async def on_error(self, interaction: discord.Interaction, error: Exception) -> None:
        try:
            print(f"[protection_invite_ui] ID modal failed: {type(error).__name__}: {error}")
        except Exception:
            pass
        await _safe_ephemeral(interaction, "⚠️ Invite Shield could not save those IDs. Nothing was changed.")


class InviteShieldView(discord.ui.View):
    def __init__(
        self,
        *,
        author_id: int,
        guild: discord.Guild | None,
        origin_channel_id: int,
        scope: dict[str, Any],
    ) -> None:
        super().__init__(timeout=900)
        self.author_id = int(author_id)
        self.guild = guild
        self.origin_channel_id = int(origin_channel_id)
        self.scope = dict(scope or {})

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        return await _require_owner(interaction, self.author_id)

    @discord.ui.button(label="Fix This Channel", emoji="✅", style=discord.ButtonStyle.success, row=0)
    async def fix_this_channel(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        center = _center()
        if not await center._require_setup_permission(interaction):
            return
        guild = interaction.guild
        channel_id = _channel_id(interaction) or self.origin_channel_id
        if guild is None or channel_id <= 0:
            return await _safe_ephemeral(interaction, "❌ I could not resolve the channel that opened this panel.")
        channel = guild.get_channel(channel_id)
        if not isinstance(channel, discord.TextChannel):
            return await _safe_ephemeral(interaction, "❌ Fix This Channel requires a normal text channel.")

        await _turn_invite_shield_on(interaction)
        scope = await save_invite_scope_settings(
            int(guild.id),
            {
                ALL_BOTS_KEY: True,
                CHANNEL_IDS_KEY: [str(channel.id)],
                PROTECTED_RULE_KEY: True,
            },
        )
        await _redraw(
            interaction,
            author_id=self.author_id,
            scope=scope,
            origin_channel_id=int(channel.id),
            note=f"Invite Shield is ON and watching bot invite posts in {channel.mention}.",
        )

    @discord.ui.button(label="Turn Shield On / Off", emoji="🛡️", style=discord.ButtonStyle.secondary, row=0)
    async def toggle_shield(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        center = _center()
        if not await center._require_setup_permission(interaction):
            return
        if not callable(_ORIGINAL_TOGGLE):
            return await _safe_ephemeral(interaction, "⚠️ Invite Shield toggle ownership is unavailable. Nothing was changed.")
        await _ORIGINAL_TOGGLE(interaction)

    @discord.ui.button(label="Watch Every Bot", emoji="🤖", style=discord.ButtonStyle.primary, row=1)
    async def watch_every_bot(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        center = _center()
        if not await center._require_setup_permission(interaction):
            return
        guild = interaction.guild
        if guild is None:
            return await _safe_ephemeral(interaction, "❌ This must be used inside a server.")
        scope = await save_invite_scope_settings(
            int(guild.id),
            {ALL_BOTS_KEY: True, PROTECTED_RULE_KEY: True},
        )
        await _redraw(
            interaction,
            author_id=self.author_id,
            scope=scope,
            origin_channel_id=self.origin_channel_id,
            note="Protected-poster targeting now includes every bot.",
        )

    @discord.ui.button(label="Choose Watched Channel", emoji="🧭", style=discord.ButtonStyle.primary, row=1)
    async def choose_channel(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        center = _center()
        if not await center._require_setup_permission(interaction):
            return
        guild = interaction.guild
        if guild is None:
            return await _safe_ephemeral(interaction, "❌ This must be used inside a server.")

        async def back(home_interaction: discord.Interaction) -> None:
            fresh = await load_invite_scope_settings(int(guild.id), refresh=True)
            await _redraw(
                home_interaction,
                author_id=self.author_id,
                scope=fresh,
                origin_channel_id=self.origin_channel_id,
            )

        async def picked(pick_interaction: discord.Interaction, channel: Any) -> None:
            live = guild.get_channel(int(getattr(channel, "id", 0) or 0))
            if not isinstance(live, discord.TextChannel):
                return await _safe_ephemeral(pick_interaction, "❌ That channel disappeared or is no longer a text channel.")
            current = await load_invite_scope_settings(int(guild.id), refresh=True)
            ids = parse_ids(current.get(CHANNEL_IDS_KEY))
            cid = str(int(live.id))
            if cid not in ids:
                ids.append(cid)
            scope = await save_invite_scope_settings(
                int(guild.id),
                {CHANNEL_IDS_KEY: ids, PROTECTED_RULE_KEY: True},
            )
            await _redraw(
                pick_interaction,
                author_id=self.author_id,
                scope=scope,
                origin_channel_id=self.origin_channel_id,
                note=f"Added {live.mention} to Invite Shield targeting.",
            )

        browser = DankGuildResourceBrowserView(
            guild=guild,
            author_id=self.author_id,
            resource_kinds=("text",),
            on_pick=picked,
            custom_id="dank_protection:invite_target_channel",
            title="🧭 Choose Invite Shield Channel",
            placeholder="Choose a text channel…",
            on_home=back,
            home_label="Back to Invite Shield",
            empty_message="No text channels matched. Search by channel name, ID, or mention.",
        )
        await interaction.response.edit_message(embed=browser.embed(), view=browser)

    @discord.ui.button(label="All Channels", emoji="🌐", style=discord.ButtonStyle.secondary, row=2)
    async def all_channels(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        center = _center()
        if not await center._require_setup_permission(interaction):
            return
        guild = interaction.guild
        if guild is None:
            return await _safe_ephemeral(interaction, "❌ This must be used inside a server.")
        scope = await save_invite_scope_settings(int(guild.id), {CHANNEL_IDS_KEY: []})
        await _redraw(
            interaction,
            author_id=self.author_id,
            scope=scope,
            origin_channel_id=self.origin_channel_id,
            note="Channel targeting cleared. Invite Shield applies across channels according to the active policy.",
        )

    @discord.ui.button(label="Advanced IDs", emoji="✍️", style=discord.ButtonStyle.secondary, row=2)
    async def advanced_ids(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        center = _center()
        if not await center._require_setup_permission(interaction):
            return
        guild = interaction.guild
        if guild is None:
            return await _safe_ephemeral(interaction, "❌ This must be used inside a server.")
        fresh = await load_invite_scope_settings(int(guild.id), refresh=True)
        await interaction.response.send_modal(
            InviteScopeIdsModal(
                author_id=self.author_id,
                guild_id=int(guild.id),
                origin_channel_id=self.origin_channel_id,
                scope=fresh,
            )
        )

    @discord.ui.button(label="Clean Existing Invites", emoji="🧹", style=discord.ButtonStyle.secondary, row=3)
    async def clean_existing(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        center = _center()
        if not await center._require_setup_permission(interaction):
            return
        guild = interaction.guild
        if guild is None:
            return await _safe_ephemeral(interaction, "❌ This must be used inside a server.")

        async def back(home_interaction: discord.Interaction) -> None:
            fresh = await load_invite_scope_settings(int(guild.id), refresh=True)
            await _redraw(
                home_interaction,
                author_id=self.author_id,
                scope=fresh,
                origin_channel_id=self.origin_channel_id,
            )

        async def picked(pick_interaction: discord.Interaction, channel: Any) -> None:
            live = guild.get_channel(int(getattr(channel, "id", 0) or 0))
            if not isinstance(live, discord.TextChannel):
                return await _safe_ephemeral(pick_interaction, "❌ That channel disappeared or is no longer a text channel.")
            from .. import invite_policy_engine

            result = await invite_policy_engine.scan_channel_invites(
                live,
                limit=200,
                repost_mixed=False,
                source="protection-center-native-invite-cleanup",
            )
            fresh = await load_invite_scope_settings(int(guild.id), refresh=True)
            note = (
                f"Scanned {live.mention}: checked `{int(result.get('checked', 0) or 0)}`, "
                f"matched `{int(result.get('matched', 0) or 0)}`, deleted `{int(result.get('deleted', 0) or 0)}`, "
                f"allowed `{int(result.get('allowed', 0) or 0)}`, failed `{int(result.get('failed', 0) or 0)}`."
            )
            await _redraw(
                pick_interaction,
                author_id=self.author_id,
                scope=fresh,
                origin_channel_id=self.origin_channel_id,
                note=note,
            )

        browser = DankGuildResourceBrowserView(
            guild=guild,
            author_id=self.author_id,
            resource_kinds=("text",),
            on_pick=picked,
            custom_id="dank_protection:invite_cleanup_channel",
            title="🧹 Choose Channel to Scan",
            placeholder="Choose a text channel to clean…",
            on_home=back,
            home_label="Back to Invite Shield",
            empty_message="No text channels matched. Search by channel name, ID, or mention.",
        )
        await interaction.response.edit_message(embed=browser.embed(), view=browser)

    @discord.ui.button(label="Back to Protection", emoji="↩️", style=discord.ButtonStyle.secondary, row=4)
    async def back_to_protection(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        center = _center()
        if not await center._require_setup_permission(interaction):
            return
        await center._refresh_panel(interaction, content="Back to Protection Center.")

    async def on_error(self, interaction: discord.Interaction, error: Exception, item: discord.ui.Item[Any]) -> None:
        _ = item
        try:
            print(f"[protection_invite_ui] callback failed: {type(error).__name__}: {error}")
        except Exception:
            pass
        await _safe_ephemeral(
            interaction,
            "⚠️ Invite Shield could not finish that action. Nothing was changed unless a success message already appeared.",
        )


async def open_invite_shield(interaction: discord.Interaction) -> None:
    center = _center()
    if not await center._require_setup_permission(interaction):
        return
    guild = interaction.guild
    if guild is None:
        return await _safe_ephemeral(interaction, "❌ This must be used inside a server.")
    scope = await load_invite_scope_settings(int(guild.id), refresh=True)
    channel_id = _channel_id(interaction)
    embed = invite_shield_embed(scope)
    view = InviteShieldView(
        author_id=int(interaction.user.id),
        guild=guild,
        origin_channel_id=channel_id,
        scope=scope,
    )
    if interaction.response.is_done():
        await interaction.edit_original_response(content=None, embed=embed, view=view)
    else:
        await interaction.response.edit_message(content=None, embed=embed, view=view)


def install_native_invite_ui() -> bool:
    """Route the canonical Invite Blocker action to this native feature UI.

    The Protection Center button already calls ``_toggle_invite_shield`` inside
    its guarded interaction boundary. Rebinding that feature function keeps the
    existing button, owner checks, and error handling intact without replacing a
    View constructor or component callback like the retired startup guards did.
    """

    global _INSTALLED, _ORIGINAL_TOGGLE
    if _INSTALLED:
        return True
    try:
        center = _center()
        original = getattr(center, "_toggle_invite_shield", None)
        if not callable(original):
            return False
        _ORIGINAL_TOGGLE = original
        center._toggle_invite_shield = open_invite_shield
        _INSTALLED = True
        return True
    except Exception:
        return False


__all__ = [
    "InviteShieldView",
    "InviteScopeIdsModal",
    "install_native_invite_ui",
    "invite_shield_embed",
    "open_invite_shield",
]
