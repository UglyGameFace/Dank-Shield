from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def replace_once(path: str, old: str, new: str) -> None:
    target = ROOT / path
    source = target.read_text(encoding="utf-8")
    count = source.count(old)
    if count != 1:
        raise RuntimeError(f"expected one anchor in {path}: {old!r}; found {count}")
    target.write_text(source.replace(old, new, 1), encoding="utf-8")


# ---------------------------------------------------------------------------
# Service-layer display safety, malformed-port handling, and indexed user state.
# ---------------------------------------------------------------------------
replace_once(
    "stoney_verify/profile_card_service.py",
    '''_CACHE_TTL_SECONDS = 60.0\n''',
    '''_CACHE_TTL_SECONDS = 60.0\n_BIDI_CONTROL_RE = re.compile("[\\u061c\\u200e\\u200f\\u202a-\\u202e\\u2066-\\u2069]")\n''',
)
replace_once(
    "stoney_verify/profile_card_service.py",
    '''def clean_profile_username(value: Any) -> str:\n    text = str(value or "").replace("@everyone", "everyone").replace("@here", "here")\n    text = " ".join(text.replace("\\r", " ").replace("\\n", " ").split()).strip()\n''',
    '''def clean_profile_username(value: Any) -> str:\n    text = str(value or "").replace("@everyone", "everyone").replace("@here", "here")\n    text = _BIDI_CONTROL_RE.sub("", text)\n    text = " ".join(text.replace("\\r", " ").replace("\\n", " ").split()).strip()\n''',
)
replace_once(
    "stoney_verify/profile_card_service.py",
    '''    if "http://" in text.lower() or "https://" in text.lower() or "discord.gg" in text.lower():\n        raise InvalidPlatformProfile("Put the username in the username field, not a link.")\n    return text\n\n\ndef _normalized_path''',
    '''    lowered = text.lower()\n    if "://" in lowered or "discord.gg" in lowered:\n        raise InvalidPlatformProfile("Put the username in the username field, not a link.")\n    return text\n\n\ndef display_profile_username(value: Any) -> str:\n    """Return a Discord-safe username that cannot create markdown links."""\n    return clean_profile_username(value).replace("`", "ʼ")\n\n\ndef _normalized_path''',
)
replace_once(
    "stoney_verify/profile_card_service.py",
    '''    parsed = urlparse(raw)\n    if parsed.scheme.lower() != "https":\n        raise InvalidPlatformProfile("Profile links must use HTTPS.")\n    if parsed.username or parsed.password or parsed.port:\n        raise InvalidPlatformProfile("Profile links cannot contain credentials or custom ports.")\n''',
    '''    parsed = urlparse(raw)\n    if parsed.scheme.lower() != "https":\n        raise InvalidPlatformProfile("Profile links must use HTTPS.")\n    try:\n        parsed_port = parsed.port\n    except ValueError as exc:\n        raise InvalidPlatformProfile("Profile links cannot contain an invalid port.") from exc\n    if parsed.username or parsed.password or parsed_port is not None:\n        raise InvalidPlatformProfile("Profile links cannot contain credentials or custom ports.")\n''',
)
replace_once(
    "stoney_verify/profile_card_service.py",
    '''async def list_live_card_states() -> list[dict[str, Any]]:\n    def read(client: Any):\n        return client.table(LIVE_CARD_STATE_TABLE).select("*").execute()\n\n    return _rows(await _execute("list live profile states", read))\n\n\nasync def upsert_live_card_state''',
    '''async def list_live_card_states() -> list[dict[str, Any]]:\n    def read(client: Any):\n        return client.table(LIVE_CARD_STATE_TABLE).select("*").execute()\n\n    return _rows(await _execute("list live profile states", read))\n\n\nasync def list_live_card_states_for_user(\n    user_id: int,\n    *,\n    guild_id: Optional[int] = None,\n) -> list[dict[str, Any]]:\n    uid = int(user_id)\n    gid = int(guild_id) if guild_id is not None else None\n\n    def read(client: Any):\n        query = (\n            client.table(LIVE_CARD_STATE_TABLE)\n            .select("*")\n            .eq("user_id", str(uid))\n        )\n        if gid is not None:\n            query = query.eq("guild_id", str(gid))\n        return query.execute()\n\n    label = f"list live profile states for user {uid}"\n    if gid is not None:\n        label += f" in guild {gid}"\n    return _rows(await _execute(label, read))\n\n\nasync def upsert_live_card_state''',
)
replace_once(
    "stoney_verify/profile_card_service.py",
    '''    "clean_platform_key",\n    "effective_preferences",\n''',
    '''    "clean_platform_key",\n    "display_profile_username",\n    "effective_preferences",\n''',
)
replace_once(
    "stoney_verify/profile_card_service.py",
    '''    "list_live_card_states",\n    "normalize_platform_entry",\n''',
    '''    "list_live_card_states",\n    "list_live_card_states_for_user",\n    "normalize_platform_entry",\n''',
)

