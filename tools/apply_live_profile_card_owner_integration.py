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
# Runtime privacy filtering and immediate stale-card invalidation.
# ---------------------------------------------------------------------------
replace_once(
    "stoney_verify/profile_card_runtime.py",
    '''    role_field_names = {"🪪 Pronouns", "🌈 Identity", "🎮 Interests", "Profile roles", "Pages"}\n    date_field_names = {"Joined server", "Account created"}\n    for field in list(base.fields):\n        if field.name in role_field_names and not show_roles:\n            continue\n        if field.name in date_field_names and not show_dates:\n            continue\n        embed.add_field(name=field.name, value=field.value, inline=field.inline)\n''',
    '''    role_field_names = {"🪪 Pronouns", "🌈 Identity", "🎮 Interests", "Profile roles"}\n    date_field_names = {"Joined server", "Account created"}\n    for field in list(base.fields):\n        field_name = str(field.name or "")\n        # Live/public cards are compact. Never show a dead pagination counter,\n        # and remove both the base and dynamic paginated role fields when the\n        # member hides roles.\n        if field_name == "Pages":\n            continue\n        if not show_roles and (\n            field_name in role_field_names\n            or field_name.startswith("Profile roles ")\n        ):\n            continue\n        if field_name in date_field_names and not show_dates:\n            continue\n        embed.add_field(name=field.name, value=field.value, inline=field.inline)\n''',
)

replace_once(
    "stoney_verify/profile_card_runtime.py",
    '''    async def disable_channel(self, guild: discord.Guild, channel: discord.TextChannel) -> None:\n''',
    '''    async def remove_user_cards(self, guild: discord.Guild, user_id: int) -> None:\n        """Remove only this member's owned live cards in one guild.\n\n        Privacy changes use this immediately so an already-posted card cannot\n        keep displaying data the member just hid. Failed Discord deletions keep\n        their durable state so reconciliation can retry later.\n        """\n        resolved_user_id = int(user_id)\n        try:\n            config = parse_live_card_config(await get_guild_config(guild.id))\n        except Exception:\n            return\n\n        for channel_id in config.channel_ids:\n            key = (int(guild.id), int(channel_id))\n            latest = self._latest.get(key)\n            if latest is not None and latest.user_id == resolved_user_id:\n                pending = self._pending.pop(key, None)\n                self._latest.pop(key, None)\n                if pending is not None and not pending.done():\n                    pending.cancel()\n\n            try:\n                state = await get_live_card_state(*key)\n            except ProfileStorageUnavailable:\n                return\n            if not state or str(state.get("user_id") or "") != str(resolved_user_id):\n                continue\n\n            channel = guild.get_channel(channel_id)\n            try:\n                message_id = int(str(state.get("message_id") or "0"))\n            except Exception:\n                message_id = 0\n            removed = not message_id\n            if isinstance(channel, discord.TextChannel) and message_id:\n                removed = await self._delete_stored_message(channel, message_id)\n            if removed:\n                try:\n                    await delete_live_card_state(*key)\n                except ProfileStorageUnavailable:\n                    return\n            self._last_posted.pop(key, None)\n\n    async def remove_user_cards_all_guilds(self, user_id: int) -> None:\n        for guild in list(getattr(self.bot, "guilds", []) or []):\n            try:\n                await self.remove_user_cards(guild, int(user_id))\n            except Exception:\n                continue\n\n    async def disable_channel(self, guild: discord.Guild, channel: discord.TextChannel) -> None:\n''',
)

# ---------------------------------------------------------------------------
# Privacy-aware public card view and immediate invalidation after changes.
# ---------------------------------------------------------------------------
replace_once(
    "stoney_verify/commands_ext/public_profile_cards.py",
    '''    LiveProfileCardRuntime,\n    parse_live_card_config,\n    render_live_profile_card,\n)\n''',
    '''    LiveProfileCardRuntime,\n    _copy_base_profile_embed,\n    parse_live_card_config,\n    render_live_profile_card,\n)\n''',
)

replace_once(
    "stoney_verify/commands_ext/public_profile_cards.py",
    '''async def _settings_payload(guild_id: int, user_id: int) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:\n''',
    '''def _profile_runtime(client: Any) -> Optional[LiveProfileCardRuntime]:\n    runtime = getattr(client, _RUNTIME_ATTRIBUTE, None)\n    return runtime if isinstance(runtime, LiveProfileCardRuntime) else None\n\n\nasync def invalidate_member_live_cards(\n    client: Any,\n    guild: discord.Guild,\n    user_id: int,\n    *,\n    all_guilds: bool = False,\n) -> None:\n    runtime = _profile_runtime(client)\n    if runtime is None:\n        return\n    if all_guilds:\n        await runtime.remove_user_cards_all_guilds(int(user_id))\n    else:\n        await runtime.remove_user_cards(guild, int(user_id))\n\n\nasync def _settings_payload(guild_id: int, user_id: int) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:\n''',
)

