from __future__ import annotations

"""One gentle pre-4:20 DM for cached holders of an explicitly configured role.

Guild staff turn on the role program once. Every current holder of that role is
then eligible automatically, while a private, per-guild opt-out remains durable
and takes effect without removing the role. No member fetches and no role pings.
"""

import asyncio
import re
from datetime import datetime, timezone
from typing import Any, Mapping

import discord

from . import somewhere_420_schedules as schedules

ROLE_MINUTES_BEFORE = 5
MAX_RECIPIENTS_PER_GUILD_WAVE = 80
DM_SPACING_SECONDS = 0.35
FOOTER_PREFIX = "Dank Shield 4:20 heads-up | guild "

def _id(value: Any) -> int:
    try:
        result = int(value)
        return result if result > 0 else 0
    except (TypeError, ValueError, OverflowError):
        return 0


async def get_role_optout(guild_id: int, user_id: int) -> bool:
    response = await schedules._execute(
        "role opt-out read",
        lambda db: db.table("dank_somewhere_420_role_optouts").select("opted_out")
        .eq("guild_id", str(guild_id)).eq("user_id", str(user_id)).limit(1).execute(),
    )
    row = schedules._row(response)
    return bool(row and row.get("opted_out"))


async def set_role_optout(guild_id: int, user_id: int, *, opted_out: bool) -> bool:
    if _id(guild_id) <= 0 or _id(user_id) <= 0:
        raise ValueError("Missing server or user")
    response = await schedules._execute(
        "role opt-out write",
        lambda db: db.table("dank_somewhere_420_role_optouts").upsert({
            "guild_id": str(guild_id),
            "user_id": str(user_id),
            "opted_out": bool(opted_out),
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }, on_conflict="guild_id,user_id").execute(),
    )
    row = schedules._row(response)
    if row is None:
        raise schedules.Somewhere420StorageUnavailable("No opt-out confirmation")
    return bool(row.get("opted_out"))


