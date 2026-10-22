from __future__ import annotations

"""It's 4:20 Somewhere: one navigable community feature, not a second /toke."""

from datetime import datetime, timezone
from typing import Any, Mapping

import discord

from stoney_verify.somewhere_420_clock import live_cities, next_wave, world_420
from .public_owner_authority import interaction_has_manage_guild_authority


SHARE_STATE_KEY = "somewhere_420_share_v1"
SHARE_COOLDOWN_SECONDS = 60 * 60


def build_420_embed(*, now: datetime | None = None, hour: int = 16, public: bool = False) -> discord.Embed:
    current = now or datetime.now(timezone.utc)
    places = world_420(current, hour=hour)
    live = live_cities(places)
    label = "AM" if hour == 4 else "PM"
    embed = discord.Embed(
        title="🌍💨 It's 4:20 Somewhere",
        description=(
            "**A little worldwide sesh energy.** The planet keeps turning, "
            "and the next local 4:20 is never far away.\n\n"
            f"🕓 **Tracking 4:20 {label}** in real IANA timezones."
        ),
        color=discord.Color.green(),
        timestamp=current,
    )
    embed.add_field(
        name="🔥 4:20 right now" if live else "🌙 Between 4:20s",
        value=(
            "**" + " · ".join(live[:8]) + "**"
            + (f" and {len(live) - 8} more" if len(live) > 8 else "")
            if live else "No tracked city is at 4:20 in this minute. The next wave is below."
        ),
        inline=False,
    )
    waves = next_wave(places)
    embed.add_field(
        name="🌐 Next waves around the world",
        value=(
            "\n".join(
                f"**<t:{int(moment.timestamp())}:R>** · "
                f"<t:{int(moment.timestamp())}:t> your time\n"
                + " · ".join(cities[:4])
                + (f" +{len(cities) - 4}" if len(cities) > 4 else "")
                for moment, cities in waves
            )
            or "Time zone data is unavailable on this host."
        )[:1024],
        inline=False,
    )
    embed.add_field(
        name="💨 Take it into /toke",
        value=(
            "Run `/toke` or open **/dank home → Community & Engagement → "
            "It's 4:20 Somewhere** for the interactive **Start /toke** button."
            if public else
            "Hit **Start /toke** to use the server's existing opt-in sesh "
            "roles, cooldowns, GIF composer, attendance and voice controls. "
            "The world clock never starts or pings a session on its own."
        ),
        inline=False,
    )
    embed.set_footer(text=(
        "Dank Shield • shared snapshot • no automatic pings • DST-aware"
        if public else "Dank Shield • live on refresh • no automatic pings • DST-aware"
    ))
    return embed


async def _feedback(interaction: discord.Interaction, message: str) -> None:
    payload = {"content": message, "ephemeral": True, "allowed_mentions": discord.AllowedMentions.none()}
    if interaction.response.is_done():
        await interaction.followup.send(**payload)
    else:
        await interaction.response.send_message(**payload)


async def _show_board(interaction: discord.Interaction, *, hour: int, owner_id: int) -> None:
    kwargs = {
        "content": None,
        "embed": build_420_embed(hour=hour),
        "view": Somewhere420View(owner_id=owner_id, hour=hour),
        "allowed_mentions": discord.AllowedMentions.none(),
    }
    if interaction.response.is_done():
        await interaction.edit_original_response(**kwargs)
    elif interaction.message is not None:
        await interaction.response.edit_message(**kwargs)
    else:
        await interaction.response.send_message(**kwargs, ephemeral=True)


async def open_somewhere_420(interaction: discord.Interaction) -> None:
    if interaction.guild is None:
        return await _feedback(interaction, "Open It's 4:20 Somewhere inside a server.")
    await _show_board(interaction, hour=16, owner_id=int(interaction.user.id))


def share_cooldown_remaining(state: Any, *, now_timestamp: int) -> int:
    """Validates persisted state; a malformed future date must not block forever."""
    if not isinstance(state, Mapping):
        return 0
    try:
        last = int(state.get("last_public_at", 0))
    except (TypeError, ValueError, OverflowError):
        return 0  # Invalid legacy state is replaced by the next atomic claim.
    if last <= 0 or last > now_timestamp + 5 * 60:
        return 0  # Clearly corrupt future timestamps must not lock a guild forever.
    age = now_timestamp - last
    return max(0, SHARE_COOLDOWN_SECONDS - age)


