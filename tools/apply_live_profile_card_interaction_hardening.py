from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def replace_once(path: str, old: str, new: str) -> None:
    target = ROOT / path
    source = target.read_text(encoding="utf-8")
    count = source.count(old)
    if count != 1:
        raise RuntimeError(f"expected one anchor in {path}: {old!r}; found {count}")
    target.write_text(source.replace(old, new, 1), encoding="utf-8")


# Fresh hosts must always perform their first reconciliation.
replace_once(
    "stoney_verify/profile_card_runtime.py",
    '''        self._reconcile_lock = asyncio.Lock()\n        self._last_reconcile_at = 0.0\n\n    async def on_ready(self) -> None:\n''',
    '''        self._reconcile_lock = asyncio.Lock()\n        self._last_reconcile_at: Optional[float] = None\n\n    async def on_ready(self) -> None:\n''',
)
replace_once(
    "stoney_verify/profile_card_runtime.py",
    '''        now = monotonic()\n        if now - self._last_reconcile_at < READY_RECONCILE_THROTTLE_SECONDS:\n            return\n        async with self._reconcile_lock:\n            now = monotonic()\n            if now - self._last_reconcile_at < READY_RECONCILE_THROTTLE_SECONDS:\n                return\n''',
    '''        now = monotonic()\n        if (\n            self._last_reconcile_at is not None\n            and now - self._last_reconcile_at < READY_RECONCILE_THROTTLE_SECONDS\n        ):\n            return\n        async with self._reconcile_lock:\n            now = monotonic()\n            if (\n                self._last_reconcile_at is not None\n                and now - self._last_reconcile_at < READY_RECONCILE_THROTTLE_SECONDS\n            ):\n                return\n''',
)

# Remove a member's actual persisted cards, not every guild in the bot.
replace_once(
    "stoney_verify/profile_card_runtime.py",
    '''    async def remove_user_cards(self, guild: discord.Guild, user_id: int) -> None:\n        """Remove only this member's owned live cards in one guild.\n\n        Privacy changes use this immediately so an already-posted card cannot\n        keep displaying data the member just hid. Failed Discord deletions keep\n        their durable state so reconciliation can retry later.\n        """\n        resolved_user_id = int(user_id)\n        try:\n            config = parse_live_card_config(await get_guild_config(guild.id))\n        except Exception:\n            return\n\n        for channel_id in config.channel_ids:\n            key = (int(guild.id), int(channel_id))\n            latest = self._latest.get(key)\n            if latest is not None and latest.user_id == resolved_user_id:\n                pending = self._pending.pop(key, None)\n                self._latest.pop(key, None)\n                if pending is not None and not pending.done():\n                    pending.cancel()\n\n            try:\n                state = await get_live_card_state(*key)\n            except ProfileStorageUnavailable:\n                return\n            if not state or str(state.get("user_id") or "") != str(resolved_user_id):\n                continue\n\n            channel = guild.get_channel(channel_id)\n            try:\n                message_id = int(str(state.get("message_id") or "0"))\n            except Exception:\n                message_id = 0\n            removed = not message_id\n            if isinstance(channel, discord.TextChannel) and message_id:\n                removed = await self._delete_stored_message(channel, message_id)\n            if removed:\n                try:\n                    await delete_live_card_state(*key)\n                except ProfileStorageUnavailable:\n                    return\n            self._last_posted.pop(key, None)\n\n    async def remove_user_cards_all_guilds(self, user_id: int) -> None:\n        for guild in list(getattr(self.bot, "guilds", []) or []):\n            try:\n                await self.remove_user_cards(guild, int(user_id))\n            except Exception:\n                continue\n''',
    '''    async def _remove_user_card_states(\n        self,\n        user_id: int,\n        *,\n        guild_id: Optional[int] = None,\n        guild_hint: Optional[discord.Guild] = None,\n    ) -> None:\n        resolved_user_id = int(user_id)\n        resolved_guild_id = int(guild_id) if guild_id is not None else None\n\n        # Stop an in-flight debounce before it can repost fields the member just\n        # hid, even when no prior durable card exists yet.\n        for key, latest in list(self._latest.items()):\n            if latest.user_id != resolved_user_id:\n                continue\n            if resolved_guild_id is not None and key[0] != resolved_guild_id:\n                continue\n            pending = self._pending.pop(key, None)\n            self._latest.pop(key, None)\n            if pending is not None and not pending.done():\n                pending.cancel()\n            self._last_posted.pop(key, None)\n\n        try:\n            states = await list_live_card_states()\n        except ProfileStorageUnavailable:\n            return\n\n        for state in states:\n            if str(state.get("user_id") or "") != str(resolved_user_id):\n                continue\n            try:\n                state_guild_id = int(state.get("guild_id"))\n                channel_id = int(state.get("channel_id"))\n                message_id = int(str(state.get("message_id") or "0"))\n            except Exception:\n                continue\n            if resolved_guild_id is not None and state_guild_id != resolved_guild_id:\n                continue\n\n            guild = (\n                guild_hint\n                if guild_hint is not None and int(guild_hint.id) == state_guild_id\n                else self.bot.get_guild(state_guild_id)\n            )\n            if guild is None:\n                # Keep durable ownership so reconciliation can retry when the\n                # guild is available again.\n                continue\n            channel = guild.get_channel(channel_id)\n            removed = not message_id\n            if message_id and isinstance(channel, discord.TextChannel):\n                removed = await self._delete_stored_message(channel, message_id)\n            elif message_id and channel is None:\n                removed = True\n            if not removed:\n                continue\n            try:\n                await delete_live_card_state(state_guild_id, channel_id)\n            except ProfileStorageUnavailable:\n                return\n            self._last_posted.pop((state_guild_id, channel_id), None)\n\n    async def remove_user_cards(self, guild: discord.Guild, user_id: int) -> None:\n        """Remove this member's persisted and pending live cards in one guild."""\n        await self._remove_user_card_states(\n            int(user_id),\n            guild_id=int(guild.id),\n            guild_hint=guild,\n        )\n\n    async def remove_user_cards_all_guilds(self, user_id: int) -> None:\n        """Remove only the member's actual persisted cards across all guilds."""\n        await self._remove_user_card_states(int(user_id))\n''',
)