replace_once(
    "stoney_verify/commands_ext/public_profile_cards.py",
    '''            await upsert_profile_guild_settings(\n                view.guild_id,\n                view.author_id,\n                {self.preference_key: not current},\n            )\n            await view.refresh(interaction)\n''',
    '''            await upsert_profile_guild_settings(\n                view.guild_id,\n                view.author_id,\n                {self.preference_key: not current},\n            )\n            if interaction.guild is not None:\n                await invalidate_member_live_cards(\n                    interaction.client,\n                    interaction.guild,\n                    view.author_id,\n                )\n            await view.refresh(interaction)\n''',
)

replace_once(
    "stoney_verify/commands_ext/public_profile_cards.py",
    '''    spec = PLATFORM_SPECS[entry["platform"]]\n    visibility = "shared on cards" if entry["shared"] else "saved privately"\n''',
    '''    await invalidate_member_live_cards(\n        interaction.client,\n        interaction.guild,\n        interaction.user.id,\n        all_guilds=True,\n    )\n    spec = PLATFORM_SPECS[entry["platform"]]\n    visibility = "shared on cards" if entry["shared"] else "saved privately"\n''',
)

replace_once(
    "stoney_verify/commands_ext/public_profile_cards.py",
    '''    spec = PLATFORM_SPECS[platform.value]\n    await _safe_ephemeral(\n''',
    '''    await invalidate_member_live_cards(\n        interaction.client,\n        interaction.guild,\n        interaction.user.id,\n        all_guilds=True,\n    )\n    spec = PLATFORM_SPECS[platform.value]\n    await _safe_ephemeral(\n''',
)

replace_once(
    "stoney_verify/commands_ext/public_profile_cards.py",
    '''def _live_status_embed(guild: discord.Guild, config: Mapping[str, Any]) -> discord.Embed:\n''',
    '''class _PublicFullRolesButton(discord.ui.Button):\n    def __init__(self, member_id: int) -> None:\n        super().__init__(\n            label="View Full Profile Roles",\n            emoji="📋",\n            style=discord.ButtonStyle.secondary,\n            custom_id=f"dank:profilecard:v1:full_roles:{int(member_id)}",\n            row=4,\n        )\n        self.member_id = int(member_id)\n\n    async def callback(self, interaction: discord.Interaction) -> None:\n        guild = interaction.guild\n        if guild is None:\n            return await _safe_ephemeral(interaction, "This only works inside the server.", ok=False)\n        member = guild.get_member(self.member_id)\n        if not isinstance(member, discord.Member):\n            return await _safe_ephemeral(interaction, "That member is no longer available.", ok=False)\n        try:\n            config = parse_live_card_config(await get_guild_config(guild.id))\n            effective = await get_effective_profile_settings(guild.id, member.id)\n        except ProfileStorageUnavailable:\n            return await _safe_ephemeral(interaction, "Private profile storage is unavailable.", ok=False)\n        preferences = dict(effective.get("preferences") or {})\n        if not bool(preferences.get("show_roles", True)) or "roles" not in config.allowed_fields:\n            return await _safe_ephemeral(interaction, "This member has hidden their profile roles.", ok=False)\n        from .public_self_roles_group import _profile_full_roles_embed\n\n        await interaction.response.send_message(\n            embed=_profile_full_roles_embed(member),\n            ephemeral=True,\n            allowed_mentions=discord.AllowedMentions.none(),\n        )\n\n\nclass _PublicProfileSettingsButton(discord.ui.Button):\n    def __init__(self) -> None:\n        super().__init__(\n            label="Privacy & Platforms",\n            emoji="🔐",\n            style=discord.ButtonStyle.primary,\n            custom_id="dank:profilecard:v1:open_settings",\n            row=4,\n        )\n\n    async def callback(self, interaction: discord.Interaction) -> None:\n        await profile_settings(interaction)\n\n\nclass PublicProfileView(discord.ui.View):\n    def __init__(\n        self,\n        *,\n        member_id: int,\n        source_view: Optional[discord.ui.View],\n        show_roles: bool,\n        show_settings: bool,\n    ) -> None:\n        super().__init__(timeout=300)\n        for child in list(getattr(source_view, "children", []) or []):\n            if not isinstance(child, discord.ui.Button) or not child.url:\n                continue\n            self.add_item(\n                discord.ui.Button(\n                    label=str(child.label or "Profile")[:80],\n                    emoji=child.emoji,\n                    style=discord.ButtonStyle.link,\n                    url=str(child.url),\n                    row=child.row,\n                )\n            )\n        if show_roles:\n            self.add_item(_PublicFullRolesButton(member_id))\n        if show_settings:\n            self.add_item(_PublicProfileSettingsButton())\n\n\nasync def send_privacy_aware_profile(\n    interaction: discord.Interaction,\n    member: discord.Member,\n) -> None:\n    guild = interaction.guild\n    if guild is None or int(member.guild.id) != int(guild.id):\n        return await _safe_ephemeral(interaction, "That member is not available in this server.", ok=False)\n    try:\n        config = parse_live_card_config(await get_guild_config(guild.id))\n        effective = await get_effective_profile_settings(guild.id, member.id)\n        rendered = await render_live_profile_card(\n            member,\n            set(config.allowed_fields),\n            trigger_message_id=0,\n            require_live_enabled=False,\n        )\n    except ProfileStorageUnavailable:\n        return await _safe_ephemeral(\n            interaction,\n            "Private profile storage is unavailable. Dank Shield will not guess this member's privacy settings.",\n            ok=False,\n        )\n\n    preferences = dict(effective.get("preferences") or {})\n    show_roles = bool(preferences.get("show_roles", True)) and "roles" in config.allowed_fields\n    if rendered is None:\n        from .public_self_roles_group import _profile_card\n\n        rendered_embed = _copy_base_profile_embed(\n            _profile_card(member),\n            show_roles=False,\n            show_dates=False,\n        )\n        rendered_embed.description = "This member has hidden their optional profile details."\n        rendered_view = None\n    else:\n        rendered_embed = rendered.embed\n        rendered_view = rendered.view\n        rendered_embed.description = "Member profile • only fields this member chose to share"\n    rendered_embed.set_footer(text="Dank Shield member profile")\n    view = PublicProfileView(\n        member_id=member.id,\n        source_view=rendered_view,\n        show_roles=show_roles,\n        show_settings=int(interaction.user.id) == int(member.id),\n    )\n    await interaction.response.send_message(\n        embed=rendered_embed,\n        view=view if view.children else None,\n        ephemeral=True,\n        allowed_mentions=discord.AllowedMentions.none(),\n    )\n\n\ndef _live_status_embed(guild: discord.Guild, config: Mapping[str, Any]) -> discord.Embed:\n''',
)

