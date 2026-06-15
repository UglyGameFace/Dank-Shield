from __future__ import annotations

"""Simple Discord Invite Blocker setup UI.

This replaces the old scope/pagination wording with staff-facing language:
what bots/users are watched, what channels are watched, and what each button does.
"""

from typing import Any, Iterable

import discord

_PATCHED = False
_ORIGINAL_GET: Any = None
_ORIGINAL_SAVE: Any = None

_ALL_BOTS_KEY = "invite_hard_block_target_all_bots"


def _safe_bool(value: Any, default: bool = False) -> bool:
    try:
        if isinstance(value, bool):
            return value
        if value is None:
            return bool(default)
        return str(value).strip().lower() in {"1", "true", "yes", "y", "on", "enabled", "all"}
    except Exception:
        return bool(default)


def _as_id_list(value: Any) -> list[str]:
    try:
        from stoney_verify.startup_guards import spam_guard_invite_override_options as policy
        return policy._parse_ids(value)
    except Exception:
        pass
    if isinstance(value, (list, tuple, set)):
        return [str(x) for x in value if str(x).isdigit()]
    return []


def _cfg_value(cfg: Any, key: str) -> Any:
    try:
        value = getattr(cfg, key, None)
        if value is not None:
            return value
    except Exception:
        pass
    try:
        if hasattr(cfg, "get"):
            value = cfg.get(key)
            if value is not None:
                return value
    except Exception:
        pass
    for bucket in ("settings", "config", "metadata", "meta"):
        try:
            nested = getattr(cfg, bucket, None)
            if isinstance(nested, dict) and nested.get(key) is not None:
                return nested.get(key)
        except Exception:
            pass
        try:
            if hasattr(cfg, "get"):
                nested = cfg.get(bucket)
                if isinstance(nested, dict) and nested.get(key) is not None:
                    return nested.get(key)
        except Exception:
            pass
    return None


async def _load_all_bots_flag(guild_id: int) -> bool:
    try:
        from stoney_verify.guild_config import get_guild_config
        cfg = await get_guild_config(int(guild_id), refresh=True)
        raw = _cfg_value(cfg, f"spam_{_ALL_BOTS_KEY}")
        if raw is None:
            raw = _cfg_value(cfg, _ALL_BOTS_KEY)
        return _safe_bool(raw, False)
    except Exception:
        return False


async def _save_all_bots_flag(guild_id: int, value: bool) -> bool:
    try:
        from stoney_verify.guild_config import invalidate_guild_config, upsert_guild_config
        await upsert_guild_config(int(guild_id), {_ALL_BOTS_KEY: bool(value), f"spam_{_ALL_BOTS_KEY}": bool(value)})
        invalidate_guild_config(int(guild_id))
        return True
    except Exception:
        return False


async def _patched_get_spam_settings(guild_id: int) -> dict[str, Any]:
    settings = dict(await _ORIGINAL_GET(int(guild_id)))
    all_bots = await _load_all_bots_flag(int(guild_id))
    settings[_ALL_BOTS_KEY] = bool(all_bots)
    settings[f"spam_{_ALL_BOTS_KEY}"] = bool(all_bots)
    try:
        from stoney_verify import spam_guard
        spam_guard._cache_runtime_settings(int(guild_id), settings, source="db+guild_config", persisted=True)
    except Exception:
        pass
    return settings


async def _patched_save_spam_settings(guild_id: int, patch: dict[str, Any], *, updated_by: discord.Member | None = None):
    raw = dict(patch or {})
    has_all_bots = _ALL_BOTS_KEY in raw or f"spam_{_ALL_BOTS_KEY}" in raw
    all_bots = _safe_bool(raw.get(_ALL_BOTS_KEY, raw.get(f"spam_{_ALL_BOTS_KEY}")), False)
    raw.pop(_ALL_BOTS_KEY, None)
    raw.pop(f"spam_{_ALL_BOTS_KEY}", None)
    settings, persisted = await _ORIGINAL_SAVE(int(guild_id), raw, updated_by=updated_by)
    settings = dict(settings or {})
    if has_all_bots:
        saved = await _save_all_bots_flag(int(guild_id), all_bots)
        settings[_ALL_BOTS_KEY] = bool(all_bots)
        settings[f"spam_{_ALL_BOTS_KEY}"] = bool(all_bots)
        persisted = bool(persisted or saved)
        try:
            from stoney_verify import spam_guard
            spam_guard._cache_runtime_settings(int(guild_id), settings, source="db+guild_config" if saved else "runtime", persisted=persisted)
        except Exception:
            pass
    return settings, bool(persisted)