async def configure_role_heads_up(
    guild_id: int, *, role_id: int, enabled: bool, zone_name: str,
) -> dict[str, Any]:
    zone = schedules.valid_timezone(zone_name)
    if _id(guild_id) <= 0 or _id(role_id) <= 0:
        raise ValueError("Set a valid server and Stoners role first.")
    existing = await schedules.get_schedule("guild", guild_id)
    if existing is None:
        await schedules.save_schedule("guild", guild_id, zone_name=zone, enabled=False)
    new = {
        "role_reminder_role_id": str(role_id),
        "role_reminder_enabled": bool(enabled),
        "role_reminder_next_at": schedules.next_daily_fire(
            zone, reminder_minutes=ROLE_MINUTES_BEFORE
        ).isoformat(),
        "timezone": zone,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    result = await schedules._execute(
        "role program write",
        lambda db: db.table(schedules.TABLE).update(new)
        .eq("id", f"g:{guild_id}").execute(),
    )
    row = schedules._row(result)
    if row is None:
        raise schedules.Somewhere420StorageUnavailable("No role-setting confirmation")
    return row


async def claim_role_waves(*, limit: int = 8) -> list[dict[str, Any]]:
    count = max(1, min(int(limit), 16))
    response = await schedules._execute(
        "role wave claim",
        lambda db: db.rpc("dank_420_claim_role_waves", {"p_limit": count}).execute(),
    )
    data = getattr(response, "data", None)
    if not isinstance(data, list):
        raise schedules.Somewhere420StorageUnavailable("Role wave RPC returned no list")
    return [dict(row) for row in data if isinstance(row, Mapping)]


async def claim_member(guild_id: int, user_id: int, local_day: str) -> bool:
    response = await schedules._execute(
        "role member claim",
        lambda db: db.rpc("dank_420_claim_role_member", {
            "p_guild": str(guild_id), "p_user": str(user_id),
            "p_local_date": str(local_day),
        }).execute(),
    )
    data = getattr(response, "data", None)
    return data is True


class StonersHeadsUpOptOut(discord.ui.View):
    """Persistent fixed button; guild is read from our own DM embed footer."""
    def __init__(self) -> None:
        super().__init__(timeout=None)

    @discord.ui.button(
        label="Stop reminders", emoji="🔕", style=discord.ButtonStyle.secondary,
        custom_id="dank:420:stoners:stop:v1",
    )
    async def stop(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        footer = ""
        msg = interaction.message
        if msg and msg.embeds:
            footer = str(getattr(msg.embeds[0].footer, "text", "") or "")
        match = re.fullmatch(r"Stoners reminder \| guild ([0-9]{5,22})", footer)
        if not match:
            return await interaction.response.send_message(
                "This reminder is too old to identify its server. Open /dank home to stop reminders.",
                ephemeral=True,
            )
        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            await set_role_optout(int(match.group(1)), int(interaction.user.id), opted_out=True)
        except schedules.Somewhere420StorageUnavailable:
            return await interaction.followup.send("Unable to save your opt-out right now. Try again in /dank home.", ephemeral=True)
        await interaction.followup.send(
            "🔕 Stoners heads-up reminders are off for this server. You keep your Stoners role. "
            "You can re-enable them in My 4:20 Preferences.",
            ephemeral=True, allowed_mentions=discord.AllowedMentions.none(),
        )


def _role_members(guild: discord.Guild, role_id: int) -> list[discord.Member]:
    role = guild.get_role(role_id)
    if not isinstance(role, discord.Role):
        return []
    # Only cached Discord members are considered. Do NOT mass-fetch all guild
    # members; cache completeness is a rollout gate for larger guilds.
    members = [
        m for m in role.members
        if isinstance(m, discord.Member) and not m.bot and not m.pending
    ]
    members.sort(key=lambda m: m.id)
    return members


async def deliver_role_wave(bot: Any, claimed: Mapping[str, Any]) -> str:
    guild_id = _id(claimed.get("guild_id"))
    if not guild_id:
        return "bad-guild"
    guild = bot.get_guild(guild_id)
    if guild is None:
        return "guild-unavailable"
    current = await schedules.get_schedule("guild", guild_id)
    if not current or not current.get("role_reminder_enabled"):
        return "disabled"
    # Staff can stop or remap the whole reminder between claim and send.
    if current.get("updated_at") != claimed.get("updated_at"):
        return "changed"
    role_id = _id(current.get("role_reminder_role_id"))
    role = guild.get_role(role_id) if role_id else None
    if not isinstance(role, discord.Role):
        return "missing-role"
    due_text = str(claimed.get("role_reminder_last_date") or "")
    try:
        due_date = datetime.strptime(due_text, "%Y-%m-%d").date()
    except ValueError:
        return "invalid-day"
    eligible = _role_members(guild, role_id)
    if not eligible:
        return "no-cached-stoners"
    if len(eligible) > MAX_RECIPIENTS_PER_GUILD_WAVE:
        print(
            f"[somewhere420] heads-up guild={guild_id} eligible={len(eligible)} "
            f"bounded_to={MAX_RECIPIENTS_PER_GUILD_WAVE}"
        )
    delivered = 0
    for member in eligible[:MAX_RECIPIENTS_PER_GUILD_WAVE]:
        # Re-check role, status, and guild opt-out before each DM.
        if role_id not in {r.id for r in member.roles}:
            continue
        if await get_role_optout(guild_id, member.id):
            continue
        if not await claim_member(guild_id, member.id, due_date.isoformat()):
            continue
        embed = discord.Embed(
            title="🌿 Just a heads-up: 4:20 in 5 minutes",
            description=(
                f"Time to get ready if you're joining the sesh in **{discord.utils.escape_markdown(guild.name)}**. "
                "No pressure. This is your one heads-up today.\n\n"
                "You received this because you have the designated Stoners role. "
                "You can stop future reminders below without losing the role."
            ),
            color=discord.Color.green(),
        )
        embed.set_footer(text=f"Stoners reminder | guild {guild_id}")
        try:
            await member.send(embed=embed, view=StonersHeadsUpOptOut(),
                              allowed_mentions=discord.AllowedMentions.none())
            delivered += 1
        except (discord.Forbidden, discord.HTTPException):
            pass  # No repeat DM; claim is consumed.
        await asyncio.sleep(DM_SPACING_SECONDS)
    return f"sent:{delivered}"


def install_optout_view(bot: Any) -> None:
    bot.add_view(StonersHeadsUpOptOut())
