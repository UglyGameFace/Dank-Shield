from __future__ import annotations

"""Run setup permission repair through the shared operation queue.

This keeps one guild's permission repair from blocking unrelated guild work and
keeps duplicate taps in the same guild from racing Discord overwrites.
"""

import discord

_DONE = False


async def _reply_now(interaction: discord.Interaction, content: str) -> None:
    try:
        if interaction.response.is_done():
            await interaction.followup.send(content, ephemeral=True, allowed_mentions=discord.AllowedMentions.none())
        else:
            await interaction.response.send_message(content, ephemeral=True, allowed_mentions=discord.AllowedMentions.none())
    except Exception:
        pass


def apply() -> bool:
    global _DONE
    if _DONE:
        return True
    try:
        from stoney_verify.operation_queue import run_interaction_exclusive
        from stoney_verify.startup_guards import setup_permission_repair_guard as repair
        from stoney_verify.commands_ext import public_setup_solid as solid

        async def queued_apply(interaction: discord.Interaction) -> None:
            if not await solid._require_setup_permission(interaction):
                return
            guild = interaction.guild
            if guild is None:
                return await _reply_now(interaction, "❌ This must be used inside a server.")
            try:
                if not interaction.response.is_done():
                    await interaction.response.defer(ephemeral=True, thinking=True)
            except Exception:
                pass

            async def job():
                return await repair._preview_or_apply(guild, apply=True)

            result = await run_interaction_exclusive(
                interaction=interaction,
                operation_type="setup_permission_repair",
                action_label="Setup permission repair",
                factory=job,
                fingerprint={"guild_id": int(guild.id), "scope": "saved_setup_permissions"},
                risk_level="moderate",
                concurrency_class="guild_config_write",
                concurrency_key="setup_permission_repair",
                timeout_seconds=180.0,
            )
            if result is None:
                return
            try:
                await interaction.followup.send(
                    embed=repair._result_embed(result),
                    view=repair.PermissionRepairDoneView(),
                    ephemeral=True,
                    allowed_mentions=discord.AllowedMentions.none(),
                )
            except Exception:
                pass

        repair._apply_permission_repair = queued_apply
        _DONE = True
        print("🧱 setup_permission_repair_queue_guard active; setup permission repair uses operation_queue")
        return True
    except Exception as exc:
        try:
            print(f"⚠️ setup_permission_repair_queue_guard failed: {exc!r}")
        except Exception:
            pass
        return False


apply()

__all__ = ["apply"]