replace_once(
    "stoney_verify/commands_ext/public_profile_cards.py",
    '''    "profile_settings",\n    "register_public_profile_cards",\n]\n''',
    '''    "profile_settings",\n    "invalidate_member_live_cards",\n    "send_privacy_aware_profile",\n    "register_public_profile_cards",\n]\n''',
)

# ---------------------------------------------------------------------------
# Existing /dank profile owner paths now call the privacy-aware composer.
# ---------------------------------------------------------------------------
replace_once(
    "stoney_verify/commands_ext/public_self_roles_group.py",
    '''        self.add_item(discord.ui.Button(label="Edit Interests", emoji="🎮", style=discord.ButtonStyle.secondary, custom_id=f"{PROFILE_PREFIX}open:interests", row=1))\n        self.add_item(discord.ui.Button(label="Server Roles / Cosmetics", emoji="🎭", style=discord.ButtonStyle.secondary, custom_id=f"{PROFILE_PREFIX}cosmetics", row=2))\n''',
    '''        self.add_item(discord.ui.Button(label="Edit Interests", emoji="🎮", style=discord.ButtonStyle.secondary, custom_id=f"{PROFILE_PREFIX}open:interests", row=1))\n        self.add_item(discord.ui.Button(label="Privacy & Platforms", emoji="🔐", style=discord.ButtonStyle.primary, custom_id=f"{PROFILE_PREFIX}privacy", row=1))\n        self.add_item(discord.ui.Button(label="Server Roles / Cosmetics", emoji="🎭", style=discord.ButtonStyle.secondary, custom_id=f"{PROFILE_PREFIX}cosmetics", row=2))\n''',
)

replace_once(
    "stoney_verify/commands_ext/public_self_roles_group.py",
    '''        self.add_item(discord.ui.Button(label="Clear Profile Roles", emoji="🧹", style=discord.ButtonStyle.danger, custom_id=f"{PROFILE_PREFIX}clear", row=1))\n        self.add_item(discord.ui.Button(label="Missing Identity?", emoji="✍️", style=discord.ButtonStyle.secondary, custom_id=f"{PROFILE_PREFIX}missing", row=2))\n''',
    '''        self.add_item(discord.ui.Button(label="Clear Profile Roles", emoji="🧹", style=discord.ButtonStyle.danger, custom_id=f"{PROFILE_PREFIX}clear", row=1))\n        self.add_item(discord.ui.Button(label="Privacy & Platforms", emoji="🔐", style=discord.ButtonStyle.primary, custom_id=f"{PROFILE_PREFIX}privacy", row=1))\n        self.add_item(discord.ui.Button(label="Missing Identity?", emoji="✍️", style=discord.ButtonStyle.secondary, custom_id=f"{PROFILE_PREFIX}missing", row=2))\n''',
)

