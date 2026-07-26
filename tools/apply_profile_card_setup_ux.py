from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def replace_once(path: str, old: str, new: str) -> None:
    target = ROOT / path
    source = target.read_text(encoding="utf-8")
    count = source.count(old)
    if count != 1:
        raise RuntimeError(f"expected one anchor in {path}: {old!r}; found {count}")
    target.write_text(source.replace(old, new, 1), encoding="utf-8")


# Put the live-card picker inside the canonical /dank setup navigation.
replace_once(
    "stoney_verify/commands_ext/public_setup_recommend.py",
    '''    @discord.ui.button(
        label="Backups & History",
        emoji="💾",
        style=discord.ButtonStyle.secondary,
        custom_id="dank_setup_features:history",
        row=3,
    )
''',
    '''    @discord.ui.button(
        label="Member Profiles & Live Cards",
        emoji="🪪",
        style=discord.ButtonStyle.secondary,
        custom_id="dank_setup_features:profiles",
        row=3,
    )
    async def profiles(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        _ = button
        from stoney_verify import profile_card_setup_ui

        await profile_card_setup_ui.open_profile_card_setup(interaction)

    @discord.ui.button(
        label="Backups & History",
        emoji="💾",
        style=discord.ButtonStyle.secondary,
        custom_id="dank_setup_features:history",
        row=3,
    )
''',
)

# Keep direct slash commands as fallbacks, while pointing ordinary managers to setup.
replace_once(
    "stoney_verify/commands_ext/public_profile_cards.py",
    '    embed.set_footer(text="Use /dank profile live-cards with a Discord channel picker")\n',
    '    embed.set_footer(text="/dank setup → All Features & Settings → Member Profiles & Live Cards")\n',
)
replace_once(
    "stoney_verify/commands_ext/public_profile_cards.py",
    '        ("live-cards", "Manager: enable or disable live cards in a selected channel.", profile_live_cards),\n',
    '        ("live-cards", "Manager fallback: toggle one channel; the full picker is in /dank setup.", profile_live_cards),\n',
)

# Separate static welcome/start-here and join-only cards from join/leave announcements.
replace_once(
    "stoney_verify/commands_ext/public_welcome_group.py",
    '    description="Set up welcome, join, and leave messages for this server.",\n',
    '    description="Set up the static welcome/start-here message and join-only image cards.",\n',
)
replace_once(
    "stoney_verify/commands_ext/public_welcome_group.py",
    '    await interaction.followup.send("Preview only. Press **Post/Update** in `/dank setup` → Feature Centers → Welcome Center when ready.", embed=embed, ephemeral=True)\n',
    '    await interaction.followup.send("Preview only. Use `/dank welcome post` when the static welcome/start-here message is ready.", embed=embed, ephemeral=True)\n',
)
replace_once(
    "stoney_verify/commands_ext/public_welcome_group.py",
    '    await interaction.followup.send(f"✅ Welcome channel saved as {channel.mention}. Press **Post/Update** in Welcome Center to post/update the message.", ephemeral=True)\n',
    '    await interaction.followup.send(f"✅ Static welcome/start-here channel saved as {channel.mention}. Use `/dank welcome post` to post or update it.", ephemeral=True)\n',
)
replace_once(
    "stoney_verify/commands_ext/public_welcome_group.py",
    '    await interaction.followup.send("✅ Welcome template saved. Preview below. Press **Post/Update** in Welcome Center to update the public message.", embed=embed, ephemeral=True)\n',
    '    await interaction.followup.send("✅ Static welcome/start-here template saved. Preview below. Use `/dank welcome post` to update the public message.", embed=embed, ephemeral=True)\n',
)
replace_once(
    "stoney_verify/commands_ext/public_welcome_group.py",
    '    await interaction.followup.send("✅ Welcome template reset to the default. Press **Post/Update** in Welcome Center to update the public message.", ephemeral=True)\n',
    '    await interaction.followup.send("✅ Static welcome/start-here template reset. Use `/dank welcome post` to update the public message.", ephemeral=True)\n',
)
replace_once(
    "stoney_verify/commands_ext/public_welcome_group.py",
    '''async def open_welcome_events(interaction: discord.Interaction) -> None:
    if not await _require_setup_permission(interaction):
        return
    try:
        from stoney_verify import welcome_event_services

        await welcome_event_services.open_welcome_events_center(interaction)
    except Exception as exc:
        await _send(interaction, f"❌ Could not open join/leave setup: `{type(exc).__name__}: {exc}`")


''',
    '''async def open_join_leave_announcements(interaction: discord.Interaction) -> None:
    if not await _require_setup_permission(interaction):
        return
    try:
        from stoney_verify import welcome_event_services

        await welcome_event_services.open_welcome_events_center(interaction)
    except Exception as exc:
        await _send(interaction, f"❌ Could not open Join & Leave Announcements: `{type(exc).__name__}: {exc}`")


# Compatibility export for older internal imports. The public command is join-leave.
open_welcome_events = open_join_leave_announcements


''',
)
replace_once(
    "stoney_verify/commands_ext/public_welcome_group.py",
    '''    lines.append("")
    lines.extend(_event_channel_health(guild, cfg, kind="join"))
    lines.extend(_event_channel_health(guild, cfg, kind="leave"))
''',
    '''    lines.append("")
    lines.append("ℹ️ Join and leave announcements are configured separately with `/dank welcome join-leave`.")
''',
)
replace_once(
    "stoney_verify/commands_ext/public_welcome_group.py",
    '''@welcome_group.command(name="events", description="Set up optional join and leave announcements for this server.")
async def welcome_events(interaction: discord.Interaction) -> None:
    await open_welcome_events(interaction)
''',
    '''@welcome_group.command(name="join-leave", description="Open the separate Join & Leave Announcements setup.")
async def welcome_join_leave(interaction: discord.Interaction) -> None:
    await open_join_leave_announcements(interaction)
''',
)
replace_once(
    "stoney_verify/commands_ext/public_welcome_group.py",
    '@welcome_group.command(name="health", description="Check whether welcome, join, and leave message setup is ready.")\n',
    '@welcome_group.command(name="health", description="Check the static welcome/start-here message and join-only image card setup.")\n',
)
replace_once(
    "stoney_verify/commands_ext/public_welcome_group.py",
    '    "open_welcome_events",\n',
    '    "open_join_leave_announcements",\n    "open_welcome_events",\n',
)