def _bot_members(guild: discord.Guild) -> list[discord.Member]:
    bots = [m for m in list(getattr(guild, "members", []) or []) if isinstance(m, discord.Member) and getattr(m, "bot", False)]
    return sorted(bots, key=lambda m: str(getattr(m, "display_name", getattr(m, "name", ""))).lower())


def _text_channels(guild: discord.Guild) -> list[discord.TextChannel]:
    channels = [c for c in list(getattr(guild, "text_channels", []) or []) if isinstance(c, discord.TextChannel)]
    return sorted(channels, key=lambda c: (getattr(c.category, "position", -1) if getattr(c, "category", None) else -1, getattr(c, "position", 0), str(c.name).lower()))


def _slice(items: list[Any], page: int) -> tuple[list[Any], int, int]:
    total_pages = max(1, (len(items) + 24) // 25)
    safe_page = max(0, min(int(page or 0), total_pages - 1))
    return items[safe_page * 25 : safe_page * 25 + 25], safe_page, total_pages


def _scope_status(policy: Any, settings: dict[str, Any]) -> str:
    all_bots = _safe_bool(settings.get(_ALL_BOTS_KEY, settings.get(f"spam_{_ALL_BOTS_KEY}")), False)
    bot_ids = _as_id_list(settings.get("invite_hard_block_target_bot_ids"))
    channel_ids = _as_id_list(settings.get("invite_hard_block_target_channel_ids"))
    bot_line = "Every bot is watched" if all_bots else (f"{len(bot_ids)} selected bot/user ID(s)" if bot_ids else "No bots selected yet")
    channel_line = f"{len(channel_ids)} selected channel(s)" if channel_ids else "All message channels are watched"
    return f"**Bots/users:** {bot_line}\n**Channels:** {channel_line}"


def _scope_embed(guild: discord.Guild, policy: Any, settings: dict[str, Any], *, bot_page: int, channel_page: int) -> discord.Embed:
    bots = _bot_members(guild)
    channels = _text_channels(guild)
    _bot_items, bp, bot_pages = _slice(bots, bot_page)
    _chan_items, cp, channel_pages = _slice(channels, channel_page)
    embed = discord.Embed(
        title="🚫 Discord Invite Blocker Setup",
        description=(
            "Use this when a bot or member is allowed to talk, but Discord invite links should still be handled by Dank Shield.\n\n"
            "For OneBump: choose **Watch Every Bot** or pick OneBump, then choose **This Channel** or **All Channels**."
        ),
        color=discord.Color.blurple(),
    )
    embed.add_field(name="What is watched right now", value=_scope_status(policy, settings), inline=False)
    embed.add_field(
        name="Buttons explained",
        value=(
            "**Watch Every Bot** = easiest option for bump/listing bots.\n"
            "**Only Listed Bots** = only selected bot/user IDs.\n"
            "**This Channel** = only the channel where you opened this setup.\n"
            "**All Channels** = every message channel Dank Shield can monitor.\n"
            "**Paste IDs** = bulk paste bot, user, or channel IDs."
        ),
        inline=False,
    )
    embed.add_field(
        name="Dropdown pages",
        value=f"Bot dropdown page `{bp + 1}/{bot_pages}` • Channel dropdown page `{cp + 1}/{channel_pages}`. Use page buttons only if the item is not visible.",
        inline=False,
    )
    return embed


async def _save_scope(guild: discord.Guild, actor: discord.abc.User, patch: dict[str, Any]) -> dict[str, Any]:
    from stoney_verify import spam_guard
    settings, _persisted = await spam_guard.save_spam_settings(int(guild.id), patch, updated_by=actor if isinstance(actor, discord.Member) else None)
    return dict(settings or {})


async def _refresh_center_message(guild: discord.Guild, actor_id: int, channel_id: int, message_id: int) -> None:
    try:
        from stoney_verify.startup_guards import protection_center_invite_controls_guard as pc
        center, _spam_guard, _policy = pc._patch_helpers()
        await pc._refresh_original_protection_message(guild=guild, author_id=int(actor_id), channel_id=int(channel_id), message_id=int(message_id), center=center)
    except Exception:
        pass


class BotPageSelect(discord.ui.Select):
    def __init__(self, *, guild: discord.Guild, channel_id: int, message_id: int, bot_page: int, channel_page: int, settings: dict[str, Any]) -> None:
        bots, safe_page, total_pages = _slice(_bot_members(guild), bot_page)
        options: list[discord.SelectOption] = []
        selected = set(_as_id_list(settings.get("invite_hard_block_target_bot_ids")))
        for bot_member in bots:
            label = str(getattr(bot_member, "display_name", bot_member.name))[:90]
            options.append(discord.SelectOption(label=label, value=str(bot_member.id), description=f"Watch invite links from bot ID {bot_member.id}"[:100], default=str(bot_member.id) in selected))
        if not options:
            options.append(discord.SelectOption(label="No bots cached", value="none", description="Use Watch Every Bot or Paste IDs."))
        super().__init__(placeholder=f"Pick bot(s) to watch • page {safe_page + 1}/{total_pages}", min_values=1, max_values=max(1, min(len(options), 25)), options=options, row=0)
        self.guild = guild
        self.channel_id = int(channel_id)
        self.message_id = int(message_id)
        self.bot_page = safe_page
        self.channel_page = int(channel_page)

    async def callback(self, interaction: discord.Interaction) -> None:
        if "none" in self.values:
            return await interaction.response.defer(ephemeral=True)
        from stoney_verify.startup_guards import spam_guard_invite_override_options as policy
        current = await _patched_get_spam_settings(int(self.guild.id))
        ids = _as_id_list(current.get("invite_hard_block_target_bot_ids"))
        for value in self.values:
            if value.isdigit() and value not in ids:
                ids.append(value)
        settings = await _save_scope(self.guild, interaction.user, {"invite_hard_block_target_bot_ids": ids})
        await _refresh_center_message(self.guild, int(interaction.user.id), self.channel_id, self.message_id)
        await interaction.response.edit_message(embed=_scope_embed(self.guild, policy, settings, bot_page=self.bot_page, channel_page=self.channel_page), view=PagedInviteScopeView(guild=self.guild, channel_id=self.channel_id, message_id=self.message_id, settings=settings, bot_page=self.bot_page, channel_page=self.channel_page))


class ChannelPageSelect(discord.ui.Select):
    def __init__(self, *, guild: discord.Guild, channel_id: int, message_id: int, bot_page: int, channel_page: int, settings: dict[str, Any]) -> None:
        channels, safe_page, total_pages = _slice(_text_channels(guild), channel_page)
        selected = set(_as_id_list(settings.get("invite_hard_block_target_channel_ids")))
        options: list[discord.SelectOption] = []
        for channel in channels:
            label = f"#{channel.name}"[:90]
            cat = str(getattr(getattr(channel, "category", None), "name", "No category"))[:45]
            options.append(discord.SelectOption(label=label, value=str(channel.id), description=f"{cat} • {channel.id}"[:100], default=str(channel.id) in selected))
        if not options:
            options.append(discord.SelectOption(label="No channels cached", value="none", description="Use Paste IDs."))
        super().__init__(placeholder=f"Pick channel(s) to watch • page {safe_page + 1}/{total_pages}", min_values=1, max_values=max(1, min(len(options), 25)), options=options, row=1)
        self.guild = guild
        self.channel_id = int(channel_id)
        self.message_id = int(message_id)
        self.bot_page = int(bot_page)
        self.channel_page = safe_page

    async def callback(self, interaction: discord.Interaction) -> None:
        if "none" in self.values:
            return await interaction.response.defer(ephemeral=True)
        from stoney_verify.startup_guards import spam_guard_invite_override_options as policy
        current = await _patched_get_spam_settings(int(self.guild.id))
        ids = _as_id_list(current.get("invite_hard_block_target_channel_ids"))
        for value in self.values:
            if value.isdigit() and value not in ids:
                ids.append(value)
        settings = await _save_scope(self.guild, interaction.user, {"invite_hard_block_target_channel_ids": ids})
        await _refresh_center_message(self.guild, int(interaction.user.id), self.channel_id, self.message_id)
        await interaction.response.edit_message(embed=_scope_embed(self.guild, policy, settings, bot_page=self.bot_page, channel_page=self.channel_page), view=PagedInviteScopeView(guild=self.guild, channel_id=self.channel_id, message_id=self.message_id, settings=settings, bot_page=self.bot_page, channel_page=self.channel_page))


class ManualScopeModal(discord.ui.Modal, title="Paste IDs for Invite Blocker"):
    def __init__(self, *, guild: discord.Guild, channel_id: int, message_id: int, settings: dict[str, Any]) -> None:
        super().__init__(timeout=300)
        from stoney_verify.startup_guards import spam_guard_invite_override_options as policy
        self.guild = guild
        self.channel_id = int(channel_id)
        self.message_id = int(message_id)
        self.bot_ids = discord.ui.TextInput(label="Bot/user IDs to watch", placeholder="Example: 123, 456 or <@123>. Blank clears selected bot/user IDs.", default=policy._ids_text(settings.get("invite_hard_block_target_bot_ids")), required=False, style=discord.TextStyle.paragraph, max_length=1200)
        self.channel_ids = discord.ui.TextInput(label="Channel IDs to watch", placeholder="Example: 123, 456 or <#123>. Blank = all message channels.", default=policy._ids_text(settings.get("invite_hard_block_target_channel_ids")), required=False, style=discord.TextStyle.paragraph, max_length=1200)
        self.add_item(self.bot_ids)
        self.add_item(self.channel_ids)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        from stoney_verify.startup_guards import spam_guard_invite_override_options as policy
        settings = await _save_scope(self.guild, interaction.user, {"invite_hard_block_target_bot_ids": policy._parse_ids(self.bot_ids.value), "invite_hard_block_target_channel_ids": policy._parse_ids(self.channel_ids.value)})
        await _refresh_center_message(self.guild, int(interaction.user.id), self.channel_id, self.message_id)
        await interaction.response.edit_message(embed=_scope_embed(self.guild, policy, settings, bot_page=0, channel_page=0), view=PagedInviteScopeView(guild=self.guild, channel_id=self.channel_id, message_id=self.message_id, settings=settings, bot_page=0, channel_page=0))


class PagedInviteScopeView(discord.ui.View):
    def __init__(self, *, guild: discord.Guild, channel_id: int, message_id: int, settings: dict[str, Any], bot_page: int = 0, channel_page: int = 0) -> None:
        super().__init__(timeout=900)
        self.guild = guild
        self.channel_id = int(channel_id)
        self.message_id = int(message_id)
        self.settings = dict(settings or {})
        self.bot_page = int(bot_page)
        self.channel_page = int(channel_page)
        self.add_item(BotPageSelect(guild=guild, channel_id=channel_id, message_id=message_id, bot_page=bot_page, channel_page=channel_page, settings=settings))
        self.add_item(ChannelPageSelect(guild=guild, channel_id=channel_id, message_id=message_id, bot_page=bot_page, channel_page=channel_page, settings=settings))

    async def _redraw(self, interaction: discord.Interaction, *, bot_page: int | None = None, channel_page: int | None = None, settings: dict[str, Any] | None = None) -> None:
        from stoney_verify.startup_guards import spam_guard_invite_override_options as policy
        next_settings = settings if settings is not None else await _patched_get_spam_settings(int(self.guild.id))
        bp = self.bot_page if bot_page is None else int(bot_page)
        cp = self.channel_page if channel_page is None else int(channel_page)
        await interaction.response.edit_message(embed=_scope_embed(self.guild, policy, next_settings, bot_page=bp, channel_page=cp), view=PagedInviteScopeView(guild=self.guild, channel_id=self.channel_id, message_id=self.message_id, settings=next_settings, bot_page=bp, channel_page=cp))

    @discord.ui.button(label="◀ Bot Page", style=discord.ButtonStyle.secondary, row=2)
    async def prev_bots(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await self._redraw(interaction, bot_page=max(0, self.bot_page - 1))

    @discord.ui.button(label="Bot Page ▶", style=discord.ButtonStyle.secondary, row=2)
    async def next_bots(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        total = max(1, (len(_bot_members(self.guild)) + 24) // 25)
        await self._redraw(interaction, bot_page=min(total - 1, self.bot_page + 1))

    @discord.ui.button(label="Watch Every Bot", emoji="🤖", style=discord.ButtonStyle.success, row=2)
    async def all_bots(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        settings = await _save_scope(self.guild, interaction.user, {_ALL_BOTS_KEY: True})
        await _refresh_center_message(self.guild, int(interaction.user.id), self.channel_id, self.message_id)
        await self._redraw(interaction, settings=settings)

    @discord.ui.button(label="Only Listed Bots", emoji="👤", style=discord.ButtonStyle.secondary, row=2)
    async def humans_only(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        settings = await _save_scope(self.guild, interaction.user, {_ALL_BOTS_KEY: False, "invite_hard_block_target_bot_ids": []})
        await _refresh_center_message(self.guild, int(interaction.user.id), self.channel_id, self.message_id)
        await self._redraw(interaction, settings=settings)

    @discord.ui.button(label="◀ Channel Page", style=discord.ButtonStyle.secondary, row=3)
    async def prev_channels(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await self._redraw(interaction, channel_page=max(0, self.channel_page - 1))

    @discord.ui.button(label="Channel Page ▶", style=discord.ButtonStyle.secondary, row=3)
    async def next_channels(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        total = max(1, (len(_text_channels(self.guild)) + 24) // 25)
        await self._redraw(interaction, channel_page=min(total - 1, self.channel_page + 1))

    @discord.ui.button(label="All Channels", emoji="🌐", style=discord.ButtonStyle.success, row=3)
    async def all_channels(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        settings = await _save_scope(self.guild, interaction.user, {"invite_hard_block_target_channel_ids": []})
        await _refresh_center_message(self.guild, int(interaction.user.id), self.channel_id, self.message_id)
        await self._redraw(interaction, settings=settings)

    @discord.ui.button(label="This Channel", emoji="#️⃣", style=discord.ButtonStyle.primary, row=3)
    async def this_channel(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        current = await _patched_get_spam_settings(int(self.guild.id))
        ids = _as_id_list(current.get("invite_hard_block_target_channel_ids"))
        channel_id = str(self.channel_id)
        if channel_id not in ids:
            ids.append(channel_id)
        settings = await _save_scope(self.guild, interaction.user, {"invite_hard_block_target_channel_ids": ids})
        await _refresh_center_message(self.guild, int(interaction.user.id), self.channel_id, self.message_id)
        await self._redraw(interaction, settings=settings)

    @discord.ui.button(label="Paste IDs", emoji="✍️", style=discord.ButtonStyle.secondary, row=4)
    async def manual(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await interaction.response.send_modal(ManualScopeModal(guild=self.guild, channel_id=self.channel_id, message_id=self.message_id, settings=await _patched_get_spam_settings(int(self.guild.id))))

    @discord.ui.button(label="Done", emoji="✅", style=discord.ButtonStyle.success, row=4)
    async def done(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await interaction.response.edit_message(content="Discord Invite Blocker setup closed. Reopen it from Protection Center if needed.", embed=None, view=None)


def _patch_protection_center_scope() -> None:
    try:
        from stoney_verify.startup_guards import protection_center_invite_controls_guard as pc
        from stoney_verify.startup_guards import spam_guard_invite_override_options as policy

        def patched_format_scope_status(policy_obj: Any, settings: dict[str, Any]) -> str:
            return _scope_status(policy_obj, settings)

        class BetterProtectionInviteScopeButton(discord.ui.Button):
            def __init__(self) -> None:
                super().__init__(label="Discord Invite Blocker", emoji="🚫", style=discord.ButtonStyle.primary, custom_id="dank_protection:invite_scope", row=3)

            async def callback(self, interaction: discord.Interaction) -> None:
                center, spam_guard, _p = pc._patch_helpers()
                if not await center._require_setup_permission(interaction):
                    return
                guild = interaction.guild
                message = interaction.message
                if guild is None or message is None:
                    return await center._send_ephemeral(interaction, "❌ Invalid Protection Center context.")
                settings = await spam_guard.get_spam_settings(int(guild.id))
                await interaction.response.send_message(
                    embed=_scope_embed(guild, policy, settings, bot_page=0, channel_page=0),
                    view=PagedInviteScopeView(guild=guild, channel_id=int(getattr(message.channel, "id", 0) or 0), message_id=int(message.id), settings=settings),
                    ephemeral=True,
                    allowed_mentions=discord.AllowedMentions.none(),
                )

        pc._format_scope_status = patched_format_scope_status
        pc.ProtectionInviteScopeButton = BetterProtectionInviteScopeButton
    except Exception:
        pass


def apply() -> bool:
    global _PATCHED, _ORIGINAL_GET, _ORIGINAL_SAVE
    if _PATCHED:
        return True
    try:
        from stoney_verify import spam_guard
        from stoney_verify.startup_guards import spam_guard_invite_override_options as policy
        try:
            policy.apply()
        except Exception:
            pass
        _ORIGINAL_GET = spam_guard.get_spam_settings
        _ORIGINAL_SAVE = spam_guard.save_spam_settings
        spam_guard.get_spam_settings = _patched_get_spam_settings
        spam_guard.save_spam_settings = _patched_save_spam_settings
        try:
            policy._scope_summary = _scope_status
        except Exception:
            pass
        _patch_protection_center_scope()
        _PATCHED = True
        print("✅ spam_guard_invite_scope_pagination_guard active; Discord Invite Blocker setup is plain-language")
        return True
    except Exception as exc:
        try:
            print(f"⚠️ spam_guard_invite_scope_pagination_guard failed: {type(exc).__name__}: {exc}")
        except Exception:
            pass
        return False


apply()

__all__ = ["apply"]