# Avoid duplicate private-storage reads in the settings panel.
replace_once(
    "stoney_verify/commands_ext/public_profile_cards.py",
    '''    DEFAULT_PROFILE_PREFERENCES,\n    InvalidPlatformProfile,\n''',
    '''    DEFAULT_PROFILE_PREFERENCES,\n    InvalidPlatformProfile,\n    effective_preferences,\n''',
)
replace_once(
    "stoney_verify/commands_ext/public_profile_cards.py",
    '''async def _settings_payload(guild_id: int, user_id: int) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:\n    user_row, guild_row, effective = await asyncio.gather(\n        get_profile_user(user_id),\n        get_profile_guild_settings(guild_id, user_id),\n        get_effective_profile_settings(guild_id, user_id),\n    )\n    return user_row, guild_row, effective\n''',
    '''async def _settings_payload(guild_id: int, user_id: int) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:\n    user_row, guild_row = await asyncio.gather(\n        get_profile_user(user_id),\n        get_profile_guild_settings(guild_id, user_id),\n    )\n    effective = {\n        "preferences": effective_preferences(\n            user_row.get("preferences"),\n            guild_row.get("settings"),\n        ),\n        "platforms": dict(user_row.get("platforms") or {}),\n    }\n    return user_row, guild_row, effective\n''',
)

# All database-backed interactions acknowledge Discord before slow I/O.
replace_once(
    "stoney_verify/commands_ext/public_profile_cards.py",
    '''async def _safe_ephemeral(\n    interaction: discord.Interaction,\n    content: str,\n    *,\n    ok: bool = True,\n) -> None:\n    prefix = "✅ " if ok else "❌ "\n    kwargs = {\n        "content": prefix + str(content or "")[:1900],\n        "ephemeral": True,\n        "allowed_mentions": discord.AllowedMentions.none(),\n    }\n    try:\n        if not interaction.response.is_done():\n            await interaction.response.send_message(**kwargs)\n        else:\n            await interaction.followup.send(**kwargs)\n    except Exception:\n        pass\n''',
    '''async def _defer_private(\n    interaction: discord.Interaction,\n    *,\n    component_update: bool = False,\n) -> None:\n    if interaction.response.is_done():\n        return\n    if component_update:\n        await interaction.response.defer()\n    else:\n        await interaction.response.defer(ephemeral=True, thinking=True)\n\n\nasync def _send_private(\n    interaction: discord.Interaction,\n    **kwargs: Any,\n) -> None:\n    kwargs.setdefault("ephemeral", True)\n    kwargs.setdefault("allowed_mentions", discord.AllowedMentions.none())\n    if not interaction.response.is_done():\n        await interaction.response.send_message(**kwargs)\n    else:\n        await interaction.followup.send(**kwargs)\n\n\nasync def _safe_ephemeral(\n    interaction: discord.Interaction,\n    content: str,\n    *,\n    ok: bool = True,\n) -> None:\n    prefix = "✅ " if ok else "❌ "\n    try:\n        await _send_private(\n            interaction,\n            content=prefix + str(content or "")[:1900],\n        )\n    except Exception:\n        pass\n''',
)

