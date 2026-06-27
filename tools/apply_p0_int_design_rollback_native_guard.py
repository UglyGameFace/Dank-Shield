from __future__ import annotations

"""Safely apply native interaction guards to Dank Design rollback flows.

Run from repo root after syncing main:
    python tools/apply_p0_int_design_rollback_native_guard.py
"""

from pathlib import Path

TARGET = Path("stoney_verify/commands_ext/public_design_studio.py")


def replace_section(text: str, start_marker: str, end_marker: str, replacement: str, label: str) -> str:
    start = text.find(start_marker)
    if start < 0:
        raise SystemExit(f"Refusing to patch {label}: start marker not found: {start_marker!r}.")
    end = text.find(end_marker, start)
    if end < 0:
        raise SystemExit(f"Refusing to patch {label}: end marker not found: {end_marker!r}.")
    return text[:start] + replacement + text[end:]


def main() -> None:
    path = TARGET
    text = path.read_text(encoding="utf-8")

    if "design.rollback.confirm" in text and "design.rollback.open" in text:
        print("Already applied: Dank Design rollback native guard slice is present.")
        return

    if "async def _guard_design_action(" not in text:
        raise SystemExit(
            "Missing _guard_design_action. Run tools/apply_p0_int_design_exact_format_native_guard.py first."
        )
    if "safe_send_interaction" not in text:
        raise SystemExit("Missing safe_send_interaction import from exact-format migration.")
    for marker in (
        "class DesignDoneView(discord.ui.View):",
        "async def _open_rollback(interaction: discord.Interaction) -> None:",
        "class RollbackConfirmView(discord.ui.View):",
        "async def open_design_studio(interaction: discord.Interaction) -> None:",
    ):
        if marker not in text:
            raise SystemExit(f"Refusing to patch rollback flow: marker not found: {marker!r}.")

    new_rollback_region = '''class DesignDoneView(discord.ui.View):
    def __init__(self, *, can_rollback: bool) -> None:
        super().__init__(timeout=900)
        self.rollback.disabled = not can_rollback

    @discord.ui.button(label="Rollback", emoji="↩️", style=discord.ButtonStyle.danger, custom_id="dank_design:rollback_done", row=0)
    async def rollback(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        async def action() -> None:
            await _open_rollback(interaction)

        await _guard_design_action(interaction, "design.rollback.open_button", action, defer=False)

    @discord.ui.button(label="Back to Studio", emoji="🎨", style=discord.ButtonStyle.secondary, custom_id="dank_design:done_back", row=0)
    async def done_back(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        async def action() -> None:
            if not await _require_design_permission(interaction):
                return
            assert interaction.guild is not None
            options = await _load_design_options(int(interaction.guild.id))
            await interaction.response.edit_message(embed=_home_embed(interaction.guild, options), view=DesignHomeView(options))

        await _guard_design_action(interaction, "design.done.back_to_studio", action, defer=False)


async def _open_rollback(interaction: discord.Interaction) -> None:
    async def action() -> None:
        if not await _require_design_permission(interaction):
            return
        guild = interaction.guild
        assert guild is not None
        latest = await _latest_rollback_snapshot(int(guild.id))
        if not latest:
            await safe_send_interaction(
                interaction,
                content="No rollback snapshot is available for this server.",
                ephemeral=True,
                action_name="design.rollback.no_snapshot",
            )
            return
        items = list(latest.get("items") or [])
        preview = []
        for item in reversed(items[-10:]):
            preview.append(f"↩️ `{item.get('new_name')}` → `{item.get('old_name')}`")
        embed = discord.Embed(title="↩️ Rollback Preview", description="Rollback uses the same safe 2-second rename queue.", color=discord.Color.orange())
        embed.add_field(name="Items", value=str(len(items)), inline=True)
        embed.add_field(name="Preview", value="\n".join(preview)[:1024] or "No items.", inline=False)
        await safe_send_interaction(
            interaction,
            embed=embed,
            view=RollbackConfirmView(),
            ephemeral=True,
            action_name="design.rollback.preview",
        )

    await _guard_design_action(interaction, "design.rollback.open", action, defer=False)


class RollbackConfirmView(discord.ui.View):
    def __init__(self) -> None:
        super().__init__(timeout=900)

    @discord.ui.button(label="Rollback Last Apply", emoji="↩️", style=discord.ButtonStyle.danger, custom_id="dank_design:rollback_confirm", row=0)
    async def rollback_confirm(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        async def action() -> None:
            if not await _require_design_permission(interaction):
                return
            guild = interaction.guild
            assert guild is not None
            lock = _lock_for(int(guild.id))
            if lock.locked():
                await safe_send_interaction(
                    interaction,
                    content="⏳ A design job is already running for this server. Wait for it to finish.",
                    ephemeral=True,
                    action_name="design.rollback.locked",
                )
                return
            latest = await _latest_rollback_snapshot(int(guild.id))
            if not latest:
                await safe_send_interaction(
                    interaction,
                    content="No rollback snapshot found.",
                    ephemeral=True,
                    action_name="design.rollback.confirm.no_snapshot",
                )
                return
            items = list(latest.get("items") or [])
            await interaction.response.defer(ephemeral=True, thinking=False)
            reverted = 0
            failed: list[str] = []
            async with lock:
                for item in reversed(items):
                    channel = guild.get_channel(_safe_int(item.get("channel_id"), 0))
                    if channel is None:
                        failed.append(f"missing `{item.get('new_name')}`")
                        continue
                    current = _safe_str(getattr(channel, "name", ""))
                    new_name = _safe_str(item.get("new_name"))
                    old_name = _safe_str(item.get("old_name"))[: studio.DISCORD_NAME_LIMIT]
                    if current != new_name:
                        failed.append(f"stale `{new_name}` is now `{current}`")
                        continue
                    try:
                        await channel.edit(name=old_name, reason=f"Dank Shield Server Design rollback by {int(interaction.user.id)}")
                        reverted += 1
                        await asyncio.sleep(studio.DEFAULT_DELAY_SECONDS)
                    except Exception as exc:
                        failed.append(f"`{current}`: {type(exc).__name__}")
            await _pop_latest_rollback_snapshot(int(guild.id))
            embed = discord.Embed(title="↩️ Rollback Complete", description=f"Restored **{reverted}** item(s). Failed **{len(failed)}**.", color=discord.Color.green() if not failed else discord.Color.orange())
            if failed:
                embed.add_field(name="Skipped / Failed", value="\n".join(failed[:10])[:1024], inline=False)
            await interaction.edit_original_response(embed=embed, view=None)

        await _guard_design_action(interaction, "design.rollback.confirm", action, defer=False)


'''
    text = replace_section(
        text,
        "class DesignDoneView(discord.ui.View):",
        "async def open_design_studio(interaction: discord.Interaction) -> None:",
        new_rollback_region,
        "rollback region",
    )

    path.write_text(text, encoding="utf-8")
    print("Applied: Dank Design rollback native guard slice.")


if __name__ == "__main__":
    main()
