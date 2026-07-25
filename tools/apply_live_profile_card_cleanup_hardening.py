from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def replace_once(path: str, old: str, new: str) -> None:
    target = ROOT / path
    source = target.read_text(encoding="utf-8")
    count = source.count(old)
    if count != 1:
        raise RuntimeError(f"expected one anchor in {path}: {old!r}; found {count}")
    target.write_text(source.replace(old, new, 1), encoding="utf-8")


replace_once(
    "stoney_verify/profile_card_service.py",
    '''    "xbox": PlatformSpec("xbox", "Xbox", "🟢", ("xbox.com", "www.xbox.com"), "official"),\n    "playstation": PlatformSpec(\n        "playstation",\n        "PlayStation",\n        "🔷",\n        ("playstation.com", "www.playstation.com", "profiles.playstation.com"),\n        "official",\n    ),\n''',
    '''    # Xbox and PlayStation do not expose one stable, reliably canonical\n    # public-profile URL format across regions. Keep them username-only rather\n    # than accepting a generic official-site page or inventing a link.\n    "xbox": PlatformSpec("xbox", "Xbox", "🟢"),\n    "playstation": PlatformSpec("playstation", "PlayStation", "🔷"),\n''',
)

replace_once(
    "stoney_verify/profile_card_runtime.py",
    '''    async def disable_channel(self, guild: discord.Guild, channel: discord.TextChannel) -> None:\n        key = (int(guild.id), int(channel.id))\n        pending = self._pending.pop(key, None)\n        self._latest.pop(key, None)\n        self._last_posted.pop(key, None)\n        if pending is not None and not pending.done():\n            pending.cancel()\n        try:\n            state = await get_live_card_state(*key)\n        except ProfileStorageUnavailable:\n            return\n        message_id = int(str((state or {}).get("message_id") or "0")) if state else 0\n        if message_id:\n            await self._delete_stored_message(channel, message_id)\n        try:\n            await delete_live_card_state(*key)\n        except ProfileStorageUnavailable:\n            pass\n''',
    '''    async def _remove_channel_card_state(\n        self,\n        guild: discord.Guild,\n        channel_id: int,\n        *,\n        cancel_pending: bool = True,\n    ) -> bool:\n        key = (int(guild.id), int(channel_id))\n        if cancel_pending:\n            pending = self._pending.pop(key, None)\n            self._latest.pop(key, None)\n            if pending is not None and not pending.done():\n                pending.cancel()\n        self._last_posted.pop(key, None)\n\n        try:\n            state = await get_live_card_state(*key)\n        except ProfileStorageUnavailable:\n            return False\n        if not state:\n            return True\n\n        try:\n            message_id = int(str(state.get("message_id") or "0"))\n        except Exception:\n            message_id = 0\n        channel = guild.get_channel(channel_id)\n        removed = not message_id\n        if message_id and isinstance(channel, discord.TextChannel):\n            removed = await self._delete_stored_message(channel, message_id)\n        elif message_id and channel is None:\n            # The channel no longer exists, so the durable state is stale.\n            removed = True\n\n        if not removed:\n            # Keep ownership state so a later reconciliation can retry safely.\n            return False\n        try:\n            await delete_live_card_state(*key)\n        except ProfileStorageUnavailable:\n            return False\n        return True\n\n    async def invalidate_guild_cards(self, guild: discord.Guild) -> None:\n        try:\n            config = parse_live_card_config(await get_guild_config(guild.id))\n        except Exception:\n            return\n        for channel_id in config.channel_ids:\n            await self._remove_channel_card_state(guild, channel_id)\n\n    async def disable_channel(self, guild: discord.Guild, channel: discord.TextChannel) -> None:\n        await self._remove_channel_card_state(guild, channel.id)\n''',
)

replace_once(
    "stoney_verify/profile_card_runtime.py",
    '''            except Exception:\n                continue\n            for channel_id in config.channel_ids:\n''',
    '''            except Exception:\n                continue\n            if not config.enabled:\n                continue\n            for channel_id in config.channel_ids:\n''',
)

replace_once(
    "stoney_verify/profile_card_runtime.py",
    '''        # State for deleted/disabled channels is removed, but no unknown message is deleted.\n        for (guild_id, channel_id), _state in persisted.items():\n            try:\n                await delete_live_card_state(guild_id, channel_id)\n            except ProfileStorageUnavailable:\n                return\n''',
    '''        # Disabled channels are cleaned only when the stored message is still\n        # verifiably Dank Shield-owned. Inaccessible guilds keep their state so a\n        # later reconciliation can retry rather than orphaning a card.\n        for (guild_id, channel_id), state in persisted.items():\n            guild = self.bot.get_guild(guild_id)\n            if guild is None:\n                continue\n            channel = guild.get_channel(channel_id)\n            try:\n                message_id = int(str(state.get("message_id") or "0"))\n            except Exception:\n                message_id = 0\n            removed = not message_id\n            if message_id and isinstance(channel, discord.TextChannel):\n                removed = await self._delete_stored_message(channel, message_id)\n            elif message_id and channel is None:\n                removed = True\n            if not removed:\n                continue\n            try:\n                await delete_live_card_state(guild_id, channel_id)\n            except ProfileStorageUnavailable:\n                return\n''',
)

replace_once(
    "stoney_verify/commands_ext/public_profile_cards.py",
    '''    updated = await upsert_guild_config(guild.id, {LIVE_ALLOWED_FIELDS_KEY: allowed})\n    await interaction.response.send_message(\n''',
    '''    updated = await upsert_guild_config(guild.id, {LIVE_ALLOWED_FIELDS_KEY: allowed})\n    runtime = _profile_runtime(interaction.client)\n    if runtime is not None:\n        await runtime.invalidate_guild_cards(guild)\n    await interaction.response.send_message(\n''',
)

print("Applied live profile cleanup hardening.")