replace_once(
    "stoney_verify/commands_ext/public_self_roles_group.py",
    '''    if suffix == "view":\n        await interaction.response.send_message(\n            embed=_profile_card(member),\n            view=_profile_card_view(member),\n            ephemeral=True,\n            allowed_mentions=discord.AllowedMentions.none(),\n        )\n        return True\n''',
    '''    if suffix == "privacy":\n        from .public_profile_cards import profile_settings\n\n        await profile_settings(interaction)\n        return True\n\n    if suffix == "view":\n        from .public_profile_cards import send_privacy_aware_profile\n\n        await send_privacy_aware_profile(interaction, member)\n        return True\n''',
)

replace_once(
    "stoney_verify/commands_ext/public_self_roles_group.py",
    '''        await interaction.response.send_message(\n            embed=_profile_card(member),\n            view=_profile_card_view_with_actions(member),\n            ephemeral=True,\n            allowed_mentions=discord.AllowedMentions.none(),\n        )\n''',
    '''        from .public_profile_cards import send_privacy_aware_profile\n\n        await send_privacy_aware_profile(interaction, member)\n''',
)

replace_once(
    "stoney_verify/commands_ext/public_self_roles_group.py",
    '''    await interaction.response.send_message(\n        embed=_profile_card(target),\n        ephemeral=True,\n        allowed_mentions=discord.AllowedMentions.none(),\n    )\n''',
    '''    from .public_profile_cards import send_privacy_aware_profile\n\n    await send_privacy_aware_profile(interaction, target)\n''',
)

replace_once(
    "stoney_verify/commands_ext/public_self_roles_group.py",
    '''    await interaction.response.send_message(\n        embed=_profile_card(target),\n            view=_profile_card_view(target),\n            ephemeral=True,\n        allowed_mentions=discord.AllowedMentions.none(),\n    )\n''',
    '''    from .public_profile_cards import send_privacy_aware_profile\n\n    await send_privacy_aware_profile(interaction, target)\n''',
)

replace_once(
    "stoney_verify/commands_ext/public_self_roles_group.py",
    '''        await _reply(interaction, "\\n".join(changes) if changes else "No profile changes needed.", ok=True)\n        return True\n''',
    '''        if changes:\n            from .public_profile_cards import invalidate_member_live_cards\n\n            await invalidate_member_live_cards(interaction.client, guild, member.id)\n        await _reply(interaction, "\\n".join(changes) if changes else "No profile changes needed.", ok=True)\n        return True\n''',
)

replace_once(
    "stoney_verify/commands_ext/public_self_roles_group.py",
    '''                await member.remove_roles(*roles, reason="Dank Shield profile clear")\n                await _reply(interaction, "Removed your optional profile roles.", ok=True)\n''',
    '''                await member.remove_roles(*roles, reason="Dank Shield profile clear")\n                from .public_profile_cards import invalidate_member_live_cards\n\n                await invalidate_member_live_cards(interaction.client, guild, member.id)\n                await _reply(interaction, "Removed your optional profile roles.", ok=True)\n''',
)

replace_once(
    "stoney_verify/commands_ext/public_self_roles_group.py",
    '''        if role in interaction.user.roles:\n            await interaction.user.remove_roles(role, reason="Dank Shield advanced self-role toggle")\n            await _reply(interaction, f"Removed {role.mention}.", ok=True)\n        else:\n            await interaction.user.add_roles(role, reason="Dank Shield advanced self-role toggle")\n            await _reply(interaction, f"Added {role.mention}.", ok=True)\n        return True\n''',
    '''        if role in interaction.user.roles:\n            await interaction.user.remove_roles(role, reason="Dank Shield advanced self-role toggle")\n            result = f"Removed {role.mention}."\n        else:\n            await interaction.user.add_roles(role, reason="Dank Shield advanced self-role toggle")\n            result = f"Added {role.mention}."\n        from .public_profile_cards import invalidate_member_live_cards\n\n        await invalidate_member_live_cards(\n            interaction.client,\n            interaction.guild,\n            interaction.user.id,\n        )\n        await _reply(interaction, result, ok=True)\n        return True\n''',
)

print("Applied native live profile card owner integration.")
