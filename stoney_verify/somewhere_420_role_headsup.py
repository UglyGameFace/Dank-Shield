from __future__ import annotations

"""One gentle pre-4:20 DM for cached holders of an explicitly configured role.

Guild staff turn on the role program once. Every current holder of that role is
then eligible automatically, while a private, per-guild opt-out remains durable
and takes effect without removing the role. No member fetches and no role pings.
"""

import asyncio
from datetime import datetime, time, timezone
from zoneinfo import ZoneInfo
from typing import Any, Mapping

import discord

from . import somewhere_420_schedules as schedules

ROLE_MINUTES_BEFORE = 5
MAX_RECIPIENTS_PER_GUILD_WAVE = 250
DM_SPACING_SECONDS = 0.7

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


async def record_role_dm(message_id: int, guild_id: int, user_id: int) -> None:
    """Durably bind bot-authored DM to source server without exposing raw IDs."""
    if min(_id(message_id), _id(guild_id), _id(user_id)) <= 0:
        raise ValueError("Invalid DM ownership identifiers")
    response = await schedules._execute(
        "role DM ownership write",
        lambda db: db.table("dank_somewhere_420_role_dm_links").upsert({
            "dm_message_id": str(message_id),
            "guild_id": str(guild_id),
            "user_id": str(user_id),
        }, on_conflict="dm_message_id").execute(),
    )
    row = schedules._row(response)
    if row is None or str(row.get("user_id")) != str(user_id):
        raise schedules.Somewhere420StorageUnavailable("DM ownership write unconfirmed")


async def source_guild_for_role_dm(message_id: int, user_id: int) -> int:
    """Server mapping stays private. Never trust embed text or footer identifiers."""
    if min(_id(message_id), _id(user_id)) <= 0:
        return 0
    response = await schedules._execute(
        "role DM ownership read",
        lambda db: db.table("dank_somewhere_420_role_dm_links")
        .select("guild_id,user_id").eq("dm_message_id", str(message_id))
        .eq("user_id", str(user_id)).limit(1).execute(),
    )
    row = schedules._row(response)
    return _id(row.get("guild_id")) if row else 0


class StonersHeadsUpOptOut(discord.ui.View):
    """Persistent button; source guild comes only from private message ownership."""
    def __init__(self) -> None:
        super().__init__(timeout=None)

    @discord.ui.button(
        label="Stop reminders", emoji="🔕", style=discord.ButtonStyle.secondary,
        custom_id="dank:420:stoners:stop:v1",
    )
    async def stop(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        msg = interaction.message
        bot_user = getattr(interaction.client, "user", None)
        if (msg is None or interaction.guild_id is not None or bot_user is None
                or getattr(msg.author, "id", None) != bot_user.id):
            return await interaction.response.send_message(
                "Only the original private reminder can change these settings.",
                ephemeral=True,
            )
        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            guild_id = await source_guild_for_role_dm(msg.id, interaction.user.id)
            if not guild_id:
                return await interaction.followup.send(
                    "This reminder couldn't be verified. Open My 4:20 Preferences to stop reminders.",
                    ephemeral=True,
                )
            await set_role_optout(guild_id, int(interaction.user.id), opted_out=True)
        except schedules.Somewhere420StorageUnavailable:
            return await interaction.followup.send(
                "Unable to save your opt-out right now. Try again in /dank home.",
                ephemeral=True,
            )
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
    try:
        target_utc = datetime.combine(
            due_date, time(16, 20), tzinfo=ZoneInfo(str(current.get("timezone") or "UTC"))
        ).astimezone(timezone.utc)
    except (ValueError, KeyError):
        return "bad-timezone"
    if datetime.now(timezone.utc) >= target_utc:
        return "past-420"
    from .somewhere_420_schedules import quiet_at
    local_hour = datetime.now(timezone.utc).astimezone(
        ZoneInfo(str(current.get("timezone") or "UTC"))
    ).hour
    if quiet_at(local_hour, int(current.get("quiet_start", 22)), int(current.get("quiet_end", 8))):
        return "quiet-hours"
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
        if datetime.now(timezone.utc) >= target_utc:
            break  # Never send a "prepare" alert once 4:20 has arrived.
        # Re-check role, status, and guild opt-out before each DM.
        if role_id not in {r.id for r in member.roles}:
            continue
        # The atomic claim itself checks the server opt-out; don't double
        # the database request load with a separate per-member read.
        if not await claim_member(guild_id, member.id, due_date.isoformat()):
            continue
        embed = discord.Embed(
            title="🌿 Heads-up: 4:20 is coming up",
            description=(
                f"**4:20 <t:{int(target_utc.timestamp())}:R>** in **{discord.utils.escape_markdown(guild.name)}**. "
                "No pressure. This is your one heads-up today.\n\n"
                "You received this because you have the designated Stoners role. "
                "You can stop future reminders below without losing the role."
            ),
            color=discord.Color.green(),
        )
        embed.set_footer(text="Stoners reminder • Opt out anytime below")
        try:
            sent = await member.send(
                embed=embed, view=StonersHeadsUpOptOut(),
                allowed_mentions=discord.AllowedMentions.none(),
            )
            try:
                await record_role_dm(sent.id, guild_id, member.id)
                delivered += 1
            except (schedules.Somewhere420StorageUnavailable, ValueError):
                # Never leave behind a broken Stop button after ownership
                # persistence fails. Delivery was already claimed once.
                try:
                    await sent.delete()
                except (discord.Forbidden, discord.HTTPException):
                    print("[somewhere420] could not remove untracked reminder DM")
        except (discord.Forbidden, discord.HTTPException):
            pass  # No repeat DM; claim is consumed.
        await asyncio.sleep(DM_SPACING_SECONDS)
    return f"sent:{delivered}"


def install_optout_view(bot: Any) -> None:
    bot.add_view(StonersHeadsUpOptOut())