async def publish_420_board(interaction: discord.Interaction, *, hour: int) -> None:
    guild, channel = interaction.guild, interaction.channel
    if not interaction_has_manage_guild_authority(interaction):
        return await _feedback(interaction, "Only the server owner or managers may post the public 4:20 board.")
    if guild is None or not isinstance(channel, discord.TextChannel):
        return await _feedback(interaction, "Open the board in the text channel where you want to share it.")

    me = guild.me
    if not isinstance(me, discord.Member):
        return await _feedback(interaction, "Dank Shield could not verify its server permissions.")
    permissions = channel.permissions_for(me)
    if not (permissions.view_channel and permissions.send_messages and permissions.embed_links):
        return await _feedback(interaction, "Dank Shield needs View Channel, Send Messages and Embed Links here.")

    # Defer before database or Discord network calls.
    if not interaction.response.is_done():
        await interaction.response.defer(ephemeral=True, thinking=True)

    from stoney_verify.guild_config import (
        compare_and_swap_guild_config_key,
        get_guild_config,
    )

    try:
        raw = await get_guild_config(guild.id, refresh=True)
        if str(raw.get("source", "")).startswith("unavailable:"):
            return await _feedback(interaction, "Cannot publish while server settings are unavailable.")
        previous = raw.get(SHARE_STATE_KEY)
        now = int(datetime.now(timezone.utc).timestamp())
        remaining = share_cooldown_remaining(previous, now_timestamp=now)
        if remaining:
            return await _feedback(
                interaction,
                f"This server shared the 4:20 board recently. Try again in {remaining // 60 + 1} minutes.",
            )
        next_state = {"last_public_at": now}
        applied, _saved = await compare_and_swap_guild_config_key(
            guild.id,
            SHARE_STATE_KEY,
            expected=previous,
            value=next_state,
            source="somewhere_420_public_share",
        )
        if not applied:
            return await _feedback(interaction, "Another manager just shared or changed this board. Refresh first.")
    except (OSError, RuntimeError, ValueError, TypeError) as exc:
        print(f"[somewhere420] share config unavailable guild={guild.id} error={type(exc).__name__}")
        return await _feedback(interaction, "Unable to safely reserve the public announcement. No message was sent.")

    try:
        post = await channel.send(
            embed=build_420_embed(hour=hour, public=True),
            allowed_mentions=discord.AllowedMentions.none(),
        )
    except (discord.HTTPException, OSError) as exc:
        print(f"[somewhere420] board send failed guild={guild.id} error={type(exc).__name__}")
        # Best-effort conditional rollback; never overwrite a concurrent setting.
        try:
            await compare_and_swap_guild_config_key(
                guild.id,
                SHARE_STATE_KEY,
                expected=next_state,
                value=previous,
                source="somewhere_420_send_rollback",
            )
        except (OSError, RuntimeError, ValueError, TypeError):
            pass
        return await _feedback(interaction, "Discord couldn't post the board. The cooldown may need to clear.")

    await _feedback(
        interaction,
        f"🌍 Shared the 4:20 world board in {channel.mention}: {post.jump_url}. No roles were pinged.",
    )


class Somewhere420View(discord.ui.View):
    def __init__(self, *, owner_id: int, hour: int = 16) -> None:
        super().__init__(timeout=600)
        self.owner_id = int(owner_id)
        self.hour = hour

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if int(interaction.user.id) != self.owner_id:
            await _feedback(interaction, "Open your own 4:20 board from /dank home.")
            return False
        return True

    @discord.ui.button(label="Refresh World Clock", emoji="🔄", style=discord.ButtonStyle.primary, row=0)
    async def refresh(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await _show_board(interaction, hour=self.hour, owner_id=self.owner_id)

    @discord.ui.button(label="Switch AM / PM", emoji="🕓", style=discord.ButtonStyle.secondary, row=0)
    async def toggle(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await _show_board(interaction, hour=4 if self.hour == 16 else 16, owner_id=self.owner_id)

    @discord.ui.button(label="Start /toke", emoji="💨", style=discord.ButtonStyle.success, row=1)
    async def toke(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        from .public_toke import open_toke_command

        # The canonical handler rechecks roles, guild cooldown, channel permissions,
        # and opens the existing media composer. No second session/send owner.
        await open_toke_command(interaction, message="It's 4:20 somewhere! 🌍💨")

    @discord.ui.button(label="Share Here (staff)", emoji="📣", style=discord.ButtonStyle.secondary, row=1)
    async def share(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await publish_420_board(interaction, hour=self.hour)


__all__ = [
    "SHARE_STATE_KEY", "Somewhere420View", "build_420_embed",
    "open_somewhere_420", "publish_420_board", "share_cooldown_remaining",
]