# ---------------------------------------------------------------------------
# Runtime anti-repetition, indexed cleanup, lifecycle cleanup, reconciliation.
# ---------------------------------------------------------------------------
replace_once(
    "stoney_verify/profile_card_runtime.py",
    '''from .guild_config import get_guild_config\n''',
    '''from .guild_config import get_guild_config, upsert_guild_config\n''',
)
replace_once(
    "stoney_verify/profile_card_runtime.py",
    '''    delete_live_card_state,\n    get_effective_profile_settings,\n''',
    '''    delete_live_card_state,\n    display_profile_username,\n    get_effective_profile_settings,\n''',
)
replace_once(
    "stoney_verify/profile_card_runtime.py",
    '''    get_live_card_state,\n    list_live_card_states,\n''',
    '''    get_live_card_state,\n    list_live_card_states,\n    list_live_card_states_for_user,\n''',
)
replace_once(
    "stoney_verify/profile_card_runtime.py",
    '''LIVE_DEBOUNCE_KEY = "profile_live_card_debounce_seconds"\nLIVE_SAME_SPEAKER_COOLDOWN_KEY = "profile_live_card_same_speaker_cooldown_seconds"\n\nDEFAULT_DEBOUNCE_SECONDS = 4.0\nDEFAULT_SAME_SPEAKER_COOLDOWN_SECONDS = 180.0\n''',
    '''LIVE_DEBOUNCE_KEY = "profile_live_card_debounce_seconds"\nLIVE_REPLACEMENT_COOLDOWN_KEY = "profile_live_card_replacement_cooldown_seconds"\nLIVE_SAME_SPEAKER_COOLDOWN_KEY = "profile_live_card_same_speaker_cooldown_seconds"\n\nDEFAULT_DEBOUNCE_SECONDS = 4.0\nDEFAULT_REPLACEMENT_COOLDOWN_SECONDS = 30.0\nDEFAULT_SAME_SPEAKER_COOLDOWN_SECONDS = 180.0\n''',
)
replace_once(
    "stoney_verify/profile_card_runtime.py",
    '''MAX_DEBOUNCE_SECONDS = 15.0\nMIN_SAME_SPEAKER_COOLDOWN_SECONDS = 30.0\n''',
    '''MAX_DEBOUNCE_SECONDS = 15.0\nMIN_REPLACEMENT_COOLDOWN_SECONDS = 15.0\nMAX_REPLACEMENT_COOLDOWN_SECONDS = 300.0\nMIN_SAME_SPEAKER_COOLDOWN_SECONDS = 30.0\n''',
)
replace_once(
    "stoney_verify/profile_card_runtime.py",
    '''    allowed_fields: frozenset[str]\n    debounce_seconds: float\n    same_speaker_cooldown_seconds: float\n''',
    '''    allowed_fields: frozenset[str]\n    debounce_seconds: float\n    replacement_cooldown_seconds: float\n    same_speaker_cooldown_seconds: float\n''',
)
replace_once(
    "stoney_verify/profile_card_runtime.py",
    '''    user_id: int\n    message_id: int\n''',
    '''    user_id: int\n    message_id: int\n    delay_seconds: float = 0.0\n''',
)
replace_once(
    "stoney_verify/profile_card_runtime.py",
    '''        debounce_seconds=_safe_float(\n            config.get(LIVE_DEBOUNCE_KEY),\n            DEFAULT_DEBOUNCE_SECONDS,\n            MIN_DEBOUNCE_SECONDS,\n            MAX_DEBOUNCE_SECONDS,\n        ),\n        same_speaker_cooldown_seconds=_safe_float(\n''',
    '''        debounce_seconds=_safe_float(\n            config.get(LIVE_DEBOUNCE_KEY),\n            DEFAULT_DEBOUNCE_SECONDS,\n            MIN_DEBOUNCE_SECONDS,\n            MAX_DEBOUNCE_SECONDS,\n        ),\n        replacement_cooldown_seconds=_safe_float(\n            config.get(LIVE_REPLACEMENT_COOLDOWN_KEY),\n            DEFAULT_REPLACEMENT_COOLDOWN_SECONDS,\n            MIN_REPLACEMENT_COOLDOWN_SECONDS,\n            MAX_REPLACEMENT_COOLDOWN_SECONDS,\n        ),\n        same_speaker_cooldown_seconds=_safe_float(\n''',
)
replace_once(
    "stoney_verify/profile_card_runtime.py",
    '''            lines.append(f"{spec.emoji} **{spec.label}:** {entry.get('username')}")\n''',
    '''            username = display_profile_username(entry.get("username"))\n            lines.append(f"{spec.emoji} **{spec.label}:** `{username}`")\n''',
)
replace_once(
    "stoney_verify/profile_card_runtime.py",
    '''        in_memory = self._last_posted.get(key)\n        if in_memory and in_memory[0] == user_id and monotonic() - in_memory[1] < config.same_speaker_cooldown_seconds:\n            return\n\n        trigger = PendingTrigger(\n            guild_id=key[0],\n            channel_id=key[1],\n            user_id=user_id,\n            message_id=int(message.id),\n        )\n''',
    '''        in_memory = self._last_posted.get(key)\n        delay_seconds = config.debounce_seconds\n        if in_memory:\n            age = monotonic() - in_memory[1]\n            if in_memory[0] == user_id and age < config.same_speaker_cooldown_seconds:\n                return\n            if age < config.replacement_cooldown_seconds:\n                delay_seconds = max(\n                    delay_seconds,\n                    config.replacement_cooldown_seconds - age,\n                )\n\n        trigger = PendingTrigger(\n            guild_id=key[0],\n            channel_id=key[1],\n            user_id=user_id,\n            message_id=int(message.id),\n            delay_seconds=delay_seconds,\n        )\n''',
)
replace_once(
    "stoney_verify/profile_card_runtime.py",
    '''    ) -> None:\n        await self.sleep(config.debounce_seconds)\n        key = (trigger.guild_id, trigger.channel_id)\n''',
    '''    ) -> None:\n        await self.sleep(max(config.debounce_seconds, trigger.delay_seconds))\n        key = (trigger.guild_id, trigger.channel_id)\n''',
)
replace_once(
    "stoney_verify/profile_card_runtime.py",
    '''        state = await get_live_card_state(trigger.guild_id, trigger.channel_id)\n        if state and str(state.get("user_id") or "") == str(trigger.user_id):\n            age = _state_age_seconds(state)\n            if age is not None and age < config.same_speaker_cooldown_seconds:\n                self._last_posted[(trigger.guild_id, trigger.channel_id)] = (trigger.user_id, monotonic())\n                return\n\n        rendered = await self.renderer(\n''',
    '''        state = await get_live_card_state(trigger.guild_id, trigger.channel_id)\n        age = _state_age_seconds(state)\n        if state and str(state.get("user_id") or "") == str(trigger.user_id):\n            if age is not None and age < config.same_speaker_cooldown_seconds:\n                self._last_posted[(trigger.guild_id, trigger.channel_id)] = (trigger.user_id, monotonic())\n                return\n        elif state and age is not None and age < config.replacement_cooldown_seconds:\n            await self.sleep(config.replacement_cooldown_seconds - age)\n            key = (trigger.guild_id, trigger.channel_id)\n            if self._latest.get(key) != trigger:\n                return\n\n        rendered = await self.renderer(\n''',
)
replace_once(
    "stoney_verify/profile_card_runtime.py",
    '''        try:\n            states = await list_live_card_states()\n        except ProfileStorageUnavailable:\n            return\n''',
    '''        try:\n            states = await list_live_card_states_for_user(\n                resolved_user_id,\n                guild_id=resolved_guild_id,\n            )\n        except ProfileStorageUnavailable:\n            return\n''',
)
replace_once(
    "stoney_verify/profile_card_runtime.py",
    '''    async def disable_channel(self, guild: discord.Guild, channel: discord.TextChannel) -> None:\n        await self._remove_channel_card_state(guild, channel.id)\n\n    async def reconcile(self) -> None:\n''',
    '''    async def disable_channel(self, guild: discord.Guild, channel: discord.TextChannel) -> None:\n        await self._remove_channel_card_state(guild, channel.id)\n\n    async def on_member_remove(self, member: discord.Member) -> None:\n        await self.remove_user_cards(member.guild, member.id)\n\n    async def on_guild_channel_delete(self, channel: discord.abc.GuildChannel) -> None:\n        if not isinstance(channel, discord.TextChannel):\n            return\n        try:\n            config = await get_guild_config(channel.guild.id, refresh=True)\n        except Exception:\n            return\n        channel_ids = set(_channel_ids(config.get(LIVE_CHANNEL_IDS_KEY)))\n        if channel.id not in channel_ids:\n            return\n        channel_ids.discard(channel.id)\n        try:\n            await upsert_guild_config(\n                channel.guild.id,\n                {\n                    LIVE_ENABLED_KEY: bool(channel_ids),\n                    LIVE_CHANNEL_IDS_KEY: [str(value) for value in sorted(channel_ids)],\n                },\n            )\n        except Exception:\n            return\n        await self._remove_channel_card_state(channel.guild, channel.id)\n\n    async def reconcile(self) -> None:\n''',
)
replace_once(
    "stoney_verify/profile_card_runtime.py",
    '''        owned: list[discord.Message] = []\n        try:\n            async for message in channel.history(limit=LIVE_CARD_HISTORY_SCAN_LIMIT):\n                if int(getattr(message.author, "id", 0) or 0) != int(bot_user.id):\n                    continue\n                if parse_live_card_footer(message) is not None:\n                    owned.append(message)\n        except Exception:\n            return\n''',
    '''        owned_by_id: dict[int, discord.Message] = {}\n        if state:\n            try:\n                stored_message_id = int(str(state.get("message_id") or "0"))\n            except Exception:\n                stored_message_id = 0\n            if stored_message_id:\n                try:\n                    stored_message = await channel.fetch_message(stored_message_id)\n                except discord.NotFound:\n                    stored_message = None\n                except Exception:\n                    return\n                if (\n                    stored_message is not None\n                    and int(getattr(stored_message.author, "id", 0) or 0) == int(bot_user.id)\n                    and parse_live_card_footer(stored_message) is not None\n                ):\n                    owned_by_id[int(stored_message.id)] = stored_message\n\n        try:\n            async for message in channel.history(limit=LIVE_CARD_HISTORY_SCAN_LIMIT):\n                if int(getattr(message.author, "id", 0) or 0) != int(bot_user.id):\n                    continue\n                if parse_live_card_footer(message) is not None:\n                    owned_by_id[int(message.id)] = message\n        except Exception:\n            if not owned_by_id:\n                return\n        owned = list(owned_by_id.values())\n''',
)
replace_once(
    "stoney_verify/profile_card_runtime.py",
    '''    "DEFAULT_DEBOUNCE_SECONDS",\n    "DEFAULT_SAME_SPEAKER_COOLDOWN_SECONDS",\n''',
    '''    "DEFAULT_DEBOUNCE_SECONDS",\n    "DEFAULT_REPLACEMENT_COOLDOWN_SECONDS",\n    "DEFAULT_SAME_SPEAKER_COOLDOWN_SECONDS",\n''',
)
replace_once(
    "stoney_verify/profile_card_runtime.py",
    '''    "LIVE_ENABLED_KEY",\n    "LIVE_SAME_SPEAKER_COOLDOWN_KEY",\n''',
    '''    "LIVE_ENABLED_KEY",\n    "LIVE_REPLACEMENT_COOLDOWN_KEY",\n    "LIVE_SAME_SPEAKER_COOLDOWN_KEY",\n''',
)