replace_once(
    "stoney_verify/commands_ext/public_profile_cards.py",
    '''        await interaction.response.edit_message(\n            embed=_settings_embed(member, user_row, guild_row, effective),\n            view=ProfileSettingsView(\n                author_id=self.author_id,\n                guild_id=self.guild_id,\n                preferences=preferences,\n            ),\n            allowed_mentions=discord.AllowedMentions.none(),\n        )\n''',
    '''        payload = {\n            "embed": _settings_embed(member, user_row, guild_row, effective),\n            "view": ProfileSettingsView(\n                author_id=self.author_id,\n                guild_id=self.guild_id,\n                preferences=preferences,\n            ),\n            "allowed_mentions": discord.AllowedMentions.none(),\n        }\n        if interaction.response.is_done():\n            await interaction.edit_original_response(**payload)\n        else:\n            await interaction.response.edit_message(**payload)\n''',
)
replace_once(
    "stoney_verify/commands_ext/public_profile_cards.py",
    '''        try:\n            effective = await get_effective_profile_settings(view.guild_id, view.author_id)\n''',
    '''        await _defer_private(interaction, component_update=True)\n        try:\n            effective = await get_effective_profile_settings(view.guild_id, view.author_id)\n''',
)
replace_once(
    "stoney_verify/commands_ext/public_profile_cards.py",
    '''        try:\n            config = await get_guild_config(view.guild_id)\n''',
    '''        await _defer_private(interaction)\n        try:\n            config = await get_guild_config(view.guild_id)\n''',
)
replace_once(
    "stoney_verify/commands_ext/public_profile_cards.py",
    '''        await interaction.response.send_message(\n            embed=rendered.embed,\n            view=rendered.view,\n            ephemeral=True,\n            allowed_mentions=discord.AllowedMentions.none(),\n        )\n''',
    '''        await _send_private(\n            interaction,\n            embed=rendered.embed,\n            view=rendered.view,\n        )\n''',
)
replace_once(
    "stoney_verify/commands_ext/public_profile_cards.py",
    '''    if guild is None or member is None:\n        return await _safe_ephemeral(interaction, "Use this command inside a server.", ok=False)\n    try:\n''',
    '''    if guild is None or member is None:\n        return await _safe_ephemeral(interaction, "Use this command inside a server.", ok=False)\n    await _defer_private(interaction)\n    try:\n''',
)
replace_once(
    "stoney_verify/commands_ext/public_profile_cards.py",
    '''    await interaction.response.send_message(\n        embed=_settings_embed(member, user_row, guild_row, effective),\n        view=ProfileSettingsView(author_id=member.id, guild_id=guild.id, preferences=preferences),\n        ephemeral=True,\n        allowed_mentions=discord.AllowedMentions.none(),\n    )\n''',
    '''    await _send_private(\n        interaction,\n        embed=_settings_embed(member, user_row, guild_row, effective),\n        view=ProfileSettingsView(author_id=member.id, guild_id=guild.id, preferences=preferences),\n    )\n''',
)

