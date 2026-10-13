"""Voluntary Voice Safety decline, ten-second exit grace, and safe cancellation.

Only an explicitly clicked Decline & Exit control can begin removal. Revocation
precedes role changes. The original member can accept again within ten seconds.
"""
from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field

import discord

DECLINE_CUSTOM_ID = "dank:basic_verify:decline_exit:v1"
GRACE_SECONDS = 10
_PENDING: dict[tuple[int, int], "PendingExit"] = {}


@dataclass(slots=True)
class PendingExit:
    guild: discord.Guild
    user_id: int
    original_role_ids: tuple[int, ...]
    expires: float
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    task: asyncio.Task[None] | None = None
    completed: bool = False

    @property
    def key(self) -> tuple[int, int]:
        return self.guild.id, self.user_id


def exit_pending(guild_id: int, member_id: int) -> bool:
    return (int(guild_id), int(member_id)) in _PENDING


def _preflight(member: discord.Member, unverified: discord.Role) -> str:
    guild = member.guild
    if int(member.id) == int(guild.owner_id):
        return "Discord does not allow a bot to kick the server owner."
    me = guild.me
    if me is None or not (me.guild_permissions.manage_roles and me.guild_permissions.kick_members):
        return "Dank Shield needs Manage Roles and Kick Members."
    if unverified.managed or unverified >= me.top_role:
        return "Dank Shield must be above Unverified in the role hierarchy."
    if any(not role.is_default() and (role.managed or role >= me.top_role) for role in member.roles):
        return "Discord prevents Dank Shield from removing one or more protected/higher roles."
    return ""


async def _kick_later(entry: PendingExit) -> None:
    try:
        await asyncio.sleep(max(0.0, entry.expires - time.monotonic()))
        async with entry.lock:
            if entry.completed or _PENDING.get(entry.key) is not entry:
                return
            try:
                member = await entry.guild.fetch_member(entry.user_id)
                await member.kick(
                    reason="Member declined Voice Safety policy; ten-second grace expired"
                )
            except discord.NotFound:
                pass  # Already left the server.
            except (discord.Forbidden, discord.HTTPException) as exc:
                # Member remains stripped of access roles and unverified.
                print(f"⚠️ voice_decline kick_failed guild={entry.guild.id} error={type(exc).__name__}")
            finally:
                entry.completed = True
                _PENDING.pop(entry.key, None)
    except asyncio.CancelledError:
        return