# ---------------------------------------------------------------------------
# Commands render usernames as non-clickable inline code and register cleanup.
# ---------------------------------------------------------------------------
replace_once(
    "stoney_verify/commands_ext/public_profile_cards.py",
    '''    effective_preferences,\n    get_effective_profile_settings,\n''',
    '''    display_profile_username,\n    effective_preferences,\n    get_effective_profile_settings,\n''',
)
replace_once(
    "stoney_verify/commands_ext/public_profile_cards.py",
    '''        identity_lines.append(f"{spec.emoji} **{spec.label}:** {username} — {visibility}{link_state}")\n''',
    '''        safe_username = display_profile_username(username)\n        identity_lines.append(\n            f"{spec.emoji} **{spec.label}:** `{safe_username}` — {visibility}{link_state}"\n        )\n''',
)
replace_once(
    "stoney_verify/commands_ext/public_profile_cards.py",
    '''            "One bot-owned card follows the latest eligible human speaker in each enabled channel. "\n            "Bursts are debounced and the same speaker is cooldown-suppressed."\n''',
    '''            "One bot-owned card follows the latest eligible human speaker in each enabled channel. "\n            f"Bursts are debounced, replacements are limited to about {int(live.replacement_cooldown_seconds)} seconds, "\n            "and the same speaker is cooldown-suppressed."\n''',
)
replace_once(
    "stoney_verify/commands_ext/public_profile_cards.py",
    '''        bot.add_listener(runtime.on_message, "on_message")\n        bot.add_listener(runtime.on_ready, "on_ready")\n''',
    '''        bot.add_listener(runtime.on_message, "on_message")\n        bot.add_listener(runtime.on_ready, "on_ready")\n        bot.add_listener(runtime.on_member_remove, "on_member_remove")\n        bot.add_listener(runtime.on_guild_channel_delete, "on_guild_channel_delete")\n''',
)

# ---------------------------------------------------------------------------
# Persistence index for user-specific cleanup.
# ---------------------------------------------------------------------------
replace_once(
    "supabase/migrations/20260725_live_profile_cards.sql",
    '''create index if not exists idx_dank_live_profile_cards_guild\n    on public.dank_live_profile_cards (guild_id, channel_id);\n''',
    '''create index if not exists idx_dank_live_profile_cards_guild\n    on public.dank_live_profile_cards (guild_id, channel_id);\n\ncreate index if not exists idx_dank_live_profile_cards_user\n    on public.dank_live_profile_cards (user_id, guild_id, channel_id);\n''',
)

print("Applied final live profile anti-spam and safety hardening.")