# Slash commands that perform storage work defer after local validation.
replace_once(
    "stoney_verify/commands_ext/public_profile_cards.py",
    '''    if interaction.guild is None:\n        return await _safe_ephemeral(interaction, "Use this command inside a server.", ok=False)\n    try:\n        entry = await save_platform_identity(\n''',
    '''    if interaction.guild is None:\n        return await _safe_ephemeral(interaction, "Use this command inside a server.", ok=False)\n    await _defer_private(interaction)\n    try:\n        entry = await save_platform_identity(\n''',
)
replace_once(
    "stoney_verify/commands_ext/public_profile_cards.py",
    '''    if interaction.guild is None:\n        return await _safe_ephemeral(interaction, "Use this command inside a server.", ok=False)\n    try:\n        removed = await remove_platform_identity(interaction.user.id, platform.value)\n''',
    '''    if interaction.guild is None:\n        return await _safe_ephemeral(interaction, "Use this command inside a server.", ok=False)\n    await _defer_private(interaction)\n    try:\n        removed = await remove_platform_identity(interaction.user.id, platform.value)\n''',
)
replace_once(
    "stoney_verify/commands_ext/public_profile_cards.py",
    '''        if not isinstance(member, discord.Member):\n            return await _safe_ephemeral(interaction, "That member is no longer available.", ok=False)\n        try:\n''',
    '''        if not isinstance(member, discord.Member):\n            return await _safe_ephemeral(interaction, "That member is no longer available.", ok=False)\n        await _defer_private(interaction)\n        try:\n''',
)
replace_once(
    "stoney_verify/commands_ext/public_profile_cards.py",
    '''        await interaction.response.send_message(\n            embed=_profile_full_roles_embed(member),\n            ephemeral=True,\n            allowed_mentions=discord.AllowedMentions.none(),\n        )\n''',
    '''        await _send_private(\n            interaction,\n            embed=_profile_full_roles_embed(member),\n        )\n''',
)
replace_once(
    "stoney_verify/commands_ext/public_profile_cards.py",
    '''    if guild is None or int(member.guild.id) != int(guild.id):\n        return await _safe_ephemeral(interaction, "That member is not available in this server.", ok=False)\n    try:\n''',
    '''    if guild is None or int(member.guild.id) != int(guild.id):\n        return await _safe_ephemeral(interaction, "That member is not available in this server.", ok=False)\n    await _defer_private(interaction)\n    try:\n''',
)
replace_once(
    "stoney_verify/commands_ext/public_profile_cards.py",
    '''    await interaction.response.send_message(\n        embed=rendered_embed,\n        view=view if view.children else None,\n        ephemeral=True,\n        allowed_mentions=discord.AllowedMentions.none(),\n    )\n''',
    '''    await _send_private(\n        interaction,\n        embed=rendered_embed,\n        view=view if view.children else None,\n    )\n''',
)

# Manager commands defer before database writes and cleanup work.
replace_once(
    "stoney_verify/commands_ext/public_profile_cards.py",
    '''        if missing:\n            return await _safe_ephemeral(\n                interaction,\n                f"Fix Dank Shield permissions in {channel.mention}: " + ", ".join(missing),\n                ok=False,\n            )\n\n    config = await get_guild_config(guild.id, refresh=True)\n''',
    '''        if missing:\n            return await _safe_ephemeral(\n                interaction,\n                f"Fix Dank Shield permissions in {channel.mention}: " + ", ".join(missing),\n                ok=False,\n            )\n\n    await _defer_private(interaction)\n    config = await get_guild_config(guild.id, refresh=True)\n''',
)
replace_once(
    "stoney_verify/commands_ext/public_profile_cards.py",
    '''    await interaction.response.send_message(\n        embed=_live_status_embed(guild, updated),\n        ephemeral=True,\n        allowed_mentions=discord.AllowedMentions.none(),\n    )\n''',
    '''    await _send_private(interaction, embed=_live_status_embed(guild, updated))\n''',
)
replace_once(
    "stoney_verify/commands_ext/public_profile_cards.py",
    '''    if guild is None:\n        return await _safe_ephemeral(interaction, "Use this command inside a server.", ok=False)\n    allowed: list[str] = []\n''',
    '''    if guild is None:\n        return await _safe_ephemeral(interaction, "Use this command inside a server.", ok=False)\n    await _defer_private(interaction)\n    allowed: list[str] = []\n''',
)
replace_once(
    "stoney_verify/commands_ext/public_profile_cards.py",
    '''    await interaction.response.send_message(\n        embed=_live_status_embed(guild, updated),\n        ephemeral=True,\n        allowed_mentions=discord.AllowedMentions.none(),\n    )\n\n\nasync def profile_live_status''',
    '''    await _send_private(interaction, embed=_live_status_embed(guild, updated))\n\n\nasync def profile_live_status''',
)
replace_once(
    "stoney_verify/commands_ext/public_profile_cards.py",
    '''    if guild is None:\n        return await _safe_ephemeral(interaction, "Use this command inside a server.", ok=False)\n    config = await get_guild_config(guild.id, refresh=True)\n    await interaction.response.send_message(\n        embed=_live_status_embed(guild, config),\n        ephemeral=True,\n        allowed_mentions=discord.AllowedMentions.none(),\n    )\n''',
    '''    if guild is None:\n        return await _safe_ephemeral(interaction, "Use this command inside a server.", ok=False)\n    await _defer_private(interaction)\n    config = await get_guild_config(guild.id, refresh=True)\n    await _send_private(interaction, embed=_live_status_embed(guild, config))\n''',
)

# The canonical public surface includes the existing /dank profile group.
replace_once(
    "stoney_verify/commands_ext/__init__.py",
    '''    "roles",\n    "modlog",\n''',
    '''    "roles",\n    "profile",\n    "modlog",\n''',
)

print("Applied profile interaction and scale hardening.")
