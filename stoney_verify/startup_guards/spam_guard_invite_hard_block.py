from __future__ import annotations

"""Legacy Spam Guard invite hard-block compatibility shim.

The old version of this module deleted Discord invites directly and could make
Spam Guard look like it was deleting posts even when Invite Shield was OFF.  The
only live runtime listener now lives in ``discord_invite_blocker_runtime_guard``
and every delete must be approved by ``invite_policy_engine``.
"""

import discord

try:
    from stoney_verify.globals import bot
except Exception:  # pragma: no cover
    bot = None  # type: ignore

from stoney_verify import invite_policy_engine as policy

_INSTALLED = False


def _log(message: str) -> None:
    try:
        print(f"🛡️ spam_guard_invite_hard_block {message}")
    except Exception:
        pass


def _extract_codes_from_message(message: discord.Message) -> list[str]:
    return policy.extract_invite_codes_from_message(message)


async def _modlog(guild: discord.Guild, message: discord.Message, codes: list[str], reason: str) -> None:
    decision = policy.InviteDecision(
        action="log_only",
        feature_owner="Invite Policy",
        rule_id="legacy_modlog_bridge",
        reason=str(reason or "legacy invite modlog bridge"),
        fix_hint="Use Protection Center → Invite Link Blocking to change behavior.",
        guild_id=int(getattr(guild, "id", 0) or 0),
        config_guild_id=int(getattr(guild, "id", 0) or 0),
        channel_id=int(getattr(getattr(message, "channel", None), "id", 0) or 0),
        author_id=int(getattr(getattr(message, "author", None), "id", 0) or 0),
        source="legacy-hard-block-bridge",
        codes=list(codes or []),
        blocked_codes=list(codes or []),
    )
    await policy.send_invite_decision_modlog(message, decision)


async def _hard_block_invite_message(message: discord.Message) -> None:
    """Compatibility entrypoint used by older imports.

    It delegates to the central runtime.  Spam Guard-only mode cannot delete a
    single invite link through this shim.
    """

    try:
        from stoney_verify.startup_guards import discord_invite_blocker_runtime_guard as runtime

        await runtime._enforce_message(message, source="legacy-hard-block-bridge")
    except Exception as exc:
        _log(f"bridge handler failed: {type(exc).__name__}: {exc}")


async def _hard_block_invite_message_edit(before: discord.Message, after: discord.Message) -> None:
    _ = before
    await _hard_block_invite_message(after)


async def _invite_shield_doctor(interaction: discord.Interaction, scan_limit: int = 10) -> None:
    try:
        if interaction.guild is None or not isinstance(interaction.channel, discord.TextChannel):
            return await interaction.response.send_message("❌ Run this in a server text channel.", ephemeral=True)

        guild = interaction.guild
        channel = interaction.channel
        checked = 0
        matched = 0
        last_summary = "No invite decisions found yet."

        async for msg in channel.history(limit=max(1, min(int(scan_limit or 10), 25))):
            checked += 1
            codes = policy.extract_invite_codes_from_message(msg)
            if not codes:
                continue
            matched += 1
            decision = await policy.decide_invite_message(msg, source="doctor", refresh_policy=True)
            last_summary = policy.decision_summary(decision)
            break

        embed = discord.Embed(
            title="🛡️ Invite Policy Doctor",
            description=f"Channel: {channel.mention}",
            color=discord.Color.blurple(),
        )
        embed.add_field(name="Scan result", value=f"Checked `{checked}` recent messages. Invite matches `{matched}`.", inline=False)
        embed.add_field(name="Latest decision", value=last_summary[:1024], inline=False)
        await interaction.response.send_message(embed=embed, ephemeral=True, allowed_mentions=discord.AllowedMentions.none())
    except Exception as exc:
        try:
            await interaction.response.send_message(f"❌ Invite doctor failed safely: `{type(exc).__name__}`", ephemeral=True)
        except Exception:
            pass


def install() -> bool:
    global _INSTALLED
    if _INSTALLED:
        return True
    _INSTALLED = True
    _log("active; legacy hard-block delete path retired in favor of central invite_policy_engine")
    return True


install()

__all__ = ["install", "_hard_block_invite_message", "_hard_block_invite_message_edit", "_invite_shield_doctor", "_modlog"]