# Make the join/leave center name and copy honest everywhere it appears.
replace_once(
    "stoney_verify/welcome_event_services.py",
    '"""Owned setup service for optional join and leave messages."""\n',
    '"""Owned setup service for separate join and leave announcements."""\n',
)
replace_once(
    "stoney_verify/welcome_event_services.py",
    '''        title="👋 Welcome & Join/Leave Center",
        description=(
            "Separate the **member-facing join welcome** from the **private staff join/leave log**.\n"
            "Join channel selection is exact: if the selected Join channel is private, join welcomes pause instead of posting somewhere else. Keep staff join/leave logs separate."
        ),
''',
    '''        title="👋 Join & Leave Announcements",
        description=(
            "These messages fire only when a member joins or leaves. They are separate from the "
            "**static welcome/start-here message**, the **join-only welcome image card**, and "
            "**live profile cards** that follow conversation.\n\n"
            "Join channel selection is exact: if the selected Join channel is private, join announcements pause instead of posting somewhere else. Keep the staff leave log separate."
        ),
''',
)
replace_once(
    "stoney_verify/welcome_event_services.py",
    '        name="Member-Facing Join Welcome",\n',
    '        name="Member-Facing Join Announcement",\n',
)
replace_once(
    "stoney_verify/welcome_event_services.py",
    '    embed.set_footer(text="/dank setup • Welcome Center • changes update this panel live")\n',
    '    embed.set_footer(text="/dank welcome join-leave • separate event announcements")\n',
)
replace_once(
    "stoney_verify/welcome_event_services.py",
    '    await _refresh_center(interaction, last_action=f"✅ Applied **{actual.title()}** welcome template preset. Channels and ON/OFF settings were not changed.")\n',
    '    await _refresh_center(interaction, last_action=f"✅ Applied **{actual.title()}** join/leave announcement preset. Channels and ON/OFF settings were not changed.")\n',
)
replace_once(
    "stoney_verify/welcome_event_services.py",
    '            title="Welcome Placeholder Help",\n',
    '            title="Join & Leave Placeholder Help",\n',
)
replace_once(
    "stoney_verify/welcome_event_services.py",
    '            await interaction.response.edit_message(content="Closed Welcome Center. Reopen it from `/dank setup`.", view=self.view)\n',
    '            await interaction.response.edit_message(content="Closed Join & Leave Announcements. Reopen it with `/dank welcome join-leave`.", view=self.view)\n',
)
replace_once(
    "stoney_verify/welcome_event_services.py",
    '            await _send_ephemeral(interaction, "Closed Welcome Center.")\n',
    '            await _send_ephemeral(interaction, "Closed Join & Leave Announcements.")\n',
)