class ExitGraceView(discord.ui.View):
    def __init__(self, entry: PendingExit):
        super().__init__(timeout=GRACE_SECONDS + 2)
        self.entry = entry

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if (interaction.guild is not None
                and int(interaction.guild.id) == self.entry.guild.id
                and int(interaction.user.id) == self.entry.user_id):
            return True
        await interaction.response.send_message(
            "Only the member who declined can use this private action.", ephemeral=True
        )
        return False

    @discord.ui.button(label="Agree & Stay", emoji="✅", style=discord.ButtonStyle.success)
    async def agree(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        entry = self.entry
        if (entry.completed or _PENDING.get(entry.key) is not entry
                or time.monotonic() >= entry.expires):
            return await interaction.response.send_message(
                "The ten-second grace window is over. No roles were restored.", ephemeral=True
            )
        await interaction.response.defer(thinking=True, ephemeral=True)
        async with entry.lock:
            if entry.completed or _PENDING.get(entry.key) is not entry:
                return await interaction.followup.send("Exit is no longer pending.", ephemeral=True)
            from stoney_verify.community_voice_caption_runtime import ensure_community_voice_caption_manager
            from stoney_verify.voice_safety_consent import read_voice_safety_agreement
            manager = ensure_community_voice_caption_manager(interaction.client)
            try:
                # A visible Agree & Stay click is a new explicit agreement
                # to the policy displayed on the verification/notice panel.
                await manager.set_voice_safety_consent(
                    entry.guild.id, entry.user_id, True, source="basic_verify_v1"
                )
                if not await read_voice_safety_agreement(entry.guild.id, entry.user_id):
                    raise RuntimeError("Agreement write not verified")
                member = await entry.guild.fetch_member(entry.user_id)
                me = entry.guild.me
                if me is None:
                    raise RuntimeError("Bot member missing")
                restored = []
                for role_id in entry.original_role_ids:
                    role = entry.guild.get_role(role_id)
                    if role is None or role.managed or role >= me.top_role:
                        raise RuntimeError("Original role is no longer safely restorable")
                    restored.append(role)
                await member.edit(
                    roles=restored,
                    reason="Voice Safety policy explicitly accepted within exit grace"
                )
            except Exception:
                # Never leave new agreement in place when restoring access failed.
                try:
                    await manager.set_voice_safety_consent(entry.guild.id, entry.user_id, False)
                except Exception:
                    pass
                return await interaction.followup.send(
                    "❌ Could not restore your roles safely. Exit is still pending. Staff can check permissions.",
                    ephemeral=True,
                )
            entry.completed = True
            _PENDING.pop(entry.key, None)
            self.stop()
            await interaction.followup.send(
                "✅ You agreed to the disclosed Voice Safety policy. Your previous manageable roles were restored. Exit canceled.",
                ephemeral=True,
            )


async def decline_and_exit(interaction: discord.Interaction, *, from_verify: bool = False) -> None:
    await interaction.response.defer(thinking=True, ephemeral=True)
    guild = interaction.guild
    user = interaction.user
    if not isinstance(guild, discord.Guild) or not isinstance(user, discord.Member) or user.bot:
        return await interaction.followup.send("Only human server members can decline.", ephemeral=True)
    if from_verify:
        from stoney_verify.voice_safety_consent import verification_message_discloses_voice_safety
        if not verification_message_discloses_voice_safety(interaction.message):
            return await interaction.followup.send(
                "This panel lacks the current Voice Safety disclosure. Ask staff to refresh it.",
                ephemeral=True,
            )

    from stoney_verify.community_voice_caption_runtime import ensure_community_voice_caption_manager
    manager = ensure_community_voice_caption_manager(interaction.client)
    # Consent withdrawal is independent of permissions to remove the member.
    try:
        await manager.set_voice_safety_consent(guild.id, user.id, False)
    except Exception:
        return await interaction.followup.send(
            "❌ Agreement withdrawal could not be confirmed. Audio admission was blocked. "
            "Use Discord's Leave Server control; staff must investigate storage.",
            ephemeral=True,
        )

    from stoney_verify.guild_config import get_guild_config
    from stoney_verify.setup_engine.loader import snapshot_from_config
    try:
        cfg = await get_guild_config(guild.id, refresh=True)
        unverified_id = snapshot_from_config(guild.id, cfg).unverified_role_id
        unverified = guild.get_role(int(unverified_id or 0))
    except Exception:
        unverified = None
    if not isinstance(unverified, discord.Role):
        return await interaction.followup.send(
            "Agreement withdrawn. Unverified isn't configured, so the exit flow cannot safely restrict roles. "
            "Use Discord's Leave Server control.", ephemeral=True
        )

    from stoney_verify.verification_new.basic_verify import _lock_for
    key = (guild.id, user.id)
    async with _lock_for(*key):
        if key in _PENDING:
            return await interaction.followup.send(
                "Your ten-second exit countdown is already running.", ephemeral=True
            )
        try:
            member = await guild.fetch_member(user.id)
        except (discord.NotFound, discord.Forbidden, discord.HTTPException):
            return await interaction.followup.send(
                "Agreement withdrawn, but your Discord membership could not be refreshed. "
                "Use the Leave Server control.", ephemeral=True
            )
        blocker = _preflight(member, unverified)
        if blocker:
            return await interaction.followup.send(
                "Agreement withdrawn. " + blocker + " Use Discord's Leave Server control.",
                ephemeral=True,
            )
        original_role_ids = tuple(role.id for role in member.roles if not role.is_default())
        try:
            # Atomic replacement: strip access and grant ONLY Unverified.
            await member.edit(
                roles=[unverified],
                reason="Member declined voice policy: ten-second exit grace",
            )
        except (discord.Forbidden, discord.HTTPException):
            return await interaction.followup.send(
                "Agreement withdrawn, but Discord denied role restriction. Use Leave Server; staff should check bot permissions.",
                ephemeral=True,
            )
        entry = PendingExit(
            guild=guild, user_id=user.id, original_role_ids=original_role_ids,
            expires=time.monotonic() + GRACE_SECONDS,
        )
        _PENDING[key] = entry

    try:
        await interaction.followup.send(
            "🚪 **Voice Safety declined.** Your access roles have been removed and **Unverified** assigned. "
            "You'll be kicked after **10 seconds** unless you press **Agree & Stay**. "
            "That button explicitly accepts the disclosed policy and restores your previous manageable roles. "
            "Voice monitoring does not start from this action.",
            ephemeral=True, view=ExitGraceView(entry),
        )
    finally:
        # Still schedule exit when Discord refuses to deliver the followup.
        entry.task = asyncio.create_task(_kick_later(entry))